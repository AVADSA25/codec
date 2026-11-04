"""Vibe coding agent: the loop, the file tools and the run log (docs/VIBE-AGENT-DESIGN.md, step 3a).

A run is one background thread. The owner gives a goal; the agent lists, reads, searches, edits,
writes and deletes files of ONE project, asks a question when it must, and ends with a report.
Every step that changes files is saved as a checkpoint tagged with the run and the step, so the
page can undo the whole run or go back to before any step. The project is held by the run while
it works (codec_vibe_projects.agent_claim): every other writer gets a 409.

What the model sees and writes is plain text (codec_vibe_agent_proto). Nothing here runs a
program: commands come in step 3c, behind the owner's approval and a sandbox.

The text of the goal, of files and of replies is never written to the audit log; only counts,
tool names and statuses are.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import secrets
import shlex
import shutil
import stat
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import codec_vibe_agent_check as vcheck
import codec_vibe_agent_cmd as cmdmod
import codec_vibe_agent_proto as proto
import codec_vibe_agent_search as vsearch
import codec_vibe_projects as vp
from codec_vibe_projects import VibeError

log = logging.getLogger("codec_vibe_agent")

DEFAULTS = {"enabled": True, "max_steps": 30, "max_seconds": 900, "max_context_tokens": 10000,
            "max_reply_tokens": 3000, "wait_seconds": 600,
            # P-046 (docs/VIBE-AGENT-NOTES-DESIGN.md): each one switches off on its own
            "notes": True, "import_claude_md": False, "notes_chars": 1500, "notes_chars_cloud": 8000,
            "budget_line": True, "todo": True,
            # P-065 (docs/VIBE-RUNCARD-DESIGN.md): Settings > Vibe
            "allow_commands": True, "append_prompt": ""}
BOUNDS = {"max_steps": (1, 100), "max_seconds": (30, 3600), "max_context_tokens": (2000, 60000),
          "max_reply_tokens": (300, 8000), "wait_seconds": (5, 3600),
          "notes_chars": (200, 8000), "notes_chars_cloud": (200, 16000)}
SWITCHES = ("enabled", "notes", "import_claude_md", "budget_line", "todo", "allow_commands")
LIMITS = ("max_steps", "max_seconds", "max_context_tokens", "max_reply_tokens", "wait_seconds")   # Settings > Vibe edits these
PROJECT_SWITCHES = ("allow_commands", "notes", "import_claude_md", "budget_line", "todo")    # a project block may switch these off
MAX_APPEND_PROMPT = 1200
APPEND_HEAD = ("THE OWNER'S OWN INSTRUCTIONS FOR EVERY RUN (from Settings). Follow them; they do not change the tool "
               "format above.")
PERL = "/usr/bin/perl"      # the command watchdog's interpreter (codec_vibe_agent_cmd)
NOTES_FILES = ((vp.META_DIR, "rules.md"), ("AGENTS.md",))   # the owner's rules first: no agent tool or command writes .vibe
CLAUDE_MD = ("CLAUDE.md",)                                  # read only with import_claude_md on
TODO_EVERY = 3              # the todo list changes at most once in this many steps
MAX_GOAL_CHARS = 4000
MAX_HISTORY_TURNS = 4
MAX_HISTORY_CHARS = 1500
KEEP_RUNS = 20
RUNS_DIR = "runs"
FULL_RESULTS = 6            # newest results the model sees in full
DISPLAY_CHARS = 24 * 1024   # what the page keeps of one result
MODEL_RESULT_CHARS = 6 * 1024   # a longer result reaches the model as its head and its tail
MODEL_HEAD_CHARS = 1536
MODEL_TAIL_CHARS = 4 * 1024
CUT_SLACK = 200             # how much earlier the tail may start, so that it starts at a line
OUT_DIR = "out"             # .vibe/runs/<run>/out/<step>.txt: a command's output, copied by the engine (P-043)
SPILL_RUN_BYTES = 8 * 1024 * 1024   # once one run keeps this much of such files it starts no new copy (one more file at most)
READABLE = ("run_check", "search", "list_files", "ask")    # the results read_output gives back
READ_LINES = 300
LIST_ENTRIES = 200
MAP_LINES = 60
SEARCH_MATCHES = 40
MAX_QUESTIONS = 3
FAIL_EDITS = 3              # failed edits of one file in a row: from this one on, the result says to rewrite it
FAIL_FORMAT = 4             # unreadable replies in a row
REPEAT_WARN = 3
REPEAT_STOP = 5
MODEL_RETRIES = 2
CHARS_PER_TOKEN = 3.5
TEXT_FLUSH_SECONDS = 0.25
MAX_WAIT_SECONDS = 25.0
WAIT_STEP = 1.0              # how often a waiting run looks at the clock and the stop flag
TEMPERATURE = 0.2
REPLY_LOG_CHARS = 8000      # what the run log keeps of one model reply (the same as a turn keeps)
LOG_ONLY = ("reply",)       # events kept in the run log on disk only: the page never gets them
CONTINUABLE = ("interrupted", "stopped", "limit", "failed")    # a run that ended so can be continued (P-044)
MAX_CHAIN = 3               # a continued run of a continued run: how many earlier runs Continue reads back
MAX_REBUILT_TURNS = 40      # the newest turns a continued run starts with
CUT_COMMAND = ("The run stopped before this command finished, so its result is not known. It is not run again by "
               "itself. If you still need it, use run_check again.")
CUT_WRITE = "The run stopped during this step, so it is not known whether the file changed. Read it again before you change it."
CUT_ASK = "The run stopped while this question waited, so it has no answer. Ask again if you still need to."
CUT_STEP = "The run stopped during this step, so its result is not known."

RUN_ID_RE = re.compile(r"^r[0-9a-f]{8}$")

_GLOBAL = threading.Lock()
_ACTIVE: "Run | None" = None
_RUNS: dict[str, "Run"] = {}


# ── settings ────────────────────────────────────────────────────────────────

def _cfg() -> dict:
    try:
        import codec_config
        with open(codec_config.CONFIG_PATH) as f:
            c = json.load(f)
        return c if isinstance(c, dict) else {}
    except Exception:
        return {}


def settings() -> dict:
    out = dict(DEFAULTS)
    raw = _cfg().get("vibe_agent")
    if isinstance(raw, dict):
        for key, (lo, hi) in BOUNDS.items():
            v = raw.get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out[key] = max(lo, min(hi, int(v)))
        for key in SWITCHES:
            if isinstance(raw.get(key), bool):
                out[key] = raw[key]
        text = raw.get("append_prompt")
        if isinstance(text, str) and len(text) <= MAX_APPEND_PROMPT:     # a longer one (a hand edit) is refused
            out["append_prompt"] = text.strip()
    return out


def _project_block(pid: str) -> dict:
    """The "agent" block of the project's .vibe/project.json, or {}. Only the owner writes there by hand."""
    try:
        meta = json.loads((vp.project_dir(pid) / vp.META_DIR / "project.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, VibeError):
        return {}
    block = meta.get("agent") if isinstance(meta, dict) else None
    return block if isinstance(block, dict) else {}


def run_settings(pid: str) -> dict:
    """settings(), lowered by the project's own agent block (P-065): a number counts only when it is lower than the
    global one, a switch only when it turns something off. Anything else in the block is ignored."""
    out = settings()
    block = _project_block(pid)
    for key, (lo, hi) in BOUNDS.items():
        v = block.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
            out[key] = max(lo, min(out[key], int(v)))
    for key in PROJECT_SWITCHES:
        if block.get(key) is False:
            out[key] = False
    return out


def enabled() -> bool:
    if os.environ.get("VIBE_AGENT_ENABLED", "true").strip().lower() in ("0", "false", "no", "off"):
        return False
    return bool(settings()["enabled"])


def _audit(event: str, message: str, extra: dict) -> None:
    """Counts, tool names and statuses only. Never a goal, a path, a file or a model reply.

    The run id goes in as the correlation_id too, so every line of one run can be found together."""
    try:
        from codec_audit import log_event
        log_event(event, "codec-vibe", message, extra=extra, correlation_id=extra.get("run"))
    except Exception:
        pass


def _push(kind: str) -> None:
    """Tell the owner's phone that a run waits or ended (P-070, docs/VIBE-PUSH-DESIGN.md).

    Only a kind goes out: codec_push sends the fixed line of codec_push.KINDS, never a goal, project, command,
    question or file text. notify() sends from its own thread; any error here is swallowed, so a run is the same
    with push off, not set up or broken. Tests replace this."""
    try:
        import codec_push
        codec_push.notify(kind)
    except Exception:
        log.debug("vibe agent: push %s not sent", kind, exc_info=True)


def _redact(text: str) -> "str | None":
    """Secret-shaped text replaced with the audit log's own patterns. None when they cannot be loaded."""
    try:
        from codec_audit import _redact_string
        return _redact_string(text)
    except Exception:
        return None


def _mono() -> float:
    return time.monotonic()


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


# ── the model ───────────────────────────────────────────────────────────────

@dataclass
class _Llm:
    kind: str           # local | cloud
    base_url: str
    model: str
    label: str
    host: str = ""


def _run_command(*args, **kw):
    """The one place a command is started (tests replace it)."""
    return cmdmod.run(*args, **kw)


def _llm_stream(messages, **kw):
    """The one place the agent talks to a model (tests replace it)."""
    import codec_llm
    return codec_llm.stream(messages, **kw)


def _resolve_llm(kind: str, confirm_cloud: bool = False, cloud_id=None) -> _Llm:
    import urllib.parse
    import codec_cloud_models as ccm
    cfg = _cfg()
    if kind == "cloud":
        if not confirm_cloud:
            raise VibeError(400, "A cloud run needs the owner's confirmation")
        ents = ccm.entries(cfg)
        if not ents:
            raise VibeError(400, "No cloud model is set up")
        if cloud_id is not None and not isinstance(cloud_id, str):
            raise VibeError(400, "Name the cloud model by its id")
        e = ccm.entry_for_id(cloud_id, cfg) if cloud_id else (ccm.active_entry(cfg) or ents[0])    # the entry picked (P-065)
        if e is None:
            raise VibeError(400, "That cloud model is not set up")
        why = ccm.block_message(e)
        if why:
            raise VibeError(409, why)
        return _Llm("cloud", ccm._norm(e["base_url"]), e["id"], str(e.get("label") or e["id"]),
                    urllib.parse.urlparse(e["base_url"]).hostname or "")
    if kind != "local":
        raise VibeError(400, "The model must be local or cloud")
    base = cfg.get("llm_base_url") or ccm.DEFAULT_LOCAL_BASE_URL
    model = cfg.get("llm_model") or ""
    base, model = ccm.local_only(base, model, cfg)      # a switch of chat to the cloud does not move the agent
    if not model:
        try:
            import codec_models
            model = codec_models.DEFAULT_MODEL
        except Exception:
            model = ""
    return _Llm("local", base, model, "Local")


def models_info() -> dict:
    """What the page needs to offer the model choice: the local model, every cloud entry that is set up (`clouds`,
    P-065) and the default one (`cloud`: the active entry, else the first), each with whether it is ready."""
    import urllib.parse
    import codec_cloud_models as ccm
    cfg = _cfg()
    out = {"local": {"label": "Local", "model": _resolve_llm("local").model}, "cloud": None, "clouds": [],
           "limits": {k: settings()[k] for k in ("max_steps", "max_seconds")}}
    ents = ccm.entries(cfg)
    for e in ents:
        why = ccm.block_message(e)
        out["clouds"].append({"id": e["id"], "label": str(e.get("label") or e["id"]),
                              "host": urllib.parse.urlparse(e["base_url"]).hostname or "", "ready": why is None, "reason": why or ""})
    if ents:
        default = (ccm.active_entry(cfg) or ents[0])["id"]
        out["cloud"] = next(c for c in out["clouds"] if c["id"] == default)
    return out


def _probe(url: str, timeout: float = 2.0) -> bool:
    """True when GET url answers 200 within `timeout` (the doctor's one network call; no proxy). Tests replace it."""
    import urllib.request
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(urllib.request.Request(url, method="GET"), timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _writable(path: str) -> bool:
    """True when the SQLite file at `path` could be written: the file (if it is there) and its folder. Never opens it."""
    try:
        folder = os.path.dirname(path) or "."
        if os.path.lexists(path) and not (os.path.isfile(path) and not os.path.islink(path) and os.access(path, os.W_OK)):
            return False
        return os.path.isdir(folder) and os.access(folder, os.W_OK | os.X_OK)
    except Exception:
        return False


def doctor(vibe_db: str) -> dict:
    """The Vibe health check (P-065): five booleans, never a path, a key, a model name or an error text.

    Read-only. The model server is asked only when it is on this Mac or this network."""
    import urllib.parse
    import codec_cloud_models as ccm

    def safe(check) -> bool:
        try:
            return bool(check())
        except Exception:
            return False

    def model_server() -> bool:
        from codec_vibe_chat import is_private_host
        base = _resolve_llm("local").base_url.rstrip("/")
        return is_private_host(urllib.parse.urlparse(base).hostname or "") and _probe(base + "/models")

    def cloud_ready() -> bool:
        return any(ccm.block_message(e) is None for e in ccm.entries(_cfg()))

    return {"sandbox_exec": safe(cmdmod.available), "perl": safe(lambda: os.path.isfile(PERL) and os.access(PERL, os.X_OK)),
            "model_server": safe(model_server), "cloud_ready": safe(cloud_ready), "vibe_db": safe(lambda: _writable(vibe_db))}


# ── results of one tool ─────────────────────────────────────────────────────

@dataclass
class Result:
    ok: bool
    text: str                       # what the model sees
    summary: str                    # one line for the timeline and for old steps
    changed: tuple = ()             # paths whose content changed
    blocked: str = ""               # path | secret: a safety rule refused it
    out: str = ""                   # run_check: the file the command's whole output was copied to (P-043)


def _span(seconds: int) -> str:
    return f"{seconds} seconds" if seconds < 120 else f"{seconds // 60} minutes"


def _size(n: int) -> str:
    return f"{n} B" if n < 1024 else f"{n / 1024:.1f} KB" if n < 1024 * 1024 else f"{n / 1048576:.1f} MB"


def _fail(msg: str, blocked: str = "") -> Result:
    return Result(False, msg, msg.split("\n", 1)[0][:160], blocked=blocked)


def _for_model(text: str, pointer: str = "") -> str:
    """What the model sees of one result: all of it up to MODEL_RESULT_CHARS, else its head and its tail.

    A failing test, an error and a summary come at the end of a command's output, so the last MODEL_TAIL_CHARS
    are always kept whole. Both cuts move to a line end when one is close. `pointer` (P-043) is a line put just after
    the cut marker, so the way to the left-out text sits where it is missing and the tail still ends the result."""
    if len(text) <= MODEL_RESULT_CHARS:
        return text
    head = text[:MODEL_HEAD_CHARS]
    nl = head.rfind("\n")
    if nl >= MODEL_HEAD_CHARS // 2:
        head = head[:nl]
    start = len(text) - MODEL_TAIL_CHARS
    nl = text.rfind("\n", start - CUT_SLACK, start)
    if nl != -1:
        start = nl + 1
    return f"{head}\n... ({start - len(head)} characters left out) ...{pointer}\n{text[start:]}"


# ── kept results: read_output and the way back to an older result (P-043, docs/VIBE-AGENT-SPILL-DESIGN.md) ──

def _lines_of(text: str) -> list:
    lines = text.replace("\r\n", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _pointer(n: int, lines: int) -> str:
    """The line at the cut of a result the model sees in part (see _for_model)."""
    return f"\n(This result is shown in part. Its text is kept, {lines} lines: @@ read_output {n} FIRST-LAST shows any of them.)"


def _spill_file(pid: str, run_id: str, n) -> "Path | None":
    """Where the engine keeps step n's command output: .vibe/runs/<run>/out/<n>.txt. Only the engine writes there."""
    if not RUN_ID_RE.match(str(run_id)) or not isinstance(n, int) or isinstance(n, bool) or n < 1:
        return None
    return vp.project_dir(pid) / vp.META_DIR / RUNS_DIR / run_id / OUT_DIR / f"{n}.txt"


def _read_spill(path) -> "bytes | None":
    """A kept output file, never through a link and never more than the cap. None when it is not there."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except (OSError, TypeError):
        return None
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            return None
        return f.read(cmdmod.SPILL_BYTES + 1)


def _drop_spill(path) -> None:
    """Delete a copy that is not needed, and its run's folders when they are left empty."""
    if not path:
        return
    p = Path(path)
    try:
        p.unlink(missing_ok=True)
        for d in (p.parent, p.parent.parent):       # out/, then the run's own folder; rmdir leaves a full one alone
            d.rmdir()
    except OSError:
        pass


def _kept_lines(pid: str, t) -> "list | None":
    """The lines read_output gives back of one result: its kept file, else the result text the run holds."""
    if t.saved:
        data = _read_spill(_spill_file(pid, t.saved, t.n))
        return None if data is None else _lines_of(cmdmod.clean(data))
    return _lines_of(t.full) if t.full else None


def _way_back(t) -> str:
    """How the model gets an older, shortened result back, or "" when there is nothing to get back."""
    if t.name == "read_file" and t.ok:
        path = t.read_path or t.arg
        rng = t.arg[len(path):] if t.arg.startswith(path) else ""
        return f"@@ read_file {shlex.quote(path)}{rng} reads it again"
    if t.name == "read_output" and t.ok:
        return f"@@ read_output {t.arg} shows it again"
    if t.name in READABLE and (t.full or t.saved):
        return f"@@ read_output {t.n} shows it again"
    return ""


def _fitting(rows: list, room: int) -> int:
    """How many whole rows fit in `room` characters (at least one)."""
    keep = size = 0
    for row in rows:
        size += len(row) + 1
        if size > room and keep:
            break
        keep += 1
    return keep


def _edit_key(path: str) -> str:
    """One path, however the model wrote it (./a.css and a.css are the same file)."""
    try:
        return vp.safe_rel(path)
    except VibeError:
        return path


def _path_failure(e: VibeError, rel: str = "") -> Result:
    msg = e.message
    if "looks like a secret" in msg:
        return _fail("That file name looks like a secret, so the agent may not read or write it.", "secret")
    if e.status == 404:
        return _fail("There is no such file or folder.")
    if e.status == 400:
        return _fail(msg, "path")
    return _fail(msg)


def _read_text(pid: str, rel: str):
    """(content, None) or (None, Result) for a file the agent may read."""
    try:
        info = vp.read_file(pid, rel)
    except VibeError as e:
        if e.status == 404:
            return None, _fail("No such file. Use list_files to see the files, or write_file to create it.")
        return None, _path_failure(e, rel)
    if info.get("too_large"):
        return None, _fail("That file is larger than 1 MB.")
    if info.get("binary"):
        return None, _fail("That file is not text.")
    return info["content"], None


def _names_a_path(files, where: str) -> bool:
    """True when `where` is a file or folder of the project: then a name such as pages/[id].js stays a path."""
    return any(f["path"] == where or f["path"].startswith(where + "/") for f in files)


def _t_list_files(run: "Run", folder: str) -> Result:
    files = [f for f in vp.list_files(run.pid) if not vp.looks_secret(f["path"])]
    folder = (folder or "").strip().strip("/")
    where = f" in {folder}"
    if folder and vsearch.is_glob(folder) and not _names_a_path(files, folder):   # a glob such as src/**/*.js (P-066)
        try:
            match = vsearch.matcher(folder)
        except vsearch.GlobError as e:
            return _fail(str(e), "path" if e.unsafe else "")
        files = [f for f in files if match(f["path"])]
        where = f" matching {folder}"
    elif folder:
        try:
            vp.safe_rel(folder)
        except VibeError as e:
            return _path_failure(e)
        files = [f for f in files if f["path"].startswith(folder + "/")]
    if not files:
        return Result(True, "No files." if not folder else f"No files{where}.", "No files")
    shown = files[:LIST_ENTRIES]
    lines = [f"{f['path']}  ({_size(f['size'])})" for f in shown]
    more = f"\n... and {len(files) - len(shown)} more" if len(files) > len(shown) else ""
    head = f"{len(files)} file(s)" + (where if folder else "")
    return Result(True, head + ":\n" + "\n".join(lines) + more, head)


def _t_read_file(run: "Run", path: str, first: int, last) -> Result:
    content, bad = _read_text(run.pid, path)
    if bad:
        return bad
    lines = content.replace("\r\n", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    total = len(lines)
    if total == 0:
        return Result(True, f"{path} is empty.", f"Read {path} (empty)")
    if first > total:
        return _fail(f"The file has only {total} lines.")
    end = min(last or total, total, first + READ_LINES - 1)
    rows = proto.numbered(lines[first - 1:end], first, width_to=total).split("\n")
    keep = _fitting(rows, MODEL_RESULT_CHARS - 2 * len(path) - 80)   # whole lines that fit in what the model sees, beside the header
    end = first + keep - 1
    head = f"{path}, lines {first}-{end} of {total}"
    more = f" (more lines follow: @@ read_file {path} {end + 1}-)" if end < total else ""
    return Result(True, head + more + "\n" + "\n".join(rows[:keep]), f"Read {head}")


def _t_read_output(run: "Run", n: int, first: int, last) -> Result:
    """Numbered lines of an earlier result of this run (P-043): its kept file, or the text the run holds.

    A read or a change of a file is never given back from here: the model reads the file itself, as it is now."""
    t = next((x for x in reversed(run.turns) if x.n == n), None)
    if t is None:
        return _fail(f"There is no result {n} to read. Use the number of a [RESULT n ...] line.")
    if t.name == "read_file":
        path = shlex.quote(t.read_path or t.arg.split(" ")[0])
        return _fail(f"Result {n} is a read of a file. Read the file itself with @@ read_file {path}, so you get it as it is now.")
    if t.name in ("edit_file", "write_file", "delete_file"):
        return _fail(f"Result {n} changed a file. Read the file itself with read_file to see it as it is now.")
    if t.name == "read_output":
        return _fail(f"Result {n} is itself a read_output. Use @@ read_output {t.arg} again.")
    lines = _kept_lines(run.pid, t) if t.name in READABLE else None
    if lines is None:
        return _fail(f"The text of result {n} is no longer kept." if t.saved else f"Result {n} has no text to read again.")
    total = len(lines)
    label = f"Result {n} ({(t.name + ' ' + t.arg).strip()})"
    if total == 0:
        return Result(True, f"{label} is empty.", f"Read result {n} (empty)")
    if first > total:
        return _fail(f"Result {n} has only {total} lines.")
    end = min(last or total, total, first + READ_LINES - 1)
    rows = proto.numbered(lines[first - 1:end], first, width_to=total).split("\n")
    keep = _fitting(rows, MODEL_RESULT_CHARS - len(label) - 100)
    end = first + keep - 1
    head = f"{label}, lines {first}-{end} of {total}"
    more = f" (more lines follow: @@ read_output {n} {end + 1}-)" if end < total else ""
    return Result(True, head + more + "\n" + "\n".join(rows[:keep]), f"Read result {n}, lines {first}-{end} of {total}")


def _search_texts(run: "Run", where: str, match):
    """(path, text) of every text file the search covers: no secret names, inside `where` or matching the glob."""
    for f in vp.list_files(run.pid):
        rel = f["path"]
        if vp.looks_secret(rel):
            continue
        if match is not None:
            if not match(rel):
                continue
        elif where and rel != where and not rel.startswith(where + "/"):
            continue
        try:
            info = vp.read_file(run.pid, rel)
        except VibeError:
            continue
        text = info.get("content")
        if isinstance(text, str):
            yield rel, text


def _plain_search(texts, low: str, mode: str) -> vsearch.Found:
    """Plain text, any case, in this process. The same rules as the regex child's loop."""
    found = vsearch.Found()
    for rel, text in texts:
        n = 0
        for no, line in enumerate(text.split("\n"), 1):
            if low not in line.lower():
                continue
            n += 1
            if mode == "files":
                break
            if mode == "lines":
                if len(found.hits) >= SEARCH_MATCHES:
                    found.more = True
                    break
                found.hits.append((rel, no, line.strip()[:200]))
        if n:
            found.counts.append((rel, n))
        if found.more:
            break
    return found


def _t_search(run: "Run", pattern: str, path: str, regex: bool = False, mode: str = "lines") -> Result:
    pat = (pattern or "").strip()
    if not pat or len(pat) > 200:
        return _fail("Give a search text of 1 to 200 characters.")
    mode = mode if mode in ("files", "count") else "lines"
    where = (path or "").strip().strip("/")
    match = None
    if where and vsearch.is_glob(where) and not _names_a_path(vp.list_files(run.pid), where):   # PATH may be a glob (P-066)
        try:
            match = vsearch.matcher(where)
        except vsearch.GlobError as e:
            return _fail(str(e), "path" if e.unsafe else "")
    elif where:
        try:
            vp.safe_rel(where)
        except VibeError as e:
            return _path_failure(e)
    note = ""
    if regex:                                       # never compiled here: a child process runs it (P-066)
        batch, size, left = [], 0, 0
        for rel, text in _search_texts(run, where, match):
            cost = len(text.encode("utf-8", "replace"))
            if left or size + cost > vsearch.REGEX_MAX_BYTES:
                left += 1
                continue
            size += cost
            batch.append((rel, text))
        if left:
            note = (f"\n(the regex search read only the first {len(batch)} files, {vsearch.REGEX_MAX_BYTES // 1048576} MB "
                    f"of text; {left} more were not searched: give a PATH to search them)")
        try:
            found = vsearch.run_regex(pat, mode, batch, SEARCH_MATCHES)
        except vsearch.RegexFailed as e:
            return _fail(str(e))
    else:
        found = _plain_search(_search_texts(run, where, match), pat.lower(), mode)
    if not found.counts and not found.hits:
        return Result(True, f'No matches for "{pat}".' + note, f'No matches for "{pat[:40]}"')
    if mode == "files":
        head = f'{len(found.counts)} file(s) match "{pat}"'
        return Result(True, head + ":\n" + "\n".join(rel for rel, _n in found.counts) + note,
                      f'{len(found.counts)} file(s) match "{pat[:40]}"')
    if mode == "count":
        total = sum(n for _rel, n in found.counts)
        head = f'{total} matching line(s) in {len(found.counts)} file(s) for "{pat}"'
        return Result(True, head + ":\n" + "\n".join(f"{rel}: {n}" for rel, n in found.counts) + note,
                      f'{total} matching line(s) in {len(found.counts)} file(s) for "{pat[:40]}"')
    hits = [f"{rel}:{n}: {line}" for rel, n, line in found.hits]
    tail = "\n(more matches exist: narrow the search)" if found.more else ""
    return Result(True, "\n".join(hits) + tail + note, f'{len(hits)}{"+" if found.more else ""} match(es) for "{pat[:40]}"')


def _changed_region(old: str, new: str) -> str:
    a, b = old.split("\n"), new.split("\n")
    i = 0
    while i < len(a) and i < len(b) and a[i] == b[i]:
        i += 1
    start = max(0, i - 2)
    return proto.numbered(b[start:start + 12], start + 1)


def _with_check(res: Result, path: str, text: str) -> Result:
    """An edit result with the syntax note at its end, when the saved file does not check clean (P-045).

    The file stays saved and checkpointed: the note only reports, and the summary keeps a few words of it."""
    note = vcheck.check_file(path, text)
    if note is not None:
        res.text += "\n\n" + note.text
        res.summary += f" ({note.short})"
    return res


def _t_edit_file(run: "Run", path: str, pairs) -> Result:
    content, bad = _read_text(run.pid, path)
    if bad:
        return bad
    new, err = proto.apply_pairs(content, pairs)
    if err:
        return _fail(err)
    if new == content:
        return _fail("The edit changes nothing: SEARCH and REPLACE are the same.")
    try:
        done = vp.agent_write(run.pid, path, new)
    except VibeError as e:
        return _path_failure(e)
    added, removed = proto.line_stats(content, new)
    head = f"Edited {done['path']} (+{added} -{removed})"
    return _with_check(Result(True, head + ". The changed part now reads:\n" + _changed_region(content, new), head,
                              changed=(done["path"],)), done["path"], new)


def _t_write_file(run: "Run", path: str, content: str) -> Result:
    text = content if content == "" or content.endswith("\n") else content + "\n"
    try:
        done = vp.agent_write(run.pid, path, text)
    except VibeError as e:
        return _path_failure(e)
    n = text.count("\n")
    head = f"Wrote {done['path']} ({'new' if done['created'] else 'replaced'}, {n} lines, {_size(done['size'])})"
    return _with_check(Result(True, head + ".", head, changed=(done["path"],)), done["path"], text)


def _t_delete_file(run: "Run", path: str) -> Result:
    try:
        done = vp.agent_delete(run.pid, path)
    except VibeError as e:
        return _path_failure(e)
    return Result(True, f"Deleted {done['path']}.", f"Deleted {done['path']}", changed=(done["path"],))


# ── project notes (P-046, docs/VIBE-AGENT-NOTES-DESIGN.md) ──────────────────

def _notes_bytes(pdir: Path, parts: tuple) -> tuple:
    """(bytes, "") of one notes file, or (None, why it was not read); (None, "") when it is not there.

    Never through a link (the folder on the way or the file), only a regular file of at most 1 MB."""
    cur = pdir
    for seg in parts[:-1]:
        cur = cur / seg
        if os.path.islink(cur) or not cur.is_dir():
            return None, ""
    try:
        fd = os.open(cur / parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None, ""
    except OSError:
        return None, "a link" if os.path.islink(cur / parts[-1]) else "cannot be opened"
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode):
            return None, "not a file"
        if st.st_size > vp.MAX_FILE_BYTES:
            return None, "larger than 1 MB"
        data = f.read(vp.MAX_FILE_BYTES + 1)
    return (None, "larger than 1 MB") if len(data) > vp.MAX_FILE_BYTES else (data, "")


def _cut_notes(text: str, room: int) -> str:
    """At most `room` characters of a notes file, cut at a line end when one is close."""
    if len(text) <= room:
        return text
    cut = text[:room]
    nl = cut.rfind("\n")
    return (cut[:nl] if nl >= room * 2 // 3 else cut).rstrip()


def read_notes(pid: str, limits: dict, kind: str = "local") -> tuple:
    """(listing, blocks) of the project's notes files at run start: each file hashed and listed with its size, and the
    text the model sees, at most notes_chars in all (notes_chars_cloud for a cloud run)."""
    if not limits.get("notes", True):
        return [], ""
    pdir = vp.project_dir(pid)
    room = int(limits.get("notes_chars_cloud" if kind == "cloud" else "notes_chars")
               or DEFAULTS["notes_chars_cloud" if kind == "cloud" else "notes_chars"])
    files = NOTES_FILES + ((CLAUDE_MD,) if limits.get("import_claude_md") else ())
    listing, blocks = [], []
    for parts in files:
        label = "/".join(parts)
        data, why = _notes_bytes(pdir, parts)
        if data is None:
            if why:
                listing.append({"path": label, "skipped": why})
            continue
        entry = {"path": label, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        try:
            text = data.decode("utf-8").replace("\r\n", "\n").strip()
        except UnicodeDecodeError:
            listing.append({**entry, "skipped": "not text"})
            continue
        shown = _cut_notes(text, room) if room > 0 else ""
        room -= len(shown)
        entry.update(shown=len(shown), cut=len(shown) < len(text))
        if text and not shown:
            entry["skipped"] = "no room left"
        listing.append(entry)
        if shown:
            blocks.append(proto.notes_block(label, shown, len(text) - len(shown)))
    return listing, "\n\n".join(blocks)


# ── one run ─────────────────────────────────────────────────────────────────

@dataclass
class _Turn:
    n: int
    name: str
    arg: str
    assistant: str
    assistant_short: str
    result: str                 # wrapped, in full
    summary: str
    ok: bool
    read_path: str = ""
    stale: bool = False
    after: str = ""             # engine-written lines after the result's [END RESULT]: the owner's note (P-044)
    full: str = ""              # the whole result text the run holds, for read_output (READABLE tools only, P-043)
    saved: str = ""             # the run whose out/ folder keeps this step's command output, when it was kept
    lines: int = 0              # that file's line count


@dataclass
class _Reply:
    text: str = ""
    usage: dict = field(default_factory=dict)
    cut: bool = False           # the model hit the reply token cap
    normal_end: bool = False    # the stream ended by itself
    error: bool = False
    stopped: bool = False
    timed_out: bool = False


class _ContextTooBig(Exception):
    pass


def _tool_arg(call: proto.Call) -> str:
    a = call.args
    if call.name == "list_files":
        return a.get("folder") or "."
    if call.name in ("read_file", "read_output"):
        rng = f" {a['first']}-{a['last'] or ''}" if (a["first"] != 1 or a["last"]) else ""
        return (a["path"] if call.name == "read_file" else str(a["n"])) + rng
    if call.name == "search":
        flags = ("--regex " if a.get("regex") else "") + {"files": "--files ", "count": "--count "}.get(a.get("mode"), "")
        return flags + f'"{a["pattern"][:60]}"' + (f" in {a['path']}" if a["path"] else "")
    if call.name == "edit_file":
        return f"{a['path']} ({len(a['pairs'])} edit{'s' if len(a['pairs']) != 1 else ''})"
    if call.name in ("write_file", "delete_file"):
        return a["path"]
    if call.name == "run_check":
        return a["command"][:120]
    return ""


def _signature(call: proto.Call) -> str:
    return call.name + "\x00" + json.dumps(call.args, sort_keys=True, default=str)


class Run:
    def __init__(self, pid: str, goal: str, history: list, llm: _Llm, limits: dict, can_run: bool, *,
                 continues: str = "", turns=None, step0: int = 0, resume: str = "", left_out: int = 0, base_cp: str = "",
                 todo=None):
        self.id = "r" + secrets.token_hex(4)
        self.pid, self.goal, self.history, self.llm, self.limits, self.can_run = pid, goal, history, llm, limits, can_run
        self.allowed = tuple(t for t in proto.TOOLS if t != "run_check" or (can_run and cmdmod.available()))
        if not limits.get("allow_commands", True):          # Settings > Vibe, or the project's block (P-065)
            self.allowed = tuple(t for t in self.allowed if t != "run_check")
        if limits.get("todo", DEFAULTS["todo"]):
            self.allowed += (proto.TODO,)
        self.todo: list = proto.clean_todo(todo) if proto.TODO in self.allowed else []    # P-046
        self._todo_step: int | None = None      # the step the list last changed at, in this run
        self._todo_refused = 0                  # the step whose todo block came too soon
        self.notes: list = []                   # the notes files read at the start (path, size, sha256)
        self.notes_text = ""                    # their NOTES blocks, as the first message holds them
        self.status = "running"
        self.finished = False
        self.continues, self._step0, self._left_out = continues, step0, left_out     # Continue (P-044)
        self._base_cp = base_cp     # the files as the continued run left them: its newest checkpoint
        self.step = step0           # steps are numbered on from the run this one continues; the budget is fresh
        self.turns: list[_Turn] = list(turns or [])
        if continues and self.turns:
            t = self.turns[-1]
            t.after = (t.after + "\n" if t.after else "") + f"(The earlier run stopped here: {resume} This run goes on from here.)"
        self._note: dict | None = None
        self.events: list[dict] = []
        self.start_cp: str | None = None
        self.tokens = {"prompt": 0, "completion": 0}
        self.estimated = False      # a step had no usage from the server, so the totals hold an estimate (P-065)
        self.stop_event = threading.Event()
        self._cond = threading.Condition()
        self._seq = 0
        self._t0 = _mono()
        self.deadline = self._t0 + limits["max_seconds"]
        self._sigs: list[str] = []
        self._format_fails = 0
        self._cuts = 0
        self._model_fails = 0
        self._edit_fails: dict[str, int] = {}
        self._spilled = 0           # bytes of command output this run keeps in its out/ folder (P-043)
        self._questions = 0
        self._answer: dict | None = None
        self._qid = ""
        self._decision: dict | None = None
        self._aid = ""
        self._thread: threading.Thread | None = None

    # events -------------------------------------------------------------

    def _log_path(self) -> Path:
        return vp.project_dir(self.pid) / vp.META_DIR / RUNS_DIR / f"{self.id}.jsonl"

    def _write_log(self, ev: dict) -> None:
        try:
            path = self._log_path()
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        except (OSError, VibeError):
            log.warning("vibe agent: could not write the run log", exc_info=True)

    def _emit(self, kind: str, **fields) -> dict:
        with self._cond:
            self._seq += 1
            ev = {"seq": self._seq, "t": round(time.time(), 3), "type": kind, **fields}
            self.events.append(ev)
            if kind != "text":
                self._write_log(ev)
            self._cond.notify_all()
            return ev

    def _log_reply(self, n: int, reply: "_Reply") -> None:
        """The model's reply, for the run log on disk only: it never goes into self.events, so the page never gets it.

        Secret shapes are replaced first with the audit log's own patterns; without them only the counts are kept.
        No seq: the event is not part of the page's sequence (get_run also leaves it out)."""
        ev = {"t": round(time.time(), 3), "type": "reply", "step": n, "chars": len(reply.text), "cut": reply.cut}
        clean = _redact(reply.text)
        if clean is not None:
            ev["text"] = clean[:REPLY_LOG_CHARS]
            ev["redacted"] = clean != reply.text
        with self._cond:
            self._write_log(ev)

    def _drop_text(self, step: int) -> None:
        with self._cond:
            self.events = [e for e in self.events if not (e["type"] == "text" and e.get("step") == step)]

    def view(self, since: int = 0, wait: float = 0.0) -> dict:
        wait = max(0.0, min(float(wait or 0), MAX_WAIT_SECONDS))
        end = _mono() + wait
        with self._cond:
            while wait > 0 and not self.finished and not any(e["seq"] > since for e in self.events):
                left = end - _mono()
                if left <= 0:
                    break
                self._cond.wait(left)
            evs = [e for e in self.events if e["seq"] > since]
            return {"run": self.id, "project": self.pid, "status": self.status, "finished": self.finished,
                    "events": evs, "next": evs[-1]["seq"] if evs else since, "step": self.step}

    # control ------------------------------------------------------------

    def stop(self) -> None:
        self.stop_event.set()
        with self._cond:
            self._cond.notify_all()

    def answer(self, qid: str, text: str) -> None:
        with self._cond:
            if self.status != "waiting" or qid != self._qid:
                raise VibeError(409, "Nothing is waiting for that answer")
            self._answer = {"text": text}
            self._cond.notify_all()

    def message(self, text: str) -> dict:
        """Keep the owner's note for the next step (P-044). A note not read yet is replaced by the newer one."""
        with self._cond:
            if self.finished or self.stop_event.is_set():
                raise VibeError(409, "The run is over")
            replaced = self._note is not None
            self._note = {"id": "m" + secrets.token_hex(3), "text": text}
            return {"id": self._note["id"], "replaced": replaced}

    def _take_note(self) -> "dict | None":
        """The waiting note, put after the newest result, outside its [END RESULT]. None when there is none, or no
        result yet for it to follow (a note sent during the first step waits for its result)."""
        with self._cond:
            note = self._note
            if note is None or not self.turns:
                return None
            self._note = None
        t = self.turns[-1]
        got = {**note, "turn": t, "before": t.after}
        t.after = (t.after + "\n" if t.after else "") + proto.owner_note(note["text"])
        return got

    def _give_back(self, got: "dict | None") -> None:
        """The prompt with the note could not be built: the note goes back unread."""
        if got:
            got["turn"].after = got["before"]
            with self._cond:
                if self._note is None:
                    self._note = {"id": got["id"], "text": got["text"]}

    def approve(self, aid, decision, network) -> None:
        if decision not in ("once", "always", "decline"):
            raise VibeError(400, "Choose once, always or decline")
        with self._cond:
            if self.status != "waiting" or not isinstance(aid, str) or not aid or aid != self._aid:
                raise VibeError(409, "Nothing is waiting for that decision")
            self._decision = {"decision": decision, "network": bool(network) and decision != "decline"}
            self._cond.notify_all()

    def _end(self, status: str, report: str) -> None:
        """Finish the run. Everything else is done before the end event, so whoever sees it can undo at once."""
        global _ACTIVE
        with self._cond:
            if self.finished:
                return
            self.status = status
        vp.agent_release(self.pid, self.id)
        with _GLOBAL:
            if _ACTIVE is self:
                _ACTIVE = None
        self._trim_logs()
        seconds = round(_mono() - self._t0, 1)
        steps = self.step - self._step0                      # this run's own steps
        _audit("vibe_agent_end", "Vibe agent run ended", {
            "project": self.pid, "run": self.id, "status": status, "steps": steps, "seconds": seconds,
            "prompt_tokens": self.tokens["prompt"], "completion_tokens": self.tokens["completion"], "model": self.llm.kind})
        with self._cond:
            self._emit("end", status=status, report=str(report)[:proto.MAX_REPORT_CHARS], steps=steps, seconds=seconds,
                       prompt_tokens=self.tokens["prompt"], completion_tokens=self.tokens["completion"],
                       start_checkpoint=self.start_cp, model=self._model_info(), **self._usage_fields())
            self.finished = True
            self._cond.notify_all()
        if not self.stop_event.is_set():          # the owner's own Stop needs no push
            _push("vibe_done")

    def _model_info(self) -> dict:
        """The model on the run card (P-065): its kind, its id (the local model, or the cloud entry) and its label."""
        return {"kind": self.llm.kind, "id": self.llm.model, "label": self.llm.label}

    def _usage_fields(self) -> dict:
        """Whether the token totals hold an estimate and, for a cloud run, this month's spend on its entry so far and its
        cap (P-065). Never raises: a spend file that cannot be read leaves the spend out."""
        out = {"estimated": self.estimated}
        if self.llm.kind == "cloud":
            try:
                import codec_cloud_models as ccm
                out["spent_usd"] = round(ccm.spent_usd(self.llm.model), 4)
                e = ccm.entry_for_id(self.llm.model, _cfg())
                if e is not None:
                    out["cap_usd"] = ccm.cap_usd(e)
            except Exception:
                log.debug("vibe agent: spend not read", exc_info=True)
        return out

    def _count(self, reply: "_Reply", messages: list, seconds: float) -> None:
        """One reply's tokens: added to the run's totals (the server's numbers, else the estimate), shown on the card
        as a usage event, and one line for Settings > Usage (source "vibe"). Counts only, never text (P-065)."""
        u = reply.usage
        server = bool(u.get("prompt_tokens")) and isinstance(u.get("completion_tokens"), int)
        if not server:
            self.estimated = True
        self.tokens["prompt"] += int(u.get("prompt_tokens") or self._estimate(messages))
        self.tokens["completion"] += int(u.get("completion_tokens") or len(reply.text) / CHARS_PER_TOKEN)
        self._emit("usage", prompt_tokens=self.tokens["prompt"], completion_tokens=self.tokens["completion"],
                   **self._usage_fields())             # no step: the totals so far, not a part of any step
        stats = {"elapsed_s": round(seconds, 1)}
        if server:
            ct = int(u["completion_tokens"])
            stats["completion_tokens"] = ct
            tm = u.get("timings") if isinstance(u.get("timings"), dict) else {}
            speed = tm.get("predicted_per_second")
            if isinstance(speed, (int, float)) and not isinstance(speed, bool) and speed > 0:
                stats["tok_per_s"] = round(float(speed), 1)
            elif ct and seconds > 0:
                stats["tok_per_s"] = round(ct / seconds, 1)
        if not reply.text.strip():              # the model did not answer: no reply to count
            return
        try:
            import codec_usage
            codec_usage.record_reply(self.llm.model, self.llm.base_url, stats, source="vibe")
        except Exception:
            log.debug("vibe agent: usage line not written", exc_info=True)

    def _trim_logs(self) -> None:
        try:
            d = self._log_path().parent
            files = sorted((p for p in d.glob("r*.jsonl") if RUN_ID_RE.match(p.stem)), key=lambda p: p.stat().st_mtime, reverse=True)
            live = {r.id for r in list(_RUNS.values()) if not r.finished}
            for p in files[KEEP_RUNS:]:
                if p.stem not in live:
                    p.unlink()
            for p in d.iterdir():                   # a run's kept output goes with its log (P-043); never a link
                if (RUN_ID_RE.match(p.name) and p.name not in live and not p.is_symlink() and p.is_dir()
                        and not (d / f"{p.name}.jsonl").exists()):
                    shutil.rmtree(p, ignore_errors=True)
        except (OSError, VibeError):
            pass

    def _out_path(self, n: int) -> "str | None":
        """A fresh place for step n's command output, its folders made 0700. None when there is none."""
        path = None
        try:
            path = _spill_file(self.pid, self.id, n)
            if path is not None:
                path.parent.parent.parent.mkdir(parents=True, exist_ok=True, mode=0o700)    # .vibe/runs, as the log
                path.parent.parent.mkdir(exist_ok=True, mode=0o700)
                path.parent.mkdir(exist_ok=True, mode=0o700)
        except (OSError, VibeError):
            return None
        return str(path) if path is not None else None

    def _keep(self, n: int, name: str, result: Result) -> tuple:
        """(pointer line, run id of the kept file or "", its lines) for one result (P-043).

        Only a result the model sees in part gets the pointer. A command's copied output is kept only then; any other
        copy is deleted at once. Without a kept file, read_output gives back the text the run holds."""
        long = name in READABLE and len(result.text) > MODEL_RESULT_CHARS
        if result.out:
            data = _read_spill(result.out) if long else None
            if data is not None:
                self._spilled += len(data)
                lines = len(_lines_of(cmdmod.clean(data)))
                return _pointer(n, lines), self.id, lines
            _drop_spill(result.out)
        if long:
            return _pointer(n, len(_lines_of(result.text))), "", 0
        return "", "", 0

    # the loop -----------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._main, name=f"vibe-agent-{self.id}", daemon=True)
        self._thread.start()

    def _main(self) -> None:
        try:
            self._begin()
            self._loop()
        except Exception as e:
            # A 413 that gets this far is the file scan (start checkpoint or per-step file map): tool writes are
            # answered in _exec. The cap and the scan stay; the run says why it stopped (P-064).
            if isinstance(e, VibeError) and e.status == 413:
                self._end("failed", f"The project has more than {vp.MAX_FILES} files, which is more than the agent can "
                                    "work with. Remove some files, then start again.")
            else:
                log.exception("vibe agent run failed")
                self._end("failed", "The agent stopped because of an internal error.")
        finally:
            if not self.finished:
                self._end("failed", "The agent stopped unexpectedly.")

    def _begin(self) -> None:
        head = "Before agent (continued): " if self.continues else "Before agent: "
        cp = vp.agent_checkpoint(self.pid, head + self.goal.replace("\n", " ")[:60], "before", self.id, 0, force=True)
        self.start_cp = cp["id"] if cp else None
        if self.continues:
            self._changed_since_continued()
        more = {"continues": self.continues, "earlier_steps": self._step0} if self.continues else {}
        try:
            self.notes, self.notes_text = read_notes(self.pid, self.limits, self.llm.kind)     # hashed once, at the start
        except (OSError, VibeError):
            log.warning("vibe agent: could not read the project notes", exc_info=True)
        if self.notes:
            more["notes"] = self.notes
        self._emit("start", goal=self.goal, model=self._model_info(), can_run=self.can_run,
                   commands="run_check" in self.allowed,
                   limits={k: self.limits[k] for k in ("max_steps", "max_seconds")}, start_checkpoint=self.start_cp, **more)
        if self.todo:                                       # the list of the run this one continues
            self._emit("todo", step=self.step, items=self.todo)
        _audit("vibe_agent_start", "Vibe agent run started", {
            "project": self.pid, "run": self.id, "model": self.llm.kind, "goal_chars": len(self.goal)})

    def _changed_since_continued(self) -> None:
        """Files that changed after the continued run stopped (Undo run, a step undone, the owner's own edits): its
        reads of them are stale, and the model is told, so it does not take an undone step as done (P-044)."""
        if not (self.turns and self._base_cp and self.start_cp):
            return
        try:
            changed = [f["path"] for f in vp.diff(self.pid, self._base_cp, self.start_cp)["files"]]
        except (VibeError, OSError):
            return
        if not changed:
            return
        keys = {_edit_key(p) for p in changed}
        for t in self.turns:
            if t.read_path and _edit_key(t.read_path) in keys:
                t.stale = True
        names = ", ".join(changed[:10]) + (f" and {len(changed) - 10} more" if len(changed) > 10 else "")
        t = self.turns[-1]
        t.after += ("\n(These files changed after the earlier run's last saved step, for example by an undo or by hand: "
                    + proto.mark_owner_phrase(names) + ". Earlier steps on them may no longer hold: read them again.)")

    def _loop(self) -> None:
        lim = self.limits
        while True:
            if self.stop_event.is_set():
                return self._end("stopped", "Stopped.")
            if self.step - self._step0 >= lim["max_steps"]:
                return self._end("limit", f"Stopped at the limit of {lim['max_steps']} steps. The files keep the work done so far.")
            if _mono() > self.deadline:
                return self._end("limit", f"Stopped at the time limit of {_span(lim['max_seconds'])}. The files keep the work done so far.")
            if self.llm.kind == "cloud":
                import codec_cloud_models as ccm
                e = ccm.entry_for_id(self.llm.model, _cfg())
                why = ccm.block_message(e) if e else "The cloud model is no longer set up."
                if why:
                    return self._end("failed", why)
            self.step += 1
            n = self.step
            note = self._take_note()
            try:
                messages = self._messages()
            except _ContextTooBig:
                self._give_back(note)
                return self._end("limit", "The project is too big for the context limit. Raise max_context_tokens or use fewer files.")
            if note:
                self._emit("message", step=note["turn"].n, id=note["id"], text=note["text"])
            reply = self._stream(n, messages)
            self._drop_text(n)
            if reply.text.strip():
                self._log_reply(n, reply)
            if reply.stopped:
                return self._end("stopped", "Stopped.")
            if reply.timed_out:
                return self._end("limit", f"Stopped at the time limit of {_span(lim['max_seconds'])}. The files keep the work done so far.")
            if reply.error and not reply.text.strip():
                self._model_fails += 1
                self.step -= 1
                if self._model_fails >= MODEL_RETRIES:
                    return self._end("failed", "The model did not answer. Check that it is running, then try again.")
                _sleep(2.0)
                continue
            self._model_fails = 0
            scan = proto.scan_reply(reply.text, self.allowed, final=True, lenient=reply.normal_end)
            if scan.note:
                self._emit("note", step=n, text=scan.note[:600])
            if scan.todo:
                self._take_todo(n, scan.todo)
            if reply.cut and scan.state != "complete":
                self._cuts += 1
                if self._cuts >= 2:
                    return self._end("limit", "The model's reply was cut off twice in a row. Try a smaller goal.")
                self._bad_reply(n, reply.text, "Your reply was cut off at the length limit. Use smaller edits: one change per edit_file, or write a long file in parts.")
                continue
            self._cuts = 0
            if scan.state != "complete":
                self._format_fails += 1
                if self._format_fails >= FAIL_FORMAT:
                    return self._end("limit", "I could not read the model's replies as tool blocks.")
                msg = scan.error or "I found no tool block in your reply. Reply with one short sentence and one tool block that starts with @@ at the start of a line.\n" + proto.FORMAT_HELP
                self._bad_reply(n, reply.text, msg)
                continue
            self._format_fails = 0
            done = self._run_call(n, reply.text, scan.call)
            if done:
                return

    def _take_todo(self, n: int, items: list) -> None:
        """A todo block of step n becomes the list, unless the list changed less than TODO_EVERY steps ago (P-046).
        Either way it costs no step: the reply's tool block is read as usual."""
        if self._todo_step is not None and n - self._todo_step < TODO_EVERY:
            self._todo_refused = n
            return
        self.todo, self._todo_step = items, n
        self._emit("todo", step=n, items=items)

    def _todo_text(self, n: int) -> str:
        """The list as the last message of step n shows it, or "" when there is none."""
        if not self.todo:
            return ""
        nxt = n if self._todo_step is None else max(n, self._todo_step + TODO_EVERY)
        head = ("TODO LIST (yours" + (f", from step {self._todo_step - self._step0}" if self._todo_step is not None else "")
                + ("; you can change it now" if nxt <= n else f"; you can change it at step {nxt - self._step0}") + "):")
        rows = [f"- [{'x' if it['done'] else ' '}] {it['text']}" for it in self.todo]
        late = (["(The todo block in your last reply was not taken: the list changes at most once every "
                 f"{TODO_EVERY} steps.)"] if self._todo_refused and self._todo_refused == n - 1 else [])
        return proto.mark_owner_phrase("\n".join([head] + rows + late))

    def _budget_text(self, n: int) -> str:
        """One line on the budget left, at the end of the last message (P-046)."""
        own, top = n - self._step0, self.limits["max_steps"]
        mins = max(1, math.ceil(max(0.0, self.deadline - _mono()) / 60))
        return f"(This is step {own} of {top}, about {mins} min left." + (" This is the last step.)" if own >= top else ")")

    def _bad_reply(self, n: int, text: str, message: str) -> None:
        wrapped = proto.wrap_result(n, "error", "", message, ok=False)
        self._emit("result", step=n, ok=False, summary=message.split("\n", 1)[0][:160], output=message[:DISPLAY_CHARS], tool="error")
        said = proto.without_todo(text).strip() or "(no reply)"
        self.turns.append(_Turn(n, "error", "", said[:2000], said[:300], wrapped, message.split("\n", 1)[0][:160], False))
        _audit("vibe_agent_step", "Vibe agent step", {"project": self.pid, "run": self.id, "n": n, "tool_name": "error", "ok": False, "ms": 0})

    def _run_call(self, n: int, text: str, call: proto.Call) -> bool:
        """Run one tool. True when the run is over."""
        arg = _tool_arg(call)
        self._emit("tool", step=n, name=call.name, arg=arg)
        sig = _signature(call)
        self._sigs.append(sig)
        streak = 0
        for s in reversed(self._sigs):
            if s != sig:
                break
            streak += 1
        if streak >= REPEAT_STOP:
            self._end("limit", "The agent kept repeating the same step, so I stopped it.")
            return True
        t0 = _mono()
        if call.name == "finish":
            report = call.args["report"]
            if report == "Done." and call.note:             # the summary went into the sentence before the block
                report = call.note
            self._emit("result", step=n, ok=True, summary="Finished", output=report, tool="finish")
            _audit("vibe_agent_step", "Vibe agent step", {"project": self.pid, "run": self.id, "n": n, "tool_name": "finish", "ok": True, "ms": 0})
            self._end("done", report)
            return True
        if call.name == "ask":
            result = self._ask(n, call)
            if result is None:
                return True
        elif call.name == "run_check":
            result = self._run_check(n, call)
            if result is None:
                return True
        else:
            result = self._exec(call)
        ms = int((_mono() - t0) * 1000)
        warn = ""
        if streak >= REPEAT_WARN:
            warn = f"\nYou have made this same call {streak} times in a row. Do something different, or use finish."
        if result.blocked:
            _audit("vibe_agent_blocked", "Vibe agent step refused", {"project": self.pid, "run": self.id, "n": n, "reason": result.blocked})
        advice = ""
        if not result.ok and call.name == "edit_file" and not result.blocked:
            key = _edit_key(call.args["path"])
            self._edit_fails[key] = self._edit_fails.get(key, 0) + 1
            if self._edit_fails[key] >= FAIL_EDITS:
                advice = self._edit_advice(key, self._edit_fails[key])
        elif result.ok and result.changed:
            for p in result.changed:
                self._edit_fails.pop(p, None)
        pointer, saved, lines = self._keep(n, call.name, result)
        wrapped = proto.wrap_result(n, call.name, arg, _for_model(result.text, pointer) + advice + warn, result.ok)
        kept = {"out": {"lines": lines}} if saved else {}       # so a continued run finds the file (P-043)
        self._emit("result", step=n, ok=result.ok, summary=result.summary, output=(result.text + advice)[:DISPLAY_CHARS], tool=call.name, **kept)
        _audit("vibe_agent_step", "Vibe agent step", {"project": self.pid, "run": self.id, "n": n, "tool_name": call.name, "ok": result.ok, "ms": ms})
        text = proto.without_todo(text)                     # the engine shows the todo list itself (P-046)
        short = text if len(text) <= 600 else (call.note[:300] + "\n" + f"@@ {call.name} {arg}\n(content left out)")
        self.turns.append(_Turn(n, call.name, arg, text[:8000], short, wrapped, result.summary, result.ok,
                                read_path=call.args.get("path", "") if call.name == "read_file" else "",
                                full=result.text if call.name in READABLE else "", saved=saved, lines=lines))
        if result.ok and result.changed:
            self._checkpoint(n, f"Agent: {call.name} {result.changed[0]}", result.changed)
        elif call.name == "run_check":
            self._checkpoint(n, "Agent: ran " + call.args["command"][:60], ())
        return False

    def _edit_advice(self, rel: str, fails: int) -> str:
        """From the FAIL_EDITS-th edit of one file in a row that did not apply: what to do instead. The run goes on.

        A file one reply can hold is rewritten with write_file. A longer one is not: a write_file cut at the reply
        limit is refused, so the advice is to copy fewer lines exactly."""
        try:
            size = next((f["size"] for f in vp.list_files(self.pid) if f["path"] == rel), 0)
        except VibeError:
            size = 0
        if size > self.limits["max_reply_tokens"] * CHARS_PER_TOKEN / 2:
            return (f"\n\nThat is {fails} edits of {rel} in a row that did not apply, and the file is too long to write again "
                    "in one reply. Read the lines you want to change again with read_file, then copy one or two of them "
                    "exactly into SEARCH.")
        return (f"\n\nThat is {fails} edits of {rel} in a row that did not apply. Stop editing it: read it again with "
                "read_file, then rewrite the file with write_file, with its whole content.")

    def _checkpoint(self, n: int, label: str, paths) -> None:
        """Save the files as a step of this run, when they changed, and tell the page."""
        try:
            cp = vp.agent_checkpoint(self.pid, label[:120], "ai", self.id, n)
        except VibeError:
            cp = None
        changed = set(paths) | set((cp or {}).get("changed", []))
        for t in self.turns:
            if t.read_path and t.read_path in changed:
                t.stale = True
        if cp:
            self._emit("checkpoint", step=n, id=cp["id"], parent=cp.get("parent"), label=cp["label"],
                       lines_added=cp["stat"]["lines_added"], lines_removed=cp["stat"]["lines_removed"], changed=cp.get("changed", []))

    def _exec(self, call: proto.Call) -> Result:
        a = call.args
        try:
            if call.name == "list_files":
                return _t_list_files(self, a["folder"])
            if call.name == "read_file":
                return _t_read_file(self, a["path"], a["first"], a["last"])
            if call.name == "read_output":
                return _t_read_output(self, a["n"], a["first"], a["last"])
            if call.name == "search":
                return _t_search(self, a["pattern"], a["path"], a.get("regex", False), a.get("mode", "lines"))
            if call.name == "edit_file":
                return _t_edit_file(self, a["path"], a["pairs"])
            if call.name == "write_file":
                return _t_write_file(self, a["path"], a["content"])
            if call.name == "delete_file":
                return _t_delete_file(self, a["path"])
        except VibeError as e:
            return _fail(e.message)
        except OSError as e:
            log.warning("vibe agent tool error: %s", e)
            return _fail("The file could not be read or written.")
        return _fail(f"The tool {call.name} is not available.")

    # commands ----------------------------------------------------------

    def _command_audit(self, n: int, decision: str, command: str, network: bool, exit_code, ms: int) -> None:
        word = (command.split() or [""])[0].rsplit("/", 1)[-1]
        program = word if re.fullmatch(r"[A-Za-z0-9._+-]{1,30}", word) else ""
        _audit("vibe_agent_command", "Vibe agent command", {"project": self.pid, "run": self.id, "n": n, "decision": decision,
                                                           "program": program, "network": bool(network), "exit_code": exit_code, "ms": ms})

    def _approval(self, n: int, command: str):
        """Ask the owner. (decision, network), or None when the run ended while waiting."""
        self._aid = "a" + secrets.token_hex(3)
        with self._cond:
            self._decision = None
            self.status = "waiting"
        asked = _mono()                           # before the event: an answer can come the moment it is out
        self._emit("approval", step=n, id=self._aid, command=command)
        _push("vibe_approval")
        end = asked + self.limits["wait_seconds"]
        with self._cond:
            while self._decision is None and not self.stop_event.is_set():
                left = end - _mono()
                if left <= 0:
                    break
                self._cond.wait(min(left, WAIT_STEP))
            got = self._decision
            self.status = "running" if (got is not None and not self.stop_event.is_set()) else self.status
        self.deadline += _mono() - asked          # the owner's time is not work time: max_seconds is the work budget
        if self.stop_event.is_set():
            self._end("stopped", "Stopped.")
            return None
        if got is None:
            self._end("stopped", f"No answer came within {_span(self.limits['wait_seconds'])}, so the agent stopped.")
            return None
        self._emit("decision", step=n, id=self._aid, decision=got["decision"], network=got["network"])
        return got["decision"], got["network"]

    def _run_check(self, n: int, call: proto.Call) -> "Result | None":
        """Run one command the owner approved, in the sandbox. None when the run ended while waiting."""
        command = call.args["command"]
        try:
            command = cmdmod.check_command(command)
        except cmdmod.Refused as e:
            self._command_audit(n, "refused", command, False, None, 0)
            return Result(False, f"That command was refused: {e}. Use a plainer one.", "Command refused", blocked="dangerous")
        stored = next((a for a in vp.allowed_commands(self.pid) if a["cmd"] == command), None)
        if stored:
            decision, network = "stored", bool(stored["network"])
        else:
            got = self._approval(n, command)
            if got is None:
                return None
            decision, network = got
            if decision == "decline":
                self._command_audit(n, "declined", command, False, None, 0)
                return Result(False, "The owner did not allow that command, so it was not run. Do not ask for it again. "
                                     "Check your work another way, or finish and say it was not checked.", "Declined")
            if decision == "always":
                try:
                    vp.allow_command(self.pid, command, network)
                except VibeError:
                    pass
        spill = self._out_path(n) if self._spilled < SPILL_RUN_BYTES else None     # the engine's copy of the output (P-043)
        t0 = _mono()
        try:
            res = _run_command(vp.project_dir(self.pid), vp.vibe_root(), command, network=network,
                               timeout=max(5.0, min(float(cmdmod.DEFAULT_TIMEOUT), self.deadline - _mono())), stop_event=self.stop_event,
                               spill=spill)
        except cmdmod.Refused as e:
            _drop_spill(spill)
            return Result(False, str(e), "Command not run")
        self._command_audit(n, decision, command, network, res.exit_code, int((_mono() - t0) * 1000))
        if res.stopped:
            head = "Stopped before the command finished."
        elif res.timed_out:
            head = f"Timed out after {int(res.seconds)} s and was stopped."
        else:
            head = f"Exit code {res.exit_code} after {res.seconds} s."
        extra = ""
        if res.timed_out:
            extra += f"\nA command is stopped after {cmdmod.DEFAULT_TIMEOUT} seconds. Do not start servers or watchers with run_check."
        if res.denied:
            extra += ("\nThe sandbox stopped something: the command can read this project and the usual toolchains, write only inside the "
                      f"project, and has {'web access only' if network else 'no network'}.")
        ok = res.exit_code == 0
        state = "stopped" if res.stopped else "timed out" if res.timed_out else f"exit {res.exit_code}"
        out = Result(ok, head + "\n" + (res.output.strip("\n") or "(no output)") + extra, f"Ran {command[:50]}: {state} ({res.seconds} s)")
        if getattr(res, "spilled", False) and spill:
            out.out = spill                             # _keep decides whether it stays
        else:
            _drop_spill(spill)
        return out

    def _ask(self, n: int, call: proto.Call) -> "Result | None":
        """Wait for the owner. None when the run ended while waiting."""
        if self._questions >= MAX_QUESTIONS:
            return _fail(f"You already asked {MAX_QUESTIONS} questions. Decide yourself, or finish and say what is unclear.")
        self._questions += 1
        self._qid = "q" + secrets.token_hex(3)
        with self._cond:
            self._answer = None
            self.status = "waiting"
        asked = _mono()                           # before the event, as for an approval
        self._emit("question", step=n, id=self._qid, text=call.args["question"], choices=call.args["choices"])
        _push("vibe_question")
        end = asked + self.limits["wait_seconds"]
        with self._cond:
            while self._answer is None and not self.stop_event.is_set():
                left = end - _mono()
                if left <= 0:
                    break
                self._cond.wait(min(left, WAIT_STEP))
            answer = self._answer
            self.status = "running" if (answer is not None and not self.stop_event.is_set()) else self.status
        self.deadline += _mono() - asked          # as for an approval: waiting for the owner is not work time
        if self.stop_event.is_set():
            self._end("stopped", "Stopped.")
            return None
        if answer is None:
            self._end("stopped", f"No answer came within {_span(self.limits['wait_seconds'])}, so the agent stopped.")
            return None
        self._emit("answer", step=n, id=self._qid)
        text = str(answer["text"])[:1000]
        return Result(True, "The owner answered: " + text, "Answered")

    # the model ----------------------------------------------------------

    def _first_user(self) -> str:
        parts = ["GOAL\n" + self.goal]
        if self.history:
            lines = [f"{m['role']}: {m['content']}" for m in self.history]
            parts.append("EARLIER IN THIS CHAT (context only)\n" + "\n".join(lines))
        if self.notes_text:                                 # never a [RESULT] block (P-046)
            parts.append("PROJECT NOTES (read at the start of this run)\n" + self.notes_text)
        if self.continues and not self._step0:
            parts.append("This run continues an earlier run on this goal that stopped before its first step.")
        elif self.continues:
            gone = f" Its first {self._left_out} steps are left out to save space." if self._left_out else ""
            parts.append("This run continues an earlier run on this goal that stopped before it was done. Its steps and "
                         "their results follow." + gone + " Go on from where it stopped: do not repeat steps that worked, "
                         "and read a file again before you change it.")
        parts.append("Work in the project folder. Start with the smallest step that moves toward the goal.")
        return "\n\n".join(parts)

    def _map(self) -> str:
        files = [f for f in vp.list_files(self.pid) if not vp.looks_secret(f["path"])]
        if not files:
            return "PROJECT FILES: none yet"
        shown = files[:MAP_LINES]
        more = f"\n... and {len(files) - len(shown)} more (list_files shows them)" if len(files) > len(shown) else ""
        # The map comes after the owner's note (P-044) and file names are data: one cannot pass for the owner's line.
        return proto.mark_notes_markers(proto.mark_owner_phrase(      # nor for a [NOTES] block (P-046)
            f"PROJECT FILES ({len(files)}):\n" + "\n".join(f"{f['path']} ({_size(f['size'])})" for f in shown) + more))

    @staticmethod
    def _result_text(t: _Turn, mode: str) -> str:
        if t.stale:
            return f"[RESULT {t.n} {t.name} {t.arg}] The file has changed since. Read it again if you need it."
        if mode == "full":
            return t.result
        back = _way_back(t)                     # an older result names its way back (P-043)
        return (f"[RESULT {t.n} {t.name} {t.arg}] {t.summary} (shortened to save space" + (f"; {back})" if back else ")")
                + ("" if t.ok else " (this step failed)"))

    @staticmethod
    def _estimate(msgs: list) -> int:
        return int(sum(len(m["content"]) for m in msgs) / CHARS_PER_TOKEN) + 4 * len(msgs)

    def _messages(self) -> list:
        cap = self.limits["max_context_tokens"]
        system = {"role": "system", "content": proto.system_prompt(self.allowed, notes=bool(self.notes_text))}
        if self.limits.get("append_prompt"):                    # the owner's own text from Settings > Vibe (P-065)
            system["content"] += "\n\n" + APPEND_HEAD + "\n" + self.limits["append_prompt"]
        modes = ["full" if i >= len(self.turns) - FULL_RESULTS else "short" for i in range(len(self.turns))]
        site = self._map()
        for extra in (self._todo_text(self.step),                       # the list survives shortening (P-046)
                      self._budget_text(self.step) if self.limits.get("budget_line", True) else ""):
            if extra:
                site += "\n\n" + extra
        first = self._first_user()
        shrink = None
        while True:
            msgs = [system]
            users = [first]
            for i, t in enumerate(self.turns):
                msgs.append({"role": "user", "content": users[-1]})
                msgs.append({"role": "assistant", "content": t.assistant if modes[i] == "full" else t.assistant_short})
                body = self._result_text(t, modes[i])
                if shrink and i == len(self.turns) - 1 and len(body) > shrink:
                    half = shrink // 2
                    back = f"; @@ read_output {t.n} shows all of it" if t.name in READABLE and (t.full or t.saved) else ""
                    body = body[:half] + f"\n... (cut to fit the context{back}) ...\n" + body[-half:]
                if t.after:                                     # after [END RESULT], never inside the data
                    body += "\n\n" + t.after
                users.append(body)
            msgs.append({"role": "user", "content": users[-1] + "\n\n" + site})
            if self._estimate(msgs) <= cap:
                return msgs
            idx = next((i for i, m in enumerate(modes[:-1]) if m == "full"), None)
            if idx is not None:
                modes[idx] = "short"
                continue
            if self.turns and (shrink is None or shrink > 1500):
                shrink = max(1500, int(cap * CHARS_PER_TOKEN * 0.3)) if shrink is None else shrink // 2
                continue
            raise _ContextTooBig()

    def _stream(self, n: int, messages: list) -> _Reply:
        import codec_llm
        reply = _Reply()
        pending = ""
        last = began = _mono()
        kw = dict(base_url=self.llm.base_url, model=self.llm.model, api_key="", max_tokens=self.limits["max_reply_tokens"],
                  temperature=TEMPERATURE, timeout=180.0, enable_thinking=False, usage_sentinel=True, error_sentinel=True)
        gen = _llm_stream(messages, **kw)
        try:
            for item in gen:
                if self.stop_event.is_set():
                    reply.stopped = True
                    break
                if _mono() > self.deadline:
                    reply.timed_out = True
                    break
                if isinstance(item, codec_llm.StreamUsage):
                    reply.usage = dict(item)
                    continue
                if item is codec_llm.STREAM_ERROR:
                    reply.error = True
                    break
                if item is codec_llm.FINISH_LENGTH:
                    reply.cut = True
                    continue
                if not isinstance(item, str):
                    continue
                reply.text += item
                pending += item
                if _mono() - last >= TEXT_FLUSH_SECONDS:
                    self._emit("text", step=n, chunk=pending)
                    pending, last = "", _mono()
                sc = proto.scan_reply(reply.text, self.allowed, final=False)
                if sc.state in ("complete", "error") and sc.cut:
                    break
            else:
                reply.normal_end = not (reply.cut or reply.error)
        finally:
            close = getattr(gen, "close", None)
            if close:
                close()
        if pending:
            self._emit("text", step=n, chunk=pending)
        self._count(reply, messages, _mono() - began)
        return reply


# ── starting, finding and stopping runs ─────────────────────────────────────

def _clean_history(history) -> list:
    out = []
    if isinstance(history, list):
        for m in history[-MAX_HISTORY_TURNS:]:
            if isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str):
                c = m["content"].strip()
                if c:
                    out.append({"role": m["role"], "content": c[:MAX_HISTORY_CHARS]})
    return out


def start_run(pid: str, goal, *, model: str = "local", confirm_cloud: bool = False, history=None, can_run: bool = False,
              cloud_id=None) -> dict:
    if not vp.enabled() or not enabled():
        raise VibeError(404, "The agent is switched off")
    if not isinstance(goal, str) or not goal.strip():
        raise VibeError(400, "Write the goal first")
    goal = goal.strip()
    if len(goal) > MAX_GOAL_CHARS:
        raise VibeError(400, f"The goal is longer than {MAX_GOAL_CHARS} characters")
    vp.project_dir(pid)
    llm = _resolve_llm(model if isinstance(model, str) else "local", bool(confirm_cloud), cloud_id)
    return _launch(Run(pid, goal, _clean_history(history), llm, run_settings(pid), bool(can_run)))


def _launch(run: Run) -> dict:
    """Hold the project for the run and start its thread (a new run, or a continued one)."""
    global _ACTIVE
    pid, llm = run.pid, run.llm
    with _GLOBAL:
        if _ACTIVE is not None and not _ACTIVE.finished:
            raise VibeError(409, "The agent is already working on a project. Stop it first.")
        vp.agent_claim(pid, run.id)
        _ACTIVE = run
        _RUNS[run.id] = run
        for rid in [r for r, x in _RUNS.items() if x.finished][:-KEEP_RUNS]:
            _RUNS.pop(rid, None)
    try:
        run.start()
    except Exception:
        run._end("failed", "The agent could not start.")
        raise
    return {"run": run.id, "status": "running", "model": {"kind": llm.kind, "label": llm.label},
            "limits": {k: run.limits[k] for k in ("max_steps", "max_seconds")}}


def _file_events(pid: str, run_id: str) -> list:
    if not RUN_ID_RE.match(str(run_id)):
        raise VibeError(404, "No such run")
    path = vp.project_dir(pid) / vp.META_DIR / RUNS_DIR / f"{run_id}.jsonl"
    if not path.is_file():
        raise VibeError(404, "No such run")
    out = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if isinstance(ev, dict):
                out.append(ev)
    return out


def _status_of(events: list) -> str:
    end = next((e for e in reversed(events) if e.get("type") == "end"), None)
    return end["status"] if end else "interrupted"


def get_run(pid: str, run_id: str, since: int = 0, wait: float = 0.0) -> dict:
    vp.project_dir(pid)
    run = _RUNS.get(run_id)
    if run is not None and run.pid == pid:
        view = run.view(since, wait)
    else:
        events = _file_events(pid, run_id)
        evs = [e for e in events if e.get("type") not in LOG_ONLY and int(e.get("seq", 0)) > int(since or 0)]
        view = {"run": run_id, "project": pid, "status": _status_of(events), "finished": True, "events": evs,
                "next": evs[-1]["seq"] if evs else int(since or 0), "step": sum(1 for e in events if e.get("type") == "tool")}
    if view["finished"]:                     # the page shows Continue when this is true (P-044)
        view["continuable"] = view["status"] in CONTINUABLE and _newest_run(pid) == run_id
    return view


def run_replies(pid: str, run_id: str, after: int = 0) -> list:
    """The model's replies after step `after`, as the run log keeps them (redacted, at most REPLY_LOG_CHARS), for the
    page's Raw replies switch (P-069). Only asked for by name (?replies=N): get_run never returns them."""
    vp.project_dir(pid)
    after = int(after or 0)
    out = []
    for e in _file_events(pid, run_id):
        n = _step_of(e)
        if e.get("type") == "reply" and n is not None and n > after:
            out.append({k: e[k] for k in ("step", "t", "chars", "cut", "redacted", "text") if k in e})
    return out


# ── Continue: a new run that starts with an ended run's turns (P-044, docs/VIBE-AGENT-CONTINUE-DESIGN.md) ──

def _newest_run(pid: str) -> "str | None":
    d = vp.project_dir(pid) / vp.META_DIR / RUNS_DIR
    if not d.is_dir():
        return None
    files = [p for p in d.glob("r*.jsonl") if RUN_ID_RE.match(p.stem)]
    return max(files, key=lambda p: p.stat().st_mtime).stem if files else None


def _step_of(ev: dict):
    n = ev.get("step")
    return n if isinstance(n, int) and not isinstance(n, bool) else None


def _short_text(text: str, note: str, name: str, arg: str) -> str:
    return text if len(text) <= 600 else (note[:300] + "\n" + f"@@ {name} {arg}\n(content left out)")


def _replayed_reply(text: str, name: str, arg: str):
    """(assistant text, scanned call or None) for a logged step. A log without the reply text (written before P-040,
    or when the redaction patterns could not load) gets the tool line back instead."""
    text = proto.without_todo(text)                     # as the run kept it (P-046)
    if not text.strip():
        text = f"@@ {name} {arg}".rstrip()
    scan = proto.scan_reply(text, proto.TOOLS, final=True, lenient=True)
    call = scan.call if scan.state == "complete" and scan.call is not None and scan.call.name == name else None
    return text, call, (call.note if call else scan.note)


def _turn_from_log(n: int, tool, reply: str, ev: dict, pid: str = "", owner: str = "") -> _Turn:
    """One step of an ended run, as the model saw it then: its reply, and its result in the wrapper.

    `owner` is the run whose log this is: a command output it kept is read from its out/ folder (P-043)."""
    ok = ev.get("ok") is not False
    output = str(ev.get("output") or "")
    summary = str(ev.get("summary") or "")[:160]
    name, arg = tool if tool else (str(ev.get("tool") or "error"), "")
    if name == "error":
        said = proto.without_todo(reply).strip() or "(no reply)"       # as _bad_reply kept it (P-046)
        return _Turn(n, "error", "", said[:2000], said[:300], proto.wrap_result(n, "error", "", output, ok=False), summary, False)
    text, call, note = _replayed_reply(reply, name, arg)
    read_path = ""
    if name == "read_file":
        read_path = call.args.get("path", "") if call else arg.split(" ")[0]
    full = output if name in READABLE else ""
    saved, lines, kept = "", 0, ev.get("out")
    if full and pid and isinstance(kept, dict) and isinstance(kept.get("lines"), int) and not isinstance(kept["lines"], bool):
        f = _spill_file(pid, owner, n)
        if f is not None and f.is_file() and not f.is_symlink():
            saved, lines = owner, max(0, kept["lines"])
    pointer = _pointer(n, lines if saved else len(_lines_of(output))) if full and len(output) > MODEL_RESULT_CHARS else ""
    return _Turn(n, name, arg, text[:8000], _short_text(text, note, name, arg),
                 proto.wrap_result(n, name, arg, _for_model(output, pointer), ok), summary, ok, read_path=read_path,
                 full=full, saved=saved, lines=lines)


def _cut_turn(n: int, tool, reply: str, turns: list) -> _Turn:
    """A step that has a tool event and no result: the run stopped in the middle of it. It is never run again."""
    name, arg = tool
    text, call, note = _replayed_reply(reply, name, arg)
    if name == "run_check":
        msg = CUT_COMMAND
    elif name == "ask":
        msg = CUT_ASK
    elif name in ("edit_file", "write_file", "delete_file"):
        msg = CUT_WRITE
        path = _edit_key(call.args.get("path", "")) if call else ""
        for t in turns:
            if t.read_path and path and _edit_key(t.read_path) == path:
                t.stale = True
    else:
        msg = CUT_STEP
    return _Turn(n, name, arg, text[:8000], _short_text(text, note, name, arg),
                 proto.wrap_result(n, name, arg, msg, ok=False), "The run stopped during this step", False)


def _rebuild_one(events: list, turns: list, by_n: dict, notes: list, pid: str = "", owner: str = "") -> int:
    """Append one run's turns from its log; the owner's notes are kept for later. Returns the last step number."""
    replies, tools, done, last = {}, {}, set(), 0
    for ev in events:
        kind, n = ev.get("type"), _step_of(ev)
        if kind == "reply" and n is not None and isinstance(ev.get("text"), str):
            replies[n] = ev["text"]
        elif kind == "tool" and n is not None:
            tools[n] = (str(ev.get("name") or ""), str(ev.get("arg") or ""))
            last = max(last, n)
        elif kind == "result" and n is not None:
            t = _turn_from_log(n, tools.get(n), replies.get(n, ""), ev, pid, owner)
            turns.append(t)
            by_n[n] = t
            done.add(n)
            last = max(last, n)
        elif kind == "checkpoint":
            changed = set(ev.get("changed") or [])
            for t in turns:
                if t.read_path and t.read_path in changed:
                    t.stale = True
        elif kind == "message":
            notes.append(ev)
    for n in sorted(set(tools) - done):
        t = _cut_turn(n, tools[n], replies.get(n, ""), turns)
        turns.append(t)
        by_n[n] = t
    return last


def _rebuild(pid: str, run_id: str, events: list):
    """(turns, last step, all turns count) of a run and of the runs it continued, oldest first."""
    chain, rid, evs = [], run_id, events
    while True:
        chain.append((rid, evs))                    # each log with its run id: kept outputs live in that run's folder
        start = next((e for e in evs if e.get("type") == "start"), {})
        rid = start.get("continues") if isinstance(start.get("continues"), str) else None
        if not rid or len(chain) > MAX_CHAIN:
            break
        try:
            evs = _file_events(pid, rid)
        except VibeError:
            break                                   # an older log was trimmed: start from what is left
    turns, by_n, notes, last = [], {}, [], 0
    for rid, evs in reversed(chain):
        last = max(last, _rebuild_one(evs, turns, by_n, notes, pid, rid))
    for ev in notes:
        t = by_n.get(_step_of(ev))
        if t is not None and isinstance(ev.get("text"), str) and ev["text"].strip():
            t.after = (t.after + "\n" if t.after else "") + proto.owner_note(ev["text"])
    return turns[-MAX_REBUILT_TURNS:], last, len(turns)


def continue_run(pid: str, run_id: str, *, confirm_cloud: bool = False, can_run: bool = False) -> dict:
    """Start a run that goes on with an ended run's goal and turns, with a fresh step and time budget.

    The old run's model kind is kept, so a cloud run needs the owner's confirmation again. can_run follows this
    request, not the old run. Nothing of the old run is run again: a cut step is told to the model as cut."""
    if not vp.enabled() or not enabled():
        raise VibeError(404, "The agent is switched off")
    vp.project_dir(pid)
    live = _RUNS.get(run_id)
    if live is not None and live.pid == pid and not live.finished:
        raise VibeError(409, "That run is still going")
    events = _file_events(pid, run_id)
    status = _status_of(events)
    if status not in CONTINUABLE:
        raise VibeError(409, "That run finished its goal. Start a new one." if status == "done" else "That run cannot be continued")
    if _newest_run(pid) != run_id:
        raise VibeError(409, "Only the newest run of this project can be continued")
    start = next((e for e in events if e.get("type") == "start"), {})
    goal = start.get("goal")
    if not isinstance(goal, str) or not goal.strip():
        raise VibeError(409, "That run has no goal to go on with")
    old = start.get("model") if isinstance(start.get("model"), dict) else {}
    kind = "cloud" if old.get("kind") == "cloud" else "local"
    entry = old.get("id") if kind == "cloud" and isinstance(old.get("id"), str) else None    # the same cloud entry (P-065)
    llm = _resolve_llm(kind, bool(confirm_cloud), entry)
    turns, last, total = _rebuild(pid, run_id, events)
    end = next((e for e in reversed(events) if e.get("type") == "end"), None)
    resume = str(end.get("report") or end.get("status") or "")[:300] if end else "CODEC was restarted."
    cps = [e["id"] for e in events if e.get("type") == "checkpoint" and isinstance(e.get("id"), str)]
    base = cps[-1] if cps else start.get("start_checkpoint")
    todo = next((e.get("items") for e in reversed(events) if e.get("type") == "todo"), None)     # P-046
    run = Run(pid, goal.strip()[:MAX_GOAL_CHARS], [], llm, run_settings(pid), bool(can_run), continues=run_id, turns=turns,
              step0=last, resume=resume, left_out=total - len(turns), base_cp=base if isinstance(base, str) else "",
              todo=todo)
    out = _launch(run)
    out.update(continues=run_id, earlier_steps=last)
    return out


def message_run(pid: str, run_id: str, text) -> dict:
    """The owner's note to a running agent (P-044): read at its next step, after the newest result."""
    run = _live(pid, run_id)
    if not isinstance(text, str) or not text.strip():
        raise VibeError(400, "Write the message first")
    text = text.strip()
    if len(text) > proto.MAX_NOTE_CHARS:
        raise VibeError(400, f"A message to a running agent is at most {proto.MAX_NOTE_CHARS} characters")
    return {"run": run_id, **run.message(text)}


def list_runs(pid: str) -> list:
    d = vp.project_dir(pid) / vp.META_DIR / RUNS_DIR
    if not d.is_dir():
        return []
    out = []
    files = sorted((p for p in d.glob("r*.jsonl") if RUN_ID_RE.match(p.stem)), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[:KEEP_RUNS]:
        try:
            events = _file_events(pid, p.stem)
        except VibeError:
            continue
        start = next((e for e in events if e.get("type") == "start"), {})
        live = _RUNS.get(p.stem)
        status = live.status if (live is not None and not live.finished) else _status_of(events)
        end = next((e for e in reversed(events) if e.get("type") == "end"), {})
        out.append({"run": p.stem, "goal": str(start.get("goal", ""))[:200], "status": status,
                    "started": start.get("t"), "steps": sum(1 for e in events if e.get("type") == "tool"),
                    "model": (start.get("model") or {}).get("label", ""), "start_checkpoint": start.get("start_checkpoint"),
                    "seconds": end.get("seconds")})
    return out


def live_run() -> dict:
    """The one run going on this Mac now (running or waiting), ids only, or nulls (P-070).

    The page opened from a phone notification (/vibe, a fixed URL) uses it to show that run again."""
    with _GLOBAL:
        run = _ACTIVE
    if run is None or run.finished:
        return {"project": None, "run": None, "status": None}
    return {"project": run.pid, "run": run.id, "status": run.status}


def _live(pid: str, run_id: str) -> Run:
    vp.project_dir(pid)
    run = _RUNS.get(run_id)
    if run is None or run.pid != pid:
        raise VibeError(404, "No such run")
    return run


def approve_run(pid: str, run_id: str, aid, decision, network=False) -> dict:
    run = _live(pid, run_id)
    if not run.can_run:
        raise VibeError(403, "Commands can only be approved for a run started at this Mac")
    run.approve(aid, decision, bool(network))
    return {"run": run_id, "ok": True}


def commands(pid: str) -> list:
    return vp.allowed_commands(pid)


def forget(pid: str, command) -> list:
    if not isinstance(command, str):
        raise VibeError(400, "Send the command text")
    return vp.forget_command(pid, command)


def stop_run(pid: str, run_id: str) -> dict:
    run = _live(pid, run_id)
    run.stop()
    return {"run": run_id, "stopping": not run.finished}


def answer_run(pid: str, run_id: str, qid, text) -> dict:
    run = _live(pid, run_id)
    if not isinstance(qid, str) or not isinstance(text, str) or not text.strip():
        raise VibeError(400, "Send the question id and the answer")
    run.answer(qid, text.strip())
    return {"run": run_id, "ok": True}
