"""Vibe coding agent: list by glob and search by regex (docs/VIBE-AGENT-SEARCH-DESIGN.md, P-066).

Two helpers for the list_files and search tools of codec_vibe_agent:

- glob matching, in pure Python: one step per path segment, each name compared with fnmatch.fnmatchcase,
  whose translation has no catastrophic backtracking;
- the regex search, which runs in a child Python process and never in the dashboard. Python's re cannot be
  stopped once it runs, so a pattern such as (a+)+$ must not run here.

The child is not a command. Its code (CHILD_CODE) and its argv are fixed, the model's pattern and the file text
travel on stdin, its environment is empty, its CPU time (and, where the system allows it, its memory) is limited,
and its whole process group is killed after REGEX_SECONDS. No shell and no sandbox profile: it only runs json and
re, so it needs no owner click. Never put model text in its argv.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import resource
import signal
import subprocess
import sys
from dataclasses import dataclass, field

MAX_GLOB_CHARS = 200
_GLOB_CHARS = frozenset("*?[")
REGEX_SECONDS = 2.0                 # wall clock; then the child's process group is killed
REGEX_CPU = (2, 3)                  # RLIMIT_CPU soft and hard, in seconds
REGEX_MEMORY = 512 * 1024 ** 2      # RLIMIT_AS; macOS refuses any value below what a process reserves at start
REGEX_MAX_BYTES = 8 * 1024 ** 2     # text sent to the child for one search
EXTRA_ARGV: tuple = ()              # empty in the product; tests add the vibe-run-test marker
SLOW = (f"The regex search took longer than {int(REGEX_SECONDS)} seconds and was stopped. Use a simpler pattern "
        "(nested repeats such as (a+)+ are slow), or search plain text.")

# The whole program the child runs. It reads one JSON request on stdin and writes one JSON answer on stdout.
CHILD_CODE = r'''
import json, re, sys
def answer(d):
    sys.stdout.write(json.dumps(d, ensure_ascii=True))
    sys.stdout.flush()
req = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
try:
    rx = re.compile(req["pattern"], re.IGNORECASE)
except (re.error, OverflowError, RecursionError, ValueError) as e:
    answer({"ok": False, "error": str(e)[:200]})
    raise SystemExit(0)
mode, cap = req["mode"], req["cap"]
hits, counts, more = [], [], False
for path, text in req["files"]:
    n = 0
    for no, line in enumerate(text.split("\n"), 1):
        if rx.search(line) is None:
            continue
        n += 1
        if mode == "files":
            break
        if mode == "lines":
            if len(hits) >= cap:
                more = True
                break
            hits.append([path, no, line.strip()[:200]])
    if n:
        counts.append([path, n])
    if more:
        break
answer({"ok": True, "hits": hits, "counts": counts, "more": more})
'''


class GlobError(ValueError):
    """A glob the agent may not use. `unsafe` when it tries to leave the project folder."""

    def __init__(self, message: str, unsafe: bool = False):
        super().__init__(message)
        self.unsafe = unsafe


class RegexFailed(Exception):
    """The regex search gave no result. The message is for the model."""


@dataclass
class Found:
    hits: list = field(default_factory=list)      # (path, line number, line) in lines mode
    counts: list = field(default_factory=list)    # (path, matching lines) for every file with a match
    more: bool = False                            # lines mode: more than `cap` matching lines


# ── globs ───────────────────────────────────────────────────────────────────

def is_glob(text: str) -> bool:
    return any(c in _GLOB_CHARS for c in (text or ""))


def check_glob(text) -> str:
    """The glob as it will be used (no ./, no empty segment). Raises GlobError."""
    g = (text or "").strip() if isinstance(text, str) else ""
    if not g or len(g) > MAX_GLOB_CHARS:
        raise GlobError(f"A glob must be 1 to {MAX_GLOB_CHARS} characters, for example src/**/*.js.")
    if "\\" in g or any(ord(c) < 32 for c in g):
        raise GlobError("A glob may not hold a backslash or a control character.")
    if g.startswith(("/", "~")):
        raise GlobError("A glob is relative to the project folder, for example src/**/*.js.", unsafe=True)
    segs = [s for s in g.split("/") if s not in ("", ".")]
    if ".." in segs:
        raise GlobError("A glob may not leave the project folder.", unsafe=True)
    if not segs:
        raise GlobError("A glob must name some files, for example src/**/*.js.")
    return "/".join(segs)


def matcher(text):
    """A function path -> bool for one glob. Case does not matter; ** is any number of folders, none included;
    a glob without / matches the file name in any folder. Raises GlobError."""
    segs: list[str] = []
    for s in check_glob(text).lower().split("/"):
        if not (s == "**" and segs and segs[-1] == "**"):
            segs.append(s)
    if len(segs) == 1 and segs[0] != "**":
        segs.insert(0, "**")
    try:
        for s in segs:
            fnmatch.fnmatchcase("", s)              # compile every segment now, not in the middle of a listing
    except re.error:
        raise GlobError("I could not read that glob. Use *, ** and ? only, for example src/**/*.js.")

    def match(path: str) -> bool:
        parts = path.lower().split("/")
        reach = {0}                                 # how many path segments the glob so far can cover
        for s in segs:
            if s == "**":
                reach = set(range(min(reach), len(parts) + 1))
            else:
                reach = {j + 1 for j in reach if j < len(parts) and fnmatch.fnmatchcase(parts[j], s)}
            if not reach:
                return False
        return len(parts) in reach
    return match


# ── the regex child ─────────────────────────────────────────────────────────

def argv() -> list:
    """The child's argv: fixed, with no model text in it."""
    return [sys.executable, "-I", "-c", CHILD_CODE, *EXTRA_ARGV]


def _limits() -> None:          # runs in the child between fork and exec: no imports, no locks
    for which, value in ((resource.RLIMIT_CPU, REGEX_CPU), (resource.RLIMIT_AS, (REGEX_MEMORY, REGEX_MEMORY)),
                         (resource.RLIMIT_CORE, (0, 0))):
        try:
            resource.setrlimit(which, value)
        except (ValueError, OSError):           # macOS refuses a low RLIMIT_AS; the input cap and the kill remain
            pass


def _kill_group(proc: subprocess.Popen) -> None:
    """Kill the child's whole process group, then reap it. Called before the child is reaped, so the group is ours."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.communicate(timeout=2)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        pass


def run_regex(pattern: str, mode: str, files: list, cap: int) -> Found:
    """Search `files` ([(path, text)]) for the regex `pattern` in a child process. Raises RegexFailed."""
    if not sys.executable:
        raise RegexFailed("The regex search cannot start here. Search plain text instead.")
    data = json.dumps({"pattern": pattern, "mode": mode, "cap": int(cap), "files": [[p, t] for p, t in files]},
                      ensure_ascii=False).encode("utf-8", "replace")
    try:
        proc = subprocess.Popen(argv(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                env={}, cwd="/", close_fds=True, start_new_session=True, preexec_fn=_limits)
    except (OSError, subprocess.SubprocessError):
        raise RegexFailed("The regex search could not start. Search plain text instead.")
    try:
        out, _ = proc.communicate(data, timeout=REGEX_SECONDS)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        raise RegexFailed(SLOW)
    except BaseException:
        _kill_group(proc)
        raise
    if proc.returncode in (-signal.SIGXCPU, -signal.SIGKILL):      # the CPU limit stopped it
        raise RegexFailed(SLOW)
    try:
        res = json.loads(out.decode("utf-8", "replace")) if proc.returncode == 0 else None
    except ValueError:
        res = None
    if not isinstance(res, dict):
        raise RegexFailed("The regex search failed. Use a simpler pattern, or search plain text.")
    if not res.get("ok"):
        raise RegexFailed(f"That is not a valid regular expression: {str(res.get('error') or '')[:200]}. "
                          "Fix it, or search plain text without --regex.")
    return Found([(str(p), int(n), str(t)) for p, n, t in res.get("hits", [])],
                 [(str(p), int(n)) for p, n in res.get("counts", [])], bool(res.get("more")))
