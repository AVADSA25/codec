"""Keep the test suite off the owner's live CODEC state (P-009, docs/TEST-LIVE-STATE-DESIGN.md).

Used by tests/conftest.py; not a test module.

- `write_fake_security`: a stand-in for /usr/bin/security that codec_keychain is
  pointed at, so no test reads or writes the owner's Keychain.
- `Guard`: in this process, refuses writes to the owner's ~/.codec memory.db,
  notifications, pending questions, audit log, shift report state, qchat.db, vibe.db, the
  overlay events file, the licence key cache and grace state and config.json, any
  sqlite3.connect to them, and any run of the real /usr/bin/security. Each
  refusal is recorded, so the test that caused it fails even when the code
  under test swallows the error.
- `watch_snapshot` / `watch_changes`: a read-only before/after look at a short,
  named list of hazards. Results are counts only, never file names.
"""
from __future__ import annotations

import builtins
import hashlib
import io
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

ACTIVE = None  # the installed Guard, or None (no owner ~/.codec, e.g. CI)
OWNER_HOME_EXISTED = False  # set by tests/conftest.py: the owner's ~/.codec was there before the run

REAL_SECURITY = "/usr/bin/security"

# A file in the owner's ~/.codec whose name starts with one of these is refused.
# The prefixes cover the side files too: memory.db-wal, notifications.json.lock,
# the shift report's notifications.tmp, audit.log.1, pending_questions.json.tmp.
# qchat.db and vibe.db (chat and Vibe history) were added on 11 Nov 2026: a test or tool opened
# the live vibe.db on 4 Nov and ran P-077's column migration there; the prefixes also cover the
# copies their migrations write (qchat.db.bak-voice, vibe.db.pre-v4-*.bak).
# overlay_events.jsonl, license_pubkey.pem, .license_state.json and config.json were added on
# 12 Nov 2026: tests that ran the real run_skill appended skill_fired lines to the live overlay
# file (the Swift overlay may show them) and the licence check rewrote the live key cache and
# grace state; a test that migrates a key could blank the live config.json. Reads stay allowed.
PROTECTED_NAMES = ("memory.db", "notifications.", "pending_questions.", "audit.log",
                   "shift_report_state.", "qchat.db", "vibe.db", "overlay_events.",
                   "license_pubkey.", ".license_state.", "config.json")

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC

# ── Fake `security` ──────────────────────────────────────────────────────────

FAKE_SECURITY = r'''#!{python}
"""Stand-in for /usr/bin/security written by tests/conftest.py (P-009).

Items live in items.json next to this file. Exit codes follow the real tool:
44 not found, 45 already exists (add without -U), 51 locked (while a file
named "locked" sits next to this file)."""
import fcntl, json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
argv = sys.argv[1:]
cmd = argv[0] if argv else ""
if os.path.exists(os.path.join(here, "locked")):
    sys.stderr.write("security: User interaction is not allowed.\n")
    sys.exit(51)
def opt(flag):
    i = argv.index(flag) if flag in argv else -1
    return argv[i + 1] if 0 <= i < len(argv) - 1 else ""
item = opt("-s") + "|" + opt("-a")
store_path = os.path.join(here, "items.json")
with open(store_path + ".lock", "w") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        with open(store_path) as f:
            store = json.load(f)
    except (OSError, ValueError):
        store = {}
    if cmd == "find-generic-password":
        if item not in store:
            sys.stderr.write("security: The specified item could not be found in the keychain.\n")
            sys.exit(44)
        if "-w" in argv:
            sys.stdout.write(store[item] + "\n")
        sys.exit(0)
    if cmd == "add-generic-password":
        if item in store and "-U" not in argv:
            sys.stderr.write("security: The specified item already exists in the keychain.\n")
            sys.exit(45)
        store[item] = opt("-w")
    elif cmd == "delete-generic-password":
        if item not in store:
            sys.stderr.write("security: The specified item could not be found in the keychain.\n")
            sys.exit(44)
        del store[item]
    else:
        sys.stderr.write("fake security: unsupported command\n")
        sys.exit(2)
    with open(store_path + ".tmp", "w") as f:
        json.dump(store, f)
    os.replace(store_path + ".tmp", store_path)
sys.exit(0)
'''


def write_fake_security(folder) -> Path:
    """Write the fake `security` program into `folder` and return its path."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    fake = folder / "security"
    fake.write_text(FAKE_SECURITY.replace("{python}", sys.executable))
    os.chmod(fake, stat.S_IRWXU)
    return fake


# ── Guard ────────────────────────────────────────────────────────────────────


class LiveStateRefused(PermissionError):
    """A test reached for the owner's live CODEC state or Keychain (P-009)."""


def _is_real_security(args, executable=None) -> bool:
    prog = executable
    try:
        if prog is None:
            if isinstance(args, (str, bytes, os.PathLike)):
                words = os.fsdecode(args).split()
                prog = words[0] if words else ""
            else:
                prog = args[0]
        prog = os.fsdecode(prog)
    except Exception:
        return False
    if os.path.basename(prog) != "security":
        return False
    if os.sep not in prog:
        prog = shutil.which(prog) or prog
    return os.path.realpath(prog) == REAL_SECURITY


def describe(hits) -> str:
    """'open for write of ~/.codec/notifications.*; ...' for a list of (what, name)."""
    return "; ".join(sorted({f"{what} of " + ("the real Keychain" if name == "keychain"
                                              else f"the owner's ~/.codec/{name}*")
                             for what, name in hits}))


class Guard:
    """Refuses this process's writes to the owner's live state under `codec_home`."""

    def __init__(self, codec_home, allow_real_keychain: bool = False):
        self.home = os.path.realpath(os.path.expanduser(str(codec_home)))
        self.allow_real_keychain = allow_real_keychain
        self.hits: list = []  # (what, name) per refusal
        self._reported = 0    # hits[:_reported] were already handed out by unreported()

    def protected(self, path):
        """The protected name `path` falls under, or None."""
        if isinstance(path, int):
            return None
        try:
            p = os.fsdecode(path)
        except TypeError:
            return None
        base = os.path.basename(p)
        name = next((n for n in PROTECTED_NAMES if base.startswith(n)), None)
        if name is None:
            return None
        if os.path.realpath(os.path.dirname(os.path.abspath(p))) != self.home:
            return None
        return name

    def refuse(self, what: str, name: str):
        self.hits.append((what, name))
        raise LiveStateRefused(f"test isolation (P-009): {describe([(what, name)])} refused; "
                               "point the test at tmp_path")

    def check_path(self, what: str, path) -> None:
        name = self.protected(path)
        if name:
            self.refuse(what, name)

    def check_command(self, args, executable=None) -> None:
        if not self.allow_real_keychain and _is_real_security(args, executable):
            self.refuse("running /usr/bin/security", "keychain")

    def unreported(self) -> list:
        """Refusals since the last call. The conftest fixture fails the test on any,
        so a refusal counts even when the code under test swallowed the error."""
        new = self.hits[self._reported:]
        self._reported = len(self.hits)
        return new

    @contextmanager
    def expected(self):
        """For the guard's own tests: refusals inside the block are not a test failure."""
        start = len(self.hits)
        try:
            yield
        finally:
            del self.hits[start:]
            self._reported = min(self._reported, start)

    def install(self) -> None:
        """Wrap the file, sqlite and subprocess entry points of this process."""
        global ACTIVE
        guard = self
        real_open, real_os_open = builtins.open, os.open
        real_replace, real_rename = os.replace, os.rename
        real_remove, real_unlink = os.remove, os.unlink
        real_connect, real_popen_init = sqlite3.connect, subprocess.Popen.__init__

        def open_(file, mode="r", *args, **kwargs):
            if isinstance(mode, str) and any(c in mode for c in "wax+"):
                guard.check_path("open for write", file)
            return real_open(file, mode, *args, **kwargs)

        def os_open(path, flags, *args, **kwargs):
            if flags & _WRITE_FLAGS and kwargs.get("dir_fd") is None:
                guard.check_path("os.open for write", path)
            return real_os_open(path, flags, *args, **kwargs)

        def two_paths(real, what):
            def wrapper(src, dst, *args, **kwargs):
                if kwargs.get("src_dir_fd") is None and kwargs.get("dst_dir_fd") is None:
                    guard.check_path(what, src)
                    guard.check_path(what, dst)
                return real(src, dst, *args, **kwargs)
            return wrapper

        def one_path(real, what):
            def wrapper(path, *args, **kwargs):
                if kwargs.get("dir_fd") is None:
                    guard.check_path(what, path)
                return real(path, *args, **kwargs)
            return wrapper

        def connect(database, *args, **kwargs):
            target = database
            if isinstance(target, str) and target.startswith("file:"):
                target = target[len("file:"):].split("?", 1)[0]
            guard.check_path("sqlite3.connect", target)
            return real_connect(database, *args, **kwargs)

        def popen_init(self_, args, *rest, **kwargs):
            guard.check_command(args, kwargs.get("executable"))
            return real_popen_init(self_, args, *rest, **kwargs)

        builtins.open = io.open = open_
        wrapped = {real_os_open: os_open,
                   real_replace: two_paths(real_replace, "replace"),
                   real_rename: two_paths(real_rename, "rename"),
                   real_remove: one_path(real_remove, "delete"),
                   real_unlink: one_path(real_unlink, "delete")}
        os.open, os.replace, os.rename, os.remove, os.unlink = wrapped.values()
        # Code that asks `os.unlink in os.supports_dir_fd` gets the same answer as before.
        for support in (os.supports_dir_fd, os.supports_fd, os.supports_follow_symlinks):
            support.update(w for real, w in wrapped.items() if real in support)
        sqlite3.connect = connect
        subprocess.Popen.__init__ = popen_init
        ACTIVE = self


# ── Watch-list (read-only, counts only) ──────────────────────────────────────

WATCHED_FILES = ("Documents/plan.md", "Desktop/scratch.md", "Downloads/note.txt")


def _stat_sig(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns)


def _tree_sig(path):
    """(inode, file count, total bytes) of a folder, or None if it is not there."""
    try:
        ino = os.stat(path).st_ino
    except OSError:
        return None
    files = size = 0
    for root, _dirs, names in os.walk(path):
        for n in names:
            try:
                size += os.stat(os.path.join(root, n)).st_size
                files += 1
            except OSError:
                pass
    return (ino, files, size)


def _digest(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def _is_test_named(entry: dict) -> bool:
    return any(isinstance(entry.get(k), str) and "test" in entry[k].lower()
               for k in ("agent_id", "agent"))


# What the inbox changes on an entry that is already there: it marks entries read
# and gives an entry written without an id an `auto_` one (routes/inbox.py). Both
# are left out of the fingerprint, so the owner reading his inbox during a run is
# not a change; a real id stays in.
def _stable(entry: dict) -> dict:
    return {k: v for k, v in entry.items()
            if k != "read" and not (k == "id" and str(v).startswith("auto_"))}


def _notification_sets(path) -> dict:
    """Fingerprints of the manual shift reports, the scheduled (time or idle) ones
    and the test-named entries; None each when the list cannot be read."""
    try:
        with open(path) as f:
            entries = json.load(f)
        if not isinstance(entries, list):
            raise ValueError("not a list")
    except (OSError, ValueError):
        return {"manual": None, "scheduled": None, "test_named": None}
    sets = {"manual": set(), "scheduled": set(), "test_named": set()}
    for e in entries:
        if not isinstance(e, dict):
            continue
        fp = hashlib.sha256(json.dumps(_stable(e), sort_keys=True,
                                       default=str).encode()).hexdigest()
        if e.get("type") == "shift_report":
            sets["manual" if e.get("trigger_kind") == "manual" else "scheduled"].add(fp)
        if _is_test_named(e):
            sets["test_named"].add(fp)
    return sets


def watch_snapshot(home=None) -> dict:
    """Read-only look at the named hazards under `home` (default: the real home)."""
    home = os.path.expanduser("~") if home is None else str(home)
    codec = os.path.join(home, ".codec")
    snap = {
        "files": [_stat_sig(os.path.join(home, rel)) for rel in WATCHED_FILES],
        "test_agent": _tree_sig(os.path.join(codec, "agents", "test_agent")),
        "state": _digest(os.path.join(codec, "shift_report_state.json")),
    }
    snap.update(_notification_sets(os.path.join(codec, "notifications.json")))
    return snap


def watch_changes(before: dict, after: dict) -> list:
    """What changed between two snapshots, as count-only sentences (no file names)."""
    out = []
    moved = sum(1 for b, a in zip(before["files"], after["files"]) if b != a)
    if moved:
        out.append(f"{moved} of {len(WATCHED_FILES)} watched files in Documents, Desktop "
                   "and Downloads were created, changed or removed")
    b, a = before["test_agent"], after["test_agent"]
    if a is not None and (b is None or a[0] != b[0] or a[1] > b[1] or a[2] > b[2]):
        was = "absent" if b is None else f"{b[1]} files, {b[2]} bytes"
        out.append(f"the test agent folder was created, recreated or grew ({was} -> "
                   f"{a[1]} files, {a[2]} bytes)")
    for key, label in (("manual", "manual shift report"), ("test_named", "test-named")):
        if before[key] is not None and after[key] is not None:
            new = len(after[key] - before[key])
            if new:
                out.append(f"{new} new or changed {label} notification(s)")
    scheduled_fired = (before["scheduled"] is None or after["scheduled"] is None
                       or bool(after["scheduled"] - before["scheduled"]))
    if not scheduled_fired and before["state"] != after["state"]:
        out.append("the shift report state changed while no scheduled report fired")
    return out
