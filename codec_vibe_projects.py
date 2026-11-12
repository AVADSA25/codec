"""Vibe projects: a folder of files with checkpoints (docs/VIBE-PROJECTS-DESIGN.md).

A project is a plain folder under ~/codec-workspace/vibe/<id>/. Next to its files
sits a hidden .vibe/ folder that holds the project's settings and its checkpoints:

    .vibe/project.json        title, created and updated times
    .vibe/checkpoints.jsonl   one line per checkpoint (id, kind, label, file -> hash)
    .vibe/objects/<sha256>    the content of every file version, stored once

A checkpoint is taken before and after every AI change. Restoring one writes its
files back and takes a new checkpoint; nothing is rewritten or deleted, so a
restore can itself be undone. No git is needed (customer Macs may not have it).

An attached project (P-050, docs/VIBE-ATTACH-FOLDER-DESIGN.md) is an existing folder the owner typed at this Mac.
Its .vibe record stays under the vibe root (~/codec-workspace/vibe/<id>/.vibe/attached.json points at the folder),
so nothing is written into the folder except the files the owner or the AI change. record_dir() is where .vibe is,
files_dir() is where the files are; for a created project both are the old project_dir(), and every created-project
path below is the code it was before. project_dir() still answers only for created projects, so code that was not
taught about attached folders cannot reach one.

Everything here is plain files and the standard library. Callers outside this
module only see VibeError (status + message) for anything the user can fix.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import posixpath
import re
import secrets
import shutil
import stat
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import codec_vibe_attach as va

META_DIR = ".vibe"
MAX_FILE_BYTES = 1_048_576          # 1 MB per file
MAX_FILES = 200                     # per project
MAX_BLOCKS = 50                     # files in one AI reply
MAX_PATH_LEN = 200
MAX_DEPTH = 8
MAX_DIFF_CHARS = 20_000
MAX_DIFF_FILES = 50

# Folders no write may land in; they are never part of a project's files.
_WRITE_BLOCKED_SEGMENTS = {META_DIR, ".git", "node_modules", ".ssh", ".gnupg", ".aws", ".kube", ".gcloud"}
# Folders and files that editors, git tools and AI tools later run code from or take orders from (tasks, hooks,
# workflows, tool settings). The owner may edit them; the agent's file tools may not (owner decision D-23,
# docs/VIBE-SANDBOX-NARROW-DESIGN.md). Matched as any path segment, case-insensitively.
_AGENT_BLOCKED_SEGMENTS = {".vscode", ".idea", ".husky", ".devcontainer", ".github", ".mcp.json", ".claude"}
# The owner's project notes files at the project root: the agent reads them, never changes them (P-046,
# docs/VIBE-AGENT-NOTES-DESIGN.md). Matched case-insensitively; the owner's own edits are unchanged.
_AGENT_NOTES_FILES = {"agents.md", "claude.md"}
# Folders a scan skips (listing, checkpoints).
_SCAN_IGNORED_DIRS = {META_DIR, ".git", "node_modules", "__pycache__", ".venv", "venv", ".next", ".cache",
                      ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",      # what test runs leave behind
                      "dist", "build", "target", "coverage"}                      # what builds leave behind (P-077)
_BLOCKED_NAME_PREFIXES = (".env", "credentials", ".netrc", ".npmrc", ".pypirc", "id_rsa", "id_ed25519", "id_dsa", ".htpasswd")
_BLOCKED_EXTS = (".pem", ".key", ".p12", ".pfx", ".keystore")
_TMP_SUFFIX = ".vibe-tmp"
_IGNORED_FILES = {".ds_store"}

ATTACHED_META = "attached.json"      # an attached project's record (P-050); a created one has project.json
STAT_CACHE = "statcache.json"       # an attached project's path -> [size, mtime_ns, ctime_ns, inode, sha256]
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_READ_ONLY = (" Vibe is read-only in this folder until it is back under: reading and chat still work; edits, AI "
              "changes, agent runs, restores and commands wait. Move files out, or attach a subfolder instead.")

_PID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,60}$")
_CID_RE = re.compile(r"^c\d{4,}$")
_KINDS = {"start", "before", "ai", "restore", "before_restore", "manual"}

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_AGENT_RUNS: dict[str, str] = {}    # project id -> run id, while an agent run holds the project
_ATTACH_LOCK = threading.Lock()     # one attach at a time, so a folder cannot get two records


class VibeError(Exception):
    """Something the caller can fix. status is an HTTP status code."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# ── locations ────────────────────────────────────────────────────────────

def vibe_root() -> Path:
    return Path(os.environ.get("CODEC_VIBE_ROOT") or os.path.expanduser("~/codec-workspace/vibe"))


def trash_root() -> Path:
    return Path(os.environ.get("CODEC_VIBE_TRASH") or os.path.expanduser("~/.codec/trash/vibe"))


def enabled() -> bool:
    return os.environ.get("VIBE_PROJECTS_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


def _lock(pid: str) -> threading.RLock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(pid, threading.RLock())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _audit(event: str, message: str, extra: dict) -> None:
    """Ids and counts only. Never a label, a file name or any code."""
    try:
        from codec_audit import log_event
        log_event(event, "codec-vibe", message, extra=extra)
    except Exception:
        pass


# ── an agent run holds its project while it works (docs/VIBE-AGENT-DESIGN.md) ──

def agent_run_active(pid: str) -> str | None:
    """The id of the agent run that holds this project, or None."""
    with _LOCKS_GUARD:
        return _AGENT_RUNS.get(pid)


def agent_claim(pid: str, run_id: str) -> None:
    """Hold the project for one run. While held, every other writer gets a 409. An attached folder must be there and
    inside its limits (R-11)."""
    ctx = _ctx(pid)
    if ctx.attached:
        _not_over(ctx)
    with _lock(pid):
        with _LOCKS_GUARD:
            if pid in _AGENT_RUNS:
                raise VibeError(409, "The agent is already working on this project")
            _AGENT_RUNS[pid] = run_id


def agent_release(pid: str, run_id: str) -> None:
    with _LOCKS_GUARD:
        if _AGENT_RUNS.get(pid) == run_id:
            del _AGENT_RUNS[pid]


def _refuse_during_agent_run(pid: str) -> None:
    """Called inside the project lock by every writer except the agent's own helpers."""
    if agent_run_active(pid):
        raise VibeError(409, "The agent is working on this project. Stop it first.")


# ── paths ────────────────────────────────────────────────────────────────

def project_dir(pid: str) -> Path:
    if not isinstance(pid, str) or not _PID_RE.match(pid):
        raise VibeError(400, "Bad project id")
    root = vibe_root()
    pdir = root / pid
    if not (pdir / META_DIR / "project.json").is_file():
        raise VibeError(404, "No such project")
    real_root = os.path.realpath(root)
    if os.path.realpath(pdir) != os.path.join(real_root, pid):
        raise VibeError(404, "No such project")
    return pdir


def attach_enabled() -> bool:
    return va.enabled()


def _is_attached_dir(rdir: Path) -> bool:
    """A record made by attach_folder: .vibe/attached.json and no .vibe/project.json."""
    meta = rdir / META_DIR
    return (not (meta / "project.json").is_file() and (meta / ATTACHED_META).is_file()
            and not os.path.islink(meta / ATTACHED_META))


def record_dir(pid: str) -> Path:
    """Where the project's .vibe folder is: project_dir() for a created project, the record of an attached one."""
    if not isinstance(pid, str) or not _PID_RE.match(pid):
        raise VibeError(400, "Bad project id")
    root = vibe_root()
    rdir = root / pid
    if not (rdir / META_DIR / "project.json").is_file() and not (va.enabled() and _is_attached_dir(rdir)):
        raise VibeError(404, "No such project")
    real_root = os.path.realpath(root)
    if os.path.realpath(rdir) != os.path.join(real_root, pid):
        raise VibeError(404, "No such project")
    return rdir


def is_attached(pid: str) -> bool:
    try:
        return _is_attached_dir(record_dir(pid))
    except VibeError:
        return False


def folder_notes(pid: str) -> bool:
    """True when the owner ticked "use this folder's notes" when attaching (R-16). False for a created project."""
    try:
        rdir = record_dir(pid)
    except VibeError:
        return False
    return _is_attached_dir(rdir) and _read_meta(rdir).get("folder_notes") is True


def _attached_folder(rdir: Path) -> Path:
    """The attached folder, the folder rules checked again (R-10): 410 when it is gone, 409 when it is refused now."""
    path = _read_meta(rdir).get("path")
    try:
        return Path(va.recheck(path))
    except va.Refused as e:
        gone = not isinstance(path, str) or not os.path.lexists(path)
        raise VibeError(410 if gone else 409, str(e) + " Detach it from Vibe; the folder itself is not touched.") from None


def files_dir(pid: str) -> Path:
    """Where the project's files are: project_dir() for a created project, the attached folder otherwise."""
    rdir = record_dir(pid)
    return _attached_folder(rdir) if _is_attached_dir(rdir) else rdir


class _Ctx:
    """One project: its record (.vibe), its files, and whether it is an attached folder."""
    __slots__ = ("pid", "rdir", "fdir", "attached")

    def __init__(self, pid: str, rdir: Path, fdir: Path, attached: bool):
        self.pid, self.rdir, self.fdir, self.attached = pid, rdir, fdir, attached


def _ctx(pid: str) -> _Ctx:
    rdir = record_dir(pid)
    if _is_attached_dir(rdir):
        return _Ctx(pid, rdir, _attached_folder(rdir), True)
    return _Ctx(pid, rdir, rdir, False)


def looks_secret(rel: str) -> bool:
    """True when the file name looks like a secret (.env, keys, credentials). Same list safe_rel uses."""
    base = str(rel).rsplit("/", 1)[-1].lower()
    return base.startswith(_BLOCKED_NAME_PREFIXES) or base.endswith(_BLOCKED_EXTS)


def safe_rel(rel: str) -> str:
    """Validate a path inside a project and return it normalised (posix, no leading ./)."""
    return _check_rel(rel, MAX_PATH_LEN, MAX_DEPTH)


def _attached_rel(rel: str) -> str:
    """safe_rel with an attached folder's longer paths and deeper levels (R-11)."""
    return _check_rel(rel, va.MAX_PATH_LEN, va.MAX_DEPTH)


def safe_rel_for(pid: str, rel: str) -> str:
    """safe_rel for this project: an attached folder allows its longer, deeper paths."""
    return _attached_rel(rel) if is_attached(pid) else safe_rel(rel)


def _check_rel(rel: str, max_len: int, max_depth: int) -> str:
    if not isinstance(rel, str) or not rel.strip() or len(rel) > max_len:
        raise VibeError(400, "Bad file path")
    if "\x00" in rel or "\\" in rel or rel.startswith(("/", "~")) or any(ord(c) < 32 for c in rel):
        raise VibeError(400, "Bad file path")
    norm = posixpath.normpath(rel.strip())
    if norm in (".", "") or norm == ".." or norm.startswith("../") or norm.startswith("/"):
        raise VibeError(400, "The path leaves the project")
    parts = norm.split("/")
    if len(parts) > max_depth:
        raise VibeError(400, "The path is too deep")
    for seg in parts:
        low = seg.lower()
        if low in _WRITE_BLOCKED_SEGMENTS or low.startswith(META_DIR):
            raise VibeError(400, f"Writing to '{seg}' is not allowed")
        if low.endswith(_TMP_SUFFIX):
            raise VibeError(400, "Bad file name")
    base = parts[-1].lower()
    if base.startswith(_BLOCKED_NAME_PREFIXES) or base.endswith(_BLOCKED_EXTS):
        raise VibeError(400, f"'{parts[-1]}' looks like a secret; Vibe will not write it")
    return norm


def _no_symlinks(pdir: Path, rel: str, *, include_last: bool) -> None:
    cur = pdir
    parts = rel.split("/")
    for i, seg in enumerate(parts):
        cur = cur / seg
        last = i == len(parts) - 1
        if (not last or include_last) and os.path.islink(cur):
            raise VibeError(400, "Links are not allowed inside a project")
    real_p = os.path.realpath(pdir)
    real_t = os.path.realpath(pdir / rel)
    if real_t != real_p and not real_t.startswith(real_p + os.sep):
        raise VibeError(400, "The path leaves the project")


def _ignored_name(name: str) -> bool:
    low = name.lower()
    return low in _IGNORED_FILES or low.endswith(_TMP_SUFFIX)


# ── scanning ─────────────────────────────────────────────────────────────

def _walk(pdir: Path):
    """Every tracked file as (path inside the project, full path, size). Skips links, ignored
    folders, temporary files and files over 1 MB."""
    base = str(pdir)
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        dirnames[:] = [d for d in dirnames
                       if d.lower() not in _SCAN_IGNORED_DIRS and not os.path.islink(os.path.join(dirpath, d))]
        for name in filenames:
            full = os.path.join(dirpath, name)
            if _ignored_name(name) or os.path.islink(full):
                continue
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            if size > MAX_FILE_BYTES:
                continue
            yield os.path.relpath(full, base).replace(os.sep, "/"), full, size


def _count_files(pdir: Path) -> int:
    return sum(1 for _ in _walk(pdir))


def _scan(pdir: Path) -> dict[str, tuple[int, str]]:
    """Every tracked file: path -> (size, sha256)."""
    out: dict[str, tuple[int, str]] = {}
    for rel, full, _size in _walk(pdir):
        try:
            with open(full, "rb") as f:
                data = f.read()
        except OSError:
            continue
        out[rel] = (len(data), hashlib.sha256(data).hexdigest())
        if len(out) > MAX_FILES:
            raise VibeError(413, f"A project can hold at most {MAX_FILES} files")
    return out


# ── an attached folder: names and stat only, a stat cache, limits (P-050, R-11, R-12, R-14) ──

class _Tree:
    """An attached folder's tracked files as (path, lstat), their bytes, and the first limit passed (or None)."""
    __slots__ = ("files", "bytes", "over")

    def __init__(self):
        self.files: list[tuple[str, os.stat_result]] = []
        self.bytes = 0
        self.over: str | None = None


def _walk_attached(fdir: Path) -> _Tree:
    """Every tracked file of an attached folder, by name and stat only: no file is opened (R-12). Leaves out what
    _walk leaves out, and also every .git entry (file or folder) at any depth, secret-looking files (R-14) and paths
    Vibe would refuse to write. Stops at the first limit it passes (R-11) and names it in .over."""
    tree = _Tree()
    base = str(fdir)
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames
                             if d.lower() not in _SCAN_IGNORED_DIRS and not os.path.islink(os.path.join(dirpath, d)))
        for name in sorted(filenames):
            if _ignored_name(name) or name.lower() == ".git" or looks_secret(name):
                continue
            full = os.path.join(dirpath, name)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_FILE_BYTES or not os.access(full, os.R_OK):
                continue
            rel = os.path.relpath(full, base).replace(os.sep, "/")
            if rel.count("/") + 1 > va.MAX_DEPTH:
                tree.over = f"This folder has files more than {va.MAX_DEPTH} levels deep."
                return tree
            try:
                _attached_rel(rel)
            except VibeError:
                continue
            tree.files.append((rel, st))
            tree.bytes += st.st_size
            if len(tree.files) > va.MAX_FILES:
                tree.over = f"This folder has more than {va.MAX_FILES:,} files Vibe would keep versions of."
                return tree
            if tree.bytes > va.MAX_BYTES:
                tree.over = f"The files Vibe would keep versions of come to more than {va.MAX_BYTES // 1048576} MB."
                return tree
    return tree


def _not_over(ctx: _Ctx, tree: _Tree | None = None) -> _Tree:
    """The folder's tree, or 413 when it is past a limit: the project is read-only until it is back under (R-11)."""
    tree = tree or _walk_attached(ctx.fdir)
    if tree.over:
        raise VibeError(413, tree.over + _READ_ONLY)
    return tree


def _room(ctx: _Ctx, rel: str, size: int, tree: _Tree | None = None) -> _Tree:
    """Refuse a write that would take the folder past a limit. `rel` is a checked path."""
    tree = _not_over(ctx, tree)
    old = next((st.st_size for r, st in tree.files if r == rel), None)
    if old is None and len(tree.files) >= va.MAX_FILES:
        raise VibeError(413, f"An attached folder can hold at most {va.MAX_FILES:,} files Vibe keeps versions of")
    if tree.bytes - (old or 0) + size > va.MAX_BYTES:
        raise VibeError(413, f"The files of an attached folder can come to at most {va.MAX_BYTES // 1048576} MB")
    return tree


def _read_nofollow(path) -> bytes:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as f:
        return f.read(MAX_FILE_BYTES + 1)


def _stat_key(st: os.stat_result) -> list:
    # ctime is in the key because a command can set mtime back (touch -r) but not ctime
    return [st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino]


def _load_stat_cache(rdir: Path) -> dict:
    try:
        raw = json.loads((rdir / META_DIR / STAT_CACHE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _scan_attached(rdir: Path, fdir: Path, tree: _Tree | None = None) -> dict[str, tuple[int, str]]:
    """_scan for an attached folder: path -> (size, sha256). A file is read only when its stat differs from the stat
    cache in the record (R-12); 413 when the folder is past a limit."""
    tree = tree or _walk_attached(fdir)
    if tree.over:
        raise VibeError(413, tree.over + _READ_ONLY)
    cache = _load_stat_cache(rdir)
    fresh: dict[str, list] = {}
    out: dict[str, tuple[int, str]] = {}
    for rel, st in tree.files:
        key = _stat_key(st)
        ent = cache.get(rel)
        if (isinstance(ent, list) and len(ent) == 5 and ent[:4] == key and isinstance(ent[4], str)
                and _SHA_RE.match(ent[4])):
            out[rel] = (st.st_size, ent[4])
            fresh[rel] = ent
            continue
        try:
            fd = os.open(fdir / rel, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        except OSError:
            continue
        with os.fdopen(fd, "rb") as f:
            fst = os.fstat(f.fileno())      # before the read: a write during it changes ctime, so the next scan reads again
            if not stat.S_ISREG(fst.st_mode):
                continue
            data = f.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            continue
        sha = hashlib.sha256(data).hexdigest()
        out[rel] = (len(data), sha)
        fresh[rel] = _stat_key(fst) + [sha]
    if fresh != cache:
        _write_json_atomic(rdir / META_DIR / STAT_CACHE, fresh)
    return out


def _scan_ctx(ctx: _Ctx) -> dict[str, tuple[int, str]]:
    return _scan_attached(ctx.rdir, ctx.fdir) if ctx.attached else _scan(ctx.rdir)


def list_files(pid: str) -> list[dict]:
    """Names and sizes, without reading any file (P-066): the same files _scan finds, and the same 413. An attached
    folder past its limits lists what it walked before the limit and does not raise (it is read-only, R-11)."""
    ctx = _ctx(pid)
    if ctx.attached:
        return sorted(({"path": rel, "size": st.st_size} for rel, st in _walk_attached(ctx.fdir).files),
                      key=lambda f: f["path"])
    pdir = ctx.rdir
    out: list[dict] = []
    for rel, full, size in _walk(pdir):
        if not os.access(full, os.R_OK):            # _scan leaves out a file it cannot open
            continue
        out.append({"path": rel, "size": size})
        if len(out) > MAX_FILES:
            raise VibeError(413, f"A project can hold at most {MAX_FILES} files")
    return sorted(out, key=lambda f: f["path"])


# ── storage ──────────────────────────────────────────────────────────────

def _meta_path(pdir: Path) -> Path:
    att = pdir / META_DIR / ATTACHED_META
    if not (pdir / META_DIR / "project.json").exists() and att.is_file():
        return att                  # an attached project's record (P-050)
    return pdir / META_DIR / "project.json"


def _objects(pdir: Path) -> Path:
    return pdir / META_DIR / "objects"


def _cp_path(pdir: Path) -> Path:
    return pdir / META_DIR / "checkpoints.jsonl"


def _read_meta(pdir: Path) -> dict:
    try:
        return json.loads(_meta_path(pdir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_json_atomic(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _store_blob(pdir: Path, sha: str, data: bytes) -> None:
    dest = _objects(pdir) / sha
    if dest.exists():
        return
    tmp = dest.with_name(sha + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, dest)


def _blob(pdir: Path, sha: str) -> bytes:
    try:
        return (_objects(pdir) / sha).read_bytes()
    except OSError:
        raise VibeError(500, "A stored file version is missing")


def _load_checkpoints(pdir: Path) -> list[dict]:
    out: list[dict] = []
    try:
        with open(_cp_path(pdir), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and isinstance(rec.get("files"), dict) and _CID_RE.match(str(rec.get("id", ""))):
                    out.append(rec)
    except OSError:
        pass
    return out


def _append_checkpoint(pdir: Path, rec: dict) -> None:
    with open(_cp_path(pdir), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _unified(old: str, new: str, path: str) -> str:
    return "".join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                        fromfile=f"a/{path}", tofile=f"b/{path}", n=3))


def _count_lines(diff: str) -> tuple[int, int]:
    add = rem = 0
    for ln in diff.splitlines():
        if ln.startswith("+++") or ln.startswith("---"):
            continue
        if ln.startswith("+"):
            add += 1
        elif ln.startswith("-"):
            rem += 1
    return add, rem


def _changes(pdir: Path, old: dict[str, str], new: dict[str, str]) -> list[dict]:
    """Per-file change list between two manifests (path -> sha): status and lines added / removed."""
    out = []
    for path in sorted(set(old) | set(new)):
        o, n = old.get(path), new.get(path)
        if o == n:
            continue
        status = "added" if o is None else "removed" if n is None else "changed"
        added = removed = 0
        try:
            ot = _text(_blob(pdir, o)) if o else ""
            nt = _text(_blob(pdir, n)) if n else ""
            if len(ot) <= 200_000 and len(nt) <= 200_000:
                added, removed = _count_lines(_unified(ot, nt, path))
        except VibeError:
            pass
        out.append({"path": path, "status": status, "added": added, "removed": removed})
    return out


def _stat(changes: list[dict]) -> dict:
    return {
        "added": sum(1 for c in changes if c["status"] == "added"),
        "changed": sum(1 for c in changes if c["status"] == "changed"),
        "removed": sum(1 for c in changes if c["status"] == "removed"),
        "lines_added": sum(c["added"] for c in changes),
        "lines_removed": sum(c["removed"] for c in changes),
    }


def _manifest(pdir: Path) -> dict[str, str]:
    return {p: h for p, (_s, h) in _scan(pdir).items()}


def _snapshot_locked(pid: str, pdir: Path, label: str, kind: str, *, force: bool = False, extra: dict | None = None,
                     fdir: Path | None = None) -> dict | None:
    """pdir is the project's record. fdir is an attached folder's files (P-050); None for a created project."""
    if kind not in _KINDS:
        raise VibeError(400, "Bad checkpoint kind")
    scan = _scan(pdir) if fdir is None else _scan_attached(pdir, fdir)
    manifest = {p: h for p, (_s, h) in scan.items()}
    cps = _load_checkpoints(pdir)
    last = cps[-1] if cps else None
    if last is not None and not force and last["files"] == manifest:
        return None
    old = last["files"] if last else {}
    for path, (_s, sha) in scan.items():
        if not (_objects(pdir) / sha).exists():
            try:
                data = (pdir / path).read_bytes() if fdir is None else _read_nofollow(fdir / path)
            except OSError:
                raise VibeError(500, "A file changed while saving a checkpoint; try again")
            if hashlib.sha256(data).hexdigest() != sha:
                raise VibeError(500, "A file changed while saving a checkpoint; try again")
            _store_blob(pdir, sha, data)
    changes = _changes(pdir, old, manifest)
    seq = (last["seq"] + 1) if last else 1
    rec = {
        "id": f"c{seq:04d}", "seq": seq, "ts": _now(), "kind": kind, "label": str(label or "")[:200],
        "parent": last["id"] if last else None, "files": manifest, "stat": _stat(changes),
        "changed": [c["path"] for c in changes][:40],
    }
    if extra:
        rec.update(extra)
    _append_checkpoint(pdir, rec)
    meta = _read_meta(pdir)
    meta["updated_at"] = rec["ts"]
    _write_json_atomic(_meta_path(pdir), meta)
    _audit("vibe_checkpoint", "Vibe checkpoint saved", {
        "project": pid, "checkpoint": rec["id"], "kind": kind, "files": len(manifest),
        "files_changed": len(changes), "lines_added": rec["stat"]["lines_added"], "lines_removed": rec["stat"]["lines_removed"]})
    return rec


def _public(rec: dict) -> dict:
    """A checkpoint without its full file map."""
    out = {k: v for k, v in rec.items() if k != "files"}
    out["files_count"] = len(rec.get("files", {}))
    return out


# ── projects ─────────────────────────────────────────────────────────────

def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(title or "").lower()).strip("-")[:32].strip("-")
    return s or "project"


def create_project(title: str, files: list[dict] | None = None) -> dict:
    title = re.sub(r"\s+", " ", str(title or "").strip())[:80] or "Untitled project"
    root = vibe_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    for _ in range(20):
        pid = f"{_slug(title)}-{secrets.token_hex(3)}"
        if not (root / pid).exists():
            break
    else:
        raise VibeError(500, "Could not name the project")
    pdir = root / pid
    prepared = _prepare_blocks(files or [], existing=set())
    pdir.mkdir(mode=0o700)
    (pdir / META_DIR / "objects").mkdir(parents=True, mode=0o700)
    _write_json_atomic(_meta_path(pdir), {"id": pid, "title": title, "created_at": _now(), "updated_at": _now()})
    with _lock(pid):
        try:
            for rel, content in prepared:
                _write_file_locked(pdir, rel, content)
            _snapshot_locked(pid, pdir, "Start", "start", force=True)
        except Exception:
            shutil.rmtree(pdir, ignore_errors=True)
            raise
    return project_info(pid)


def list_projects(limit: int = 100) -> list[dict]:
    root = vibe_root()
    out = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    for name in names:
        if not _PID_RE.match(name):
            continue
        attached = _is_attached_dir(root / name)
        if attached and not va.enabled():
            continue                # switched off: attached records are neither listed nor opened
        meta = _read_meta(root / name)
        if meta.get("id") == name:
            out.append({"id": name, "title": meta.get("title", name), "created_at": meta.get("created_at", ""),
                        "updated_at": meta.get("updated_at", "")})
            if attached:
                out[-1]["attached"] = True
    out.sort(key=lambda m: m["updated_at"], reverse=True)
    return out[:max(1, min(int(limit), 200))]


def project_info(pid: str) -> dict:
    pdir = record_dir(pid)
    if _is_attached_dir(pdir):
        return _attached_info(pid, pdir)
    meta = _read_meta(pdir)
    cps = _load_checkpoints(pdir)
    return {
        "id": pid, "title": meta.get("title", pid), "created_at": meta.get("created_at", ""),
        "updated_at": meta.get("updated_at", ""), "files": list_files(pid),
        "checkpoint_count": len(cps), "last_checkpoint": _public(cps[-1]) if cps else None,
        "can_undo": _undo_target(cps) is not None,
    }


def _trash_dest(pid: str) -> Path:
    dest_root = trash_root()
    dest_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    dest = dest_root / f"{pid}-{time.strftime('%Y%m%d-%H%M%S')}"
    n = 1
    while dest.exists():
        n += 1
        dest = dest_root / f"{pid}-{time.strftime('%Y%m%d-%H%M%S')}-{n}"
    return dest


def trash_project(pid: str) -> dict:
    """A created project's folder goes to the trash. An attached one is detached: only its record moves (R-18)."""
    pdir = record_dir(pid)
    if _is_attached_dir(pdir):
        return detach_project(pid)
    with _lock(pid):
        _refuse_during_agent_run(pid)
        dest = _trash_dest(pid)
        shutil.move(str(pdir), str(dest))
    _audit("vibe_project_trash", "Vibe project moved to the trash", {"project": pid})
    return {"ok": True, "trash": str(dest)}


# ── attach and detach an existing folder (P-050, docs/VIBE-ATTACH-FOLDER-DESIGN.md) ──

def _attached_records() -> dict[str, str]:
    """The real path of every attached folder -> its project id."""
    root = vibe_root()
    out: dict[str, str] = {}
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for name in names:
        if _PID_RE.match(name) and _is_attached_dir(root / name):
            path = _read_meta(root / name).get("path")
            if isinstance(path, str):
                out[path] = name
    return out


def attach_folder(path, folder_notes=False) -> dict:
    """Attach the folder the owner typed (R-10). Nothing is written into it: the record goes under the vibe root (R-09)
    and holds no version until the first change (R-12). A folder that already has a record opens that record.
    Called only by the Mac-only route POST /api/vibe/projects/attach, never by a skill, an agent or a file."""
    if not va.enabled():
        raise VibeError(404, "Attaching folders is switched off")
    if not isinstance(folder_notes, bool):
        raise VibeError(400, "folder_notes must be true or false")
    with _ATTACH_LOCK:
        known = _attached_records()
        try:
            real = va.check_folder(path, others=list(known))
        except va.Refused as e:
            raise VibeError(400, str(e)) from None
        if real in known:
            info = project_info(known[real])
            info["already_attached"] = True
            return info
        tree = _walk_attached(Path(real))
        if tree.over:
            raise VibeError(413, tree.over + " Attach a subfolder instead.")
        root = vibe_root()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        title = re.sub(r"\s+", " ", os.path.basename(real)).strip()[:80] or "Attached folder"
        for _ in range(20):
            pid = f"{_slug(title)}-{secrets.token_hex(3)}"
            if not (root / pid).exists():
                break
        else:
            raise VibeError(500, "Could not name the project")
        rdir = root / pid
        rdir.mkdir(mode=0o700)
        try:
            (rdir / META_DIR / "objects").mkdir(parents=True, mode=0o700)
            # Last: until attached.json exists the folder is not a record, so a failed attach is never listed.
            _write_json_atomic(rdir / META_DIR / ATTACHED_META, {
                "id": pid, "title": title, "path": real, "folder_notes": folder_notes,
                "created_at": _now(), "updated_at": _now()})
        except Exception:
            shutil.rmtree(rdir, ignore_errors=True)
            raise
    _audit("vibe_project_attach", "Vibe attached a folder", {"project": pid, "files": len(tree.files),
                                                             "folder_notes": folder_notes})
    return project_info(pid)


def detach_project(pid: str) -> dict:
    """Vibe forgets an attached folder (R-18): its record (versions, run logs, allowed commands) moves to the trash;
    nothing in the folder is moved, renamed or deleted. 409 while an agent run holds it; what it serves or runs on this
    Mac is stopped first. Attaching the folder again starts a fresh record."""
    rdir = record_dir(pid)
    if not _is_attached_dir(rdir):
        raise VibeError(400, "Only an attached folder can be detached")
    with _lock(pid):
        _refuse_during_agent_run(pid)
        try:
            import codec_vibe_runner as vr
            vr.stop(pid)
        except VibeError:
            pass
        checkpoints = len(_load_checkpoints(rdir))
        try:
            runs = sum(1 for n in os.listdir(rdir / META_DIR / "runs") if n.endswith(".jsonl"))
        except OSError:
            runs = 0
        dest = _trash_dest(pid)
        shutil.move(str(rdir), str(dest))
    _audit("vibe_project_detach", "Vibe detached a folder", {"project": pid, "checkpoints": checkpoints, "runs": runs})
    return {"ok": True, "detached": True, "trash": str(dest)}


def _attached_info(pid: str, rdir: Path) -> dict:
    """project_info for an attached project, also when its folder is gone (so the page can offer Detach)."""
    meta = _read_meta(rdir)
    cps = _load_checkpoints(rdir)
    att = {"path": meta.get("path") if isinstance(meta.get("path"), str) else "", "folder_notes": meta.get("folder_notes") is True,
           "read_only": None, "missing": None,
           "limits": {"files": va.MAX_FILES, "megabytes": va.MAX_BYTES // 1048576, "depth": va.MAX_DEPTH}}
    files: list[dict] = []
    try:
        tree = _walk_attached(_attached_folder(rdir))
        files = sorted(({"path": rel, "size": st.st_size} for rel, st in tree.files), key=lambda f: f["path"])
        if tree.over:
            att["read_only"] = tree.over + _READ_ONLY
    except VibeError as e:
        att["missing"] = e.message
    return {
        "id": pid, "title": meta.get("title", pid), "created_at": meta.get("created_at", ""),
        "updated_at": meta.get("updated_at", ""), "files": files,
        "checkpoint_count": len(cps), "last_checkpoint": _public(cps[-1]) if cps else None,
        "can_undo": _undo_target(cps) is not None, "attached": att,
    }


# ── files ────────────────────────────────────────────────────────────────

def _check_content(content) -> bytes:
    if not isinstance(content, str):
        raise VibeError(400, "File content must be text")
    data = content.encode("utf-8")
    if len(data) > MAX_FILE_BYTES:
        raise VibeError(413, "A file can be at most 1 MB")
    return data


def _write_bytes_locked(pdir: Path, rel: str, data: bytes, *, attached: bool = False) -> int:
    """The one place a project file is written: checked path, no links, atomic replace. pdir is where the files are.
    attached: an attached folder's file (P-050): its longer paths, its own limits (checked by the caller with _room),
    and a file that is replaced keeps its permission bits (a script stays executable)."""
    rel = _attached_rel(rel) if attached else safe_rel(rel)
    _no_symlinks(pdir, rel, include_last=True)
    target = pdir / rel
    if target.is_dir():
        raise VibeError(400, "That path is a folder")
    if not attached and not target.exists() and _count_files(pdir) >= MAX_FILES:
        raise VibeError(413, f"A project can hold at most {MAX_FILES} files")
    keep_mode = None
    if attached:
        try:
            st = os.lstat(target)
            if not stat.S_ISREG(st.st_mode):
                raise VibeError(400, "That path is not a plain file")
            keep_mode = stat.S_IMODE(st.st_mode)
        except FileNotFoundError:
            pass
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _no_symlinks(pdir, rel, include_last=True)
    tmp = target.with_name(target.name + _TMP_SUFFIX)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
            if keep_mode is not None:
                os.fchmod(f.fileno(), keep_mode)
        os.replace(tmp, target)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return len(data)


def _write_file_locked(pdir: Path, rel: str, content: str, *, attached: bool = False) -> int:
    return _write_bytes_locked(pdir, rel, _check_content(content), attached=attached)


def _ensure_start_locked(ctx: _Ctx) -> None:
    """An attached folder's first version is taken at its first change (R-12): the files as they are before it."""
    if not _load_checkpoints(ctx.rdir):
        _snapshot_locked(ctx.pid, ctx.rdir, "Start", "start", force=True, fdir=ctx.fdir)


def write_file(pid: str, rel: str, content: str) -> dict:
    ctx = _ctx(pid)
    if ctx.attached:
        return _write_file_attached(ctx, rel, content)
    pdir = project_dir(pid)
    with _lock(pid):
        _refuse_during_agent_run(pid)
        size = _write_file_locked(pdir, rel, content)
    return {"path": safe_rel(rel), "size": size}


def _write_file_attached(ctx: _Ctx, rel: str, content: str) -> dict:
    """The owner's editor in an attached folder: free as in a created project (D-23 names allowed), within the
    folder's limits; the first version is taken before the first save."""
    rel = _attached_rel(rel)
    data = _check_content(content)
    with _lock(ctx.pid):
        _refuse_during_agent_run(ctx.pid)
        _room(ctx, rel, len(data))
        _ensure_start_locked(ctx)
        size = _write_bytes_locked(ctx.fdir, rel, data, attached=True)
    return {"path": rel, "size": size}


def _read_attached(ctx: _Ctx, rel: str) -> dict:
    rel = _attached_rel(rel)
    _no_symlinks(ctx.fdir, rel, include_last=True)
    target = ctx.fdir / rel
    try:
        st = os.lstat(target)
    except OSError:
        raise VibeError(404, "No such file") from None
    if not stat.S_ISREG(st.st_mode):
        raise VibeError(404, "No such file")
    if st.st_size > MAX_FILE_BYTES:
        return {"path": rel, "size": st.st_size, "too_large": True}
    try:
        data = _read_nofollow(target)
    except OSError:
        raise VibeError(404, "No such file") from None
    if len(data) > MAX_FILE_BYTES:
        return {"path": rel, "size": len(data), "too_large": True}
    try:
        return {"path": rel, "size": len(data), "content": data.decode("utf-8")}
    except UnicodeDecodeError:
        return {"path": rel, "size": len(data), "binary": True}


def read_file(pid: str, rel: str) -> dict:
    ctx = _ctx(pid)
    if ctx.attached:
        return _read_attached(ctx, rel)
    pdir = project_dir(pid)
    rel = safe_rel(rel)
    _no_symlinks(pdir, rel, include_last=True)
    target = pdir / rel
    if not target.is_file():
        raise VibeError(404, "No such file")
    size = target.stat().st_size
    if size > MAX_FILE_BYTES:
        return {"path": rel, "size": size, "too_large": True}
    data = target.read_bytes()
    try:
        return {"path": rel, "size": size, "content": data.decode("utf-8")}
    except UnicodeDecodeError:
        return {"path": rel, "size": size, "binary": True}


def _prepare_blocks(blocks, existing: set[str], *, attached: bool = False) -> list[tuple[str, str]]:
    """Validate every block before anything is written. Later blocks win over earlier ones for the same path.
    attached: chat apply in an attached folder also refuses what the agent may not change (R-15)."""
    if not isinstance(blocks, list) or len(blocks) > MAX_BLOCKS:
        raise VibeError(400, f"Send at most {MAX_BLOCKS} files at a time")
    merged: dict[str, str] = {}
    for b in blocks:
        if not isinstance(b, dict):
            raise VibeError(400, "Each file needs a path and content")
        rel = _agent_rel(b.get("path"), attached=True, who="The AI") if attached else safe_rel(b.get("path"))
        _check_content(b.get("content"))
        merged[rel] = b["content"]
    if attached:
        if len(existing | set(merged)) > va.MAX_FILES:
            raise VibeError(413, f"An attached folder can hold at most {va.MAX_FILES:,} files Vibe keeps versions of")
        return list(merged.items())
    if len(existing | set(merged)) > MAX_FILES:
        raise VibeError(413, f"A project can hold at most {MAX_FILES} files")
    return list(merged.items())


def checkpoint_now(pid: str, label: str = "Saved version") -> dict | None:
    ctx = _ctx(pid)
    if ctx.attached:
        with _lock(pid):
            _not_over(ctx)
            if not _load_checkpoints(ctx.rdir):      # "Save version" before any change takes the first version
                rec = _snapshot_locked(pid, ctx.rdir, "Start", "start", force=True, fdir=ctx.fdir)
            else:
                rec = _snapshot_locked(pid, ctx.rdir, label, "manual", fdir=ctx.fdir)
        return _public(rec) if rec else None
    pdir = project_dir(pid)
    with _lock(pid):
        rec = _snapshot_locked(pid, pdir, label, "manual")
    return _public(rec) if rec else None


def apply_blocks(pid: str, blocks: list[dict], label: str = "AI change") -> dict:
    """Write every file or none: checkpoint the state before, write, checkpoint after."""
    ctx = _ctx(pid)
    if ctx.attached:
        return _apply_attached(ctx, blocks, label)
    pdir = project_dir(pid)
    with _lock(pid):
        _refuse_during_agent_run(pid)
        existing = set(_scan(pdir))
        prepared = _prepare_blocks(blocks, existing)
        if not prepared:
            raise VibeError(400, "No files to apply")
        before = _snapshot_locked(pid, pdir, "Before: " + str(label or "")[:180], "before")
        parent_rec = before or (_load_checkpoints(pdir) or [None])[-1]
        old_state: dict[str, bytes | None] = {}
        written: list[str] = []
        try:
            for rel, _c in prepared:
                _no_symlinks(pdir, rel, include_last=True)
                tgt = pdir / rel
                old_state[rel] = tgt.read_bytes() if tgt.is_file() else None
            for rel, content in prepared:
                _write_file_locked(pdir, rel, content)
                written.append(rel)
        except Exception:
            for rel in written:      # put back what was already written
                tgt = pdir / rel
                try:
                    if old_state.get(rel) is None:
                        tgt.unlink()
                    else:
                        tgt.write_bytes(old_state[rel])
                except OSError:
                    pass
            raise
        after = _snapshot_locked(pid, pdir, str(label or "AI change")[:200], "ai")
        changes = _changes(pdir, parent_rec["files"] if parent_rec else {}, after["files"]) if after else []
    _audit("vibe_apply", "Vibe applied AI files", {
        "project": pid, "checkpoint": after["id"] if after else None, "files_changed": len(changes),
        "lines_added": sum(c["added"] for c in changes), "lines_removed": sum(c["removed"] for c in changes)})
    return {
        "checkpoint": _public(after) if after else None,
        "before": parent_rec["id"] if parent_rec else None,
        "changed": changes,
    }


def _apply_attached(ctx: _Ctx, blocks: list[dict], label: str) -> dict:
    """apply_blocks in an attached folder: _agent_rel on every path (R-15), the folder's limits (R-11), the first
    version before the first change (R-12)."""
    pid, rdir, fdir = ctx.pid, ctx.rdir, ctx.fdir
    with _lock(pid):
        _refuse_during_agent_run(pid)
        tree = _not_over(ctx)
        sizes = {r: st.st_size for r, st in tree.files}
        prepared = _prepare_blocks(blocks, set(sizes), attached=True)
        if not prepared:
            raise VibeError(400, "No files to apply")
        total = tree.bytes + sum(len(c.encode("utf-8")) - sizes.get(r, 0) for r, c in prepared)
        if total > va.MAX_BYTES:
            raise VibeError(413, f"The files of an attached folder can come to at most {va.MAX_BYTES // 1048576} MB")
        _ensure_start_locked(ctx)
        before = _snapshot_locked(pid, rdir, "Before: " + str(label or "")[:180], "before", fdir=fdir)
        parent_rec = before or (_load_checkpoints(rdir) or [None])[-1]
        old_state: dict[str, bytes | None] = {}
        written: list[str] = []
        try:
            for rel, _c in prepared:
                _no_symlinks(fdir, rel, include_last=True)
                tgt = fdir / rel
                old_state[rel] = _read_nofollow(tgt) if tgt.is_file() else None
            for rel, content in prepared:
                _write_file_locked(fdir, rel, content, attached=True)
                written.append(rel)
        except Exception:
            for rel in written:      # put back what was already written
                try:
                    if old_state.get(rel) is None:
                        (fdir / rel).unlink()
                    else:
                        _write_bytes_locked(fdir, rel, old_state[rel], attached=True)
                except (OSError, VibeError):
                    pass
            raise
        after = _snapshot_locked(pid, rdir, str(label or "AI change")[:200], "ai", fdir=fdir)
        changes = _changes(rdir, parent_rec["files"] if parent_rec else {}, after["files"]) if after else []
    _audit("vibe_apply", "Vibe applied AI files", {
        "project": pid, "checkpoint": after["id"] if after else None, "files_changed": len(changes),
        "lines_added": sum(c["added"] for c in changes), "lines_removed": sum(c["removed"] for c in changes)})
    return {
        "checkpoint": _public(after) if after else None,
        "before": parent_rec["id"] if parent_rec else None,
        "changed": changes,
    }


# ── checkpoints ──────────────────────────────────────────────────────────

def list_checkpoints(pid: str, limit: int = 100) -> list[dict]:
    pdir = record_dir(pid)
    cps = _load_checkpoints(pdir)
    spans = _rewound_spans(cps)
    return [dict(_public(c), rewound=True) if _in_spans(c["seq"], spans) else _public(c)
            for c in reversed(cps)][:max(1, min(int(limit), 500))]


def _find(cps: list[dict], cid: str) -> dict:
    if not isinstance(cid, str) or not _CID_RE.match(cid):
        raise VibeError(400, "Bad checkpoint id")
    for c in cps:
        if c["id"] == cid:
            return c
    raise VibeError(404, "No such checkpoint")


def _undo_target(cps: list[dict]) -> dict | None:
    """The newest AI checkpoint that is still in effect, or None. A restore drops every AI
    checkpoint newer than the checkpoint it restored."""
    stack: list[dict] = []
    seq_of = {c["id"]: c["seq"] for c in cps}
    for c in cps:
        if c["kind"] == "ai":
            stack.append(c)
        elif c["kind"] == "restore":
            floor = seq_of.get(c.get("restored_to"), 0)
            stack = [a for a in stack if a["seq"] <= floor]
    return stack[-1] if stack else None


def _restore_locked(pid: str, pdir: Path, target: dict, label: str, extra: dict, *, force: bool = False) -> dict:
    files = target["files"]
    for rel in files:
        safe_rel(rel)       # a damaged history must not be able to write outside the project
    saved = _snapshot_locked(pid, pdir, "Before restore", "before_restore")
    current = _scan(pdir)
    for rel, sha in files.items():
        if rel in current and current[rel][1] == sha:
            continue
        _write_bytes_locked(pdir, rel, _blob(pdir, sha))
    for rel in current:
        if rel not in files:
            try:
                (pdir / rel).unlink()
            except OSError:
                pass
            _prune_empty_dirs(pdir, (pdir / rel).parent)
    rec = _snapshot_locked(pid, pdir, label, "restore", extra=extra, force=force)
    if rec is None:    # restored to the state we were already in
        rec = (_load_checkpoints(pdir) or [None])[-1]
    _audit("vibe_restore", "Vibe restored a checkpoint", {
        "project": pid, "restored_to": target["id"], "checkpoint": rec["id"] if rec else None,
        "saved_before": saved["id"] if saved else None})
    return {"checkpoint": _public(rec) if rec else None, "restored_to": target["id"],
            "saved_before": saved["id"] if saved else None}


def _differs(a: dict, b: dict) -> set[str]:
    return {k for k in set(a) | set(b) if a.get(k) != b.get(k)}


def _ai_touched(cps: list[dict], after_seq: int) -> set[str]:
    """Every file Vibe's own AI changes (chat applies, agent steps and their commands) changed after checkpoint
    after_seq: what each "ai" checkpoint changed against its parent (R-13)."""
    by_id = {c["id"]: c for c in cps}
    out: set[str] = set()
    for c in cps:
        if c["kind"] == "ai" and c["seq"] > after_seq:
            parent = by_id.get(c.get("parent"))
            out |= _differs(parent["files"] if parent else {}, c["files"])
    return out


def _restore_attached_locked(ctx: _Ctx, target: dict, label: str, extra: dict, *, scope: set[str] | None = None,
                             force: bool = False) -> dict:
    """_restore_locked for an attached folder. scope None puts back the whole folder (the Versions drawer); a set
    puts back only those files (Undo, Rewind, Undo rewind: R-13). Empty folders a restore leaves are kept."""
    pid, rdir, fdir = ctx.pid, ctx.rdir, ctx.fdir
    files = target["files"]
    for rel in set(files) | (scope or set()):
        _attached_rel(rel)  # a damaged history must not be able to write outside the folder
    tree = _not_over(ctx)
    saved = _snapshot_locked(pid, rdir, "Before restore", "before_restore", fdir=fdir)
    current = _scan_attached(rdir, fdir)
    paths = sorted(set(files) | set(current) if scope is None else scope)
    sizes = {r: st.st_size for r, st in tree.files}
    count, total = len(sizes), tree.bytes
    for rel in paths:
        want, have = files.get(rel), current.get(rel)
        if want == (have[1] if have else None):
            continue
        new_size = 0
        if want is not None:
            try:
                new_size = (_objects(rdir) / want).stat().st_size
            except OSError:
                raise VibeError(500, "A stored file version is missing") from None
        count += (want is not None) - (rel in sizes)
        total += new_size - sizes.get(rel, 0)
    if count > va.MAX_FILES or total > va.MAX_BYTES:
        raise VibeError(413, "That version would take the folder past its limits" + _READ_ONLY)
    for rel in paths:
        want, have = files.get(rel), current.get(rel)
        if want == (have[1] if have else None):
            continue
        if want is None:
            _no_symlinks(fdir, rel, include_last=False)
            try:
                st = os.lstat(fdir / rel)
                if stat.S_ISREG(st.st_mode):
                    os.unlink(fdir / rel)
            except OSError:
                pass
        else:
            _write_bytes_locked(fdir, rel, _blob(rdir, want), attached=True)
    rec = _snapshot_locked(pid, rdir, label, "restore", extra=extra, force=force, fdir=fdir)
    if rec is None:    # restored to the state we were already in
        rec = (_load_checkpoints(rdir) or [None])[-1]
    _audit("vibe_restore", "Vibe restored a checkpoint", {
        "project": pid, "restored_to": target["id"], "checkpoint": rec["id"] if rec else None,
        "saved_before": saved["id"] if saved else None})
    return {"checkpoint": _public(rec) if rec else None, "restored_to": target["id"],
            "saved_before": saved["id"] if saved else None}


def restore_preview(pid: str, cid: str) -> dict:
    """What a whole-folder restore of cid would change in an attached folder, before it runs (R-13): how many files
    in all, and how many of them are files Vibe's AI did not change since that version ("other")."""
    ctx = _ctx(pid)
    cps = _load_checkpoints(ctx.rdir)
    target = _find(cps, cid)
    if not ctx.attached:
        return {"checkpoint": cid, "files": None, "other": None, "attached": False}
    current = {p: h for p, (_s, h) in _scan_attached(ctx.rdir, ctx.fdir, _not_over(ctx)).items()}
    changed = _differs(target["files"], current)
    ai = _ai_touched(cps, target["seq"])
    return {"checkpoint": cid, "files": len(changed), "other": len(changed - ai), "attached": True}


def _prune_empty_dirs(pdir: Path, start: Path) -> None:
    cur = start
    real = os.path.realpath(pdir)
    while os.path.realpath(cur) != real and str(cur).startswith(str(pdir)):
        try:
            cur.rmdir()
        except OSError:
            break
        cur = cur.parent


def restore(pid: str, cid: str, scope: str | None = None) -> dict:
    """Restore cid. In an attached folder scope "ai" puts back only the files Vibe's AI changed since cid (what the
    chat's Undo button sends, R-13); any other value restores the whole folder. A created project ignores scope."""
    ctx = _ctx(pid)
    if ctx.attached:
        with _lock(pid):
            _refuse_during_agent_run(pid)
            cps = _load_checkpoints(ctx.rdir)
            target = _find(cps, cid)
            only = _ai_touched(cps, target["seq"]) if scope == "ai" else None
            return _restore_attached_locked(ctx, target, "Restored: " + (target["label"] or target["id"])[:180],
                                            {"restored_to": target["id"]}, scope=only)
    pdir = project_dir(pid)
    with _lock(pid):
        _refuse_during_agent_run(pid)
        cps = _load_checkpoints(pdir)
        target = _find(cps, cid)
        return _restore_locked(pid, pdir, target, "Restored: " + (target["label"] or target["id"])[:180],
                               {"restored_to": target["id"]})


def undo_last_ai(pid: str) -> dict:
    ctx = _ctx(pid)
    if ctx.attached:
        with _lock(pid):
            _refuse_during_agent_run(pid)
            cps = _load_checkpoints(ctx.rdir)
            ai = _undo_target(cps)
            if ai is None or not ai.get("parent"):
                raise VibeError(409, "Nothing to undo")
            parent = _find(cps, ai["parent"])
            out = _restore_attached_locked(ctx, parent, "Undid: " + (ai["label"] or ai["id"])[:180],
                                           {"restored_to": parent["id"], "undid": ai["id"]},
                                           scope=_ai_touched(cps, parent["seq"]))
            out["undid"] = ai["id"]
            return out
    pdir = project_dir(pid)
    with _lock(pid):
        _refuse_during_agent_run(pid)
        cps = _load_checkpoints(pdir)
        ai = _undo_target(cps)
        if ai is None or not ai.get("parent"):
            raise VibeError(409, "Nothing to undo")
        parent = _find(cps, ai["parent"])
        out = _restore_locked(pid, pdir, parent, "Undid: " + (ai["label"] or ai["id"])[:180],
                              {"restored_to": parent["id"], "undid": ai["id"]})
        out["undid"] = ai["id"]
        return out


# ── rewind, fork, rename (docs/VIBE-V4-DESIGN.md, P-077) ─────────────────
# A rewind is a restore tagged with the chat and the vibe.db rows it hid; Undo rewind restores the state just before
# it. Nothing is rewritten: both are new checkpoints, and the rows are hidden in vibe.db, never deleted.

MAX_REWIND_ROWS = 5000
UNVERSIONED_WALK_CAP = 20_000


def _row_ids(rows) -> list[int]:
    return [r for r in (rows if isinstance(rows, list) else [])
            if isinstance(r, int) and not isinstance(r, bool) and r > 0][:MAX_REWIND_ROWS]


def _rewinds(cps: list[dict]) -> list[dict]:
    """Rewind checkpoints that were not undone, oldest first."""
    undone = {c.get("unrewind") for c in cps if isinstance(c.get("unrewind"), str)}
    return [c for c in cps if c.get("kind") == "restore" and isinstance(c.get("rewind"), dict) and c["id"] not in undone]


def _rewound_spans(cps: list[dict]) -> list[tuple[int, int]]:
    """(target seq, rewind seq) of every rewind in effect: what lies strictly between was rewound."""
    seq = {c["id"]: c["seq"] for c in cps}
    return [(seq.get(c.get("restored_to"), 0), c["seq"]) for c in _rewinds(cps)]


def _in_spans(seq, spans: list[tuple[int, int]]) -> bool:
    return isinstance(seq, int) and any(lo < seq < hi for lo, hi in spans)


def rewound_checkpoints(pid: str) -> set[str]:
    """Ids of the checkpoints a rewind in effect went back over (an agent run that started there was rewound)."""
    cps = _load_checkpoints(record_dir(pid))
    spans = _rewound_spans(cps)
    return {c["id"] for c in cps if _in_spans(c["seq"], spans)}


def rewind(pid: str, cid: str, session: str, rows) -> dict:
    """Restore cid for a chat rewind: always a new 'restore' checkpoint, carrying the chat and the rows it hid."""
    if not isinstance(session, str) or not session.strip():
        raise VibeError(400, "Bad session id")
    ctx = _ctx(pid)
    if ctx.attached:
        with _lock(pid):
            _refuse_during_agent_run(pid)
            cps = _load_checkpoints(ctx.rdir)
            target = _find(cps, cid)
            return _restore_attached_locked(
                ctx, target, "Rewound: " + (target["label"] or target["id"])[:180],
                {"restored_to": target["id"], "rewind": {"session": session[:80], "rows": _row_ids(rows)}},
                scope=_ai_touched(cps, target["seq"]), force=True)
    pdir = project_dir(pid)
    with _lock(pid):
        _refuse_during_agent_run(pid)
        target = _find(_load_checkpoints(pdir), cid)
        return _restore_locked(pid, pdir, target, "Rewound: " + (target["label"] or target["id"])[:180],
                               {"restored_to": target["id"], "rewind": {"session": session[:80], "rows": _row_ids(rows)}},
                               force=True)


def undo_rewind(pid: str, rewind_id: str, session: str) -> dict:
    """Put the files back as they were just before a rewind of this chat. Returns the rows it hid, as "rows".
    In an attached folder only the files the rewind changed are put back (R-13)."""
    ctx = _ctx(pid)
    pdir = ctx.rdir
    with _lock(pid):
        _refuse_during_agent_run(pid)
        cps = _load_checkpoints(pdir)
        rec = _find(cps, rewind_id)
        rw = rec.get("rewind")
        if rec.get("kind") != "restore" or not isinstance(rw, dict) or rw.get("session") != session:
            raise VibeError(404, "That is not a rewind of this chat")
        if rec not in _rewinds(cps):
            raise VibeError(409, "That rewind was already undone")
        if not rec.get("parent"):
            raise VibeError(409, "Nothing to go back to")
        before = _find(cps, rec["parent"])
        if ctx.attached:
            out = _restore_attached_locked(ctx, before, "Undid rewind",
                                           {"restored_to": before["id"], "unrewind": rewind_id},
                                           scope=_differs(before["files"], rec["files"]), force=True)
        else:
            out = _restore_locked(pid, pdir, before, "Undid rewind",
                                  {"restored_to": before["id"], "unrewind": rewind_id}, force=True)
    out["rows"] = _row_ids(rw.get("rows"))
    return out


def rewind_state(pid: str, session: str) -> dict | None:
    """The newest rewind of this chat that Undo rewind can still take back, or None."""
    try:
        cps = _load_checkpoints(record_dir(pid))
    except VibeError:
        return None
    for c in reversed(_rewinds(cps)):
        if c["rewind"].get("session") == session:
            return {"checkpoint": c["id"], "restored_to": c.get("restored_to"), "rows": _row_ids(c["rewind"].get("rows")),
                    "newest": c is cps[-1]}      # a later checkpoint means the work went on after the rewind
    return None


def checkpoint_changes(pid: str, cid: str) -> dict:
    """What one checkpoint changed, per file, and the checkpoint that goes back to before it. An agent run's
    checkpoint counts from the run's start checkpoint, so its chips show the whole run."""
    pdir = record_dir(pid)
    cps = _load_checkpoints(pdir)
    rec = _find(cps, cid)
    run = rec.get("run") if isinstance(rec.get("run"), str) and rec.get("run") else None
    base = next((c for c in cps if c.get("run") == run), None) if run else None
    if base is None and rec.get("parent"):
        base = next((c for c in cps if c["id"] == rec["parent"]), None)
    if base is rec:          # a run that changed nothing: its report stores the start checkpoint itself
        base = None
        changes: list[dict] = []
    else:
        changes = _changes(pdir, base["files"] if base else {}, rec["files"])
    return {"checkpoint": _public(rec), "before": base["id"] if base else None, "changed": changes, "run": run}


def fork_project(pid: str, cid: str) -> dict:
    """A new project with exactly the files of checkpoint cid and the history up to it. The original is unchanged.
    The copy of an attached folder's version is a created project, with a created project's limits."""
    src = record_dir(pid)
    root = vibe_root()
    with _lock(pid):
        _refuse_during_agent_run(pid)
        cps = _load_checkpoints(src)
        target = _find(cps, cid)
        history = cps[:cps.index(target) + 1]
        for c in history:
            for rel in c["files"]:
                safe_rel(rel)       # a damaged history must not be able to write outside the new project
            if len(c["files"]) > MAX_FILES:     # only an attached folder's version can be this large
                raise VibeError(413, f"A copy is a normal Vibe project, which holds at most {MAX_FILES} files")
        title = (str(_read_meta(src).get("title") or pid)[:72] + " (copy)")
        for _ in range(20):
            new = f"{_slug(title)}-{secrets.token_hex(3)}"
            if not (root / new).exists():
                break
        else:
            raise VibeError(500, "Could not name the project")
        dest = root / new
        dest.mkdir(mode=0o700)
        try:
            _objects(dest).mkdir(parents=True, mode=0o700)
            for sha in sorted({h for c in history for h in c["files"].values()}):
                data = _blob(src, sha)
                if hashlib.sha256(data).hexdigest() != sha:
                    raise VibeError(500, "A stored file version is damaged")
                _store_blob(dest, sha, data)
            with open(_cp_path(dest), "w", encoding="utf-8") as f:
                for c in history:
                    f.write(json.dumps(c, ensure_ascii=False, separators=(",", ":")) + "\n")
            with _lock(new):
                for rel, sha in target["files"].items():
                    _write_bytes_locked(dest, rel, _blob(dest, sha))
            # Last: until project.json exists the folder is not a project, so a failed copy is never listed.
            _write_json_atomic(_meta_path(dest), {"id": new, "title": title, "created_at": _now(), "updated_at": _now(),
                                                  "forked_from": {"project": pid, "checkpoint": cid}})
        except Exception:
            shutil.rmtree(dest, ignore_errors=True)
            raise
    _audit("vibe_project_fork", "Vibe project copied from a checkpoint",
           {"project": new, "from": pid, "checkpoint": cid, "files": len(target["files"])})
    return project_info(new)


def rename_project(pid: str, title) -> dict:
    """The shown name only: the id and the folder stay."""
    title = re.sub(r"\s+", " ", title.strip())[:80] if isinstance(title, str) else ""
    if not title:
        raise VibeError(400, "Write the new name first")
    pdir = record_dir(pid)
    with _lock(pid):
        _refuse_during_agent_run(pid)
        meta = _read_meta(pdir)
        meta["title"] = title
        _write_json_atomic(_meta_path(pdir), meta)
    return {"id": pid, "title": title}


def unversioned(pid: str) -> dict:
    """Files in the folder that no checkpoint keeps: inside an ignored folder (by its name), over 1 MB, or links.
    .vibe and .git are left out of the count. The walk stops after UNVERSIONED_WALK_CAP entries. An attached folder
    also counts its secret-looking files ("secrets", R-14): named, never read."""
    ctx = _ctx(pid)
    if ctx.attached:
        return _unversioned_attached(ctx)
    pdir = project_dir(pid)
    folders: dict[str, int] = {}
    large = links = seen = 0
    capped = False
    for dirpath, dirnames, filenames in os.walk(str(pdir), followlinks=False):
        keep = []
        for d in dirnames:
            full, low = os.path.join(dirpath, d), d.lower()
            if os.path.islink(full):
                links += 1
            elif low in (META_DIR, ".git"):
                pass
            elif low in _SCAN_IGNORED_DIRS:
                n = 0
                for _dp, _dn, fn in os.walk(full, followlinks=False):
                    n += len(fn)
                    seen += len(fn) + 1
                    if seen > UNVERSIONED_WALK_CAP:
                        capped = True
                        break
                folders[low] = folders.get(low, 0) + n
            else:
                keep.append(d)
        dirnames[:] = [] if capped else keep
        for name in filenames:
            seen += 1
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                links += 1
            elif not _ignored_name(name):
                try:
                    if os.path.getsize(full) > MAX_FILE_BYTES:
                        large += 1
                except OSError:
                    pass
        if capped or seen > UNVERSIONED_WALK_CAP:
            capped = True
            break
    return {"count": sum(folders.values()) + large + links, "folders": folders, "large": large, "links": links,
            "capped": capped}


def _unversioned_attached(ctx: _Ctx) -> dict:
    folders: dict[str, int] = {}
    large = links = secrets_n = seen = 0
    capped = False
    for dirpath, dirnames, filenames in os.walk(str(ctx.fdir), followlinks=False):
        keep = []
        for d in dirnames:
            full, low = os.path.join(dirpath, d), d.lower()
            if os.path.islink(full):
                links += 1
            elif low in (META_DIR, ".git"):
                pass
            elif low in _SCAN_IGNORED_DIRS:
                n = 0
                for _dp, _dn, fn in os.walk(full, followlinks=False):
                    n += len(fn)
                    seen += len(fn) + 1
                    if seen > UNVERSIONED_WALK_CAP:
                        capped = True
                        break
                folders[low] = folders.get(low, 0) + n
            else:
                keep.append(d)
        dirnames[:] = [] if capped else keep
        for name in filenames:
            seen += 1
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                links += 1
            elif name.lower() == ".git" or _ignored_name(name):
                pass
            elif looks_secret(name):
                secrets_n += 1
            else:
                try:
                    if os.path.getsize(full) > MAX_FILE_BYTES:
                        large += 1
                except OSError:
                    pass
        if capped or seen > UNVERSIONED_WALK_CAP:
            capped = True
            break
    return {"count": sum(folders.values()) + large + links + secrets_n, "folders": folders, "large": large,
            "links": links, "secrets": secrets_n, "capped": capped}


# ── commands the owner has allowed for this project (docs/VIBE-AGENT-DESIGN.md, section 7) ──
# Stored as exact text in .vibe/project.json: a command with other spacing asks again.
# One of them may carry "check": true, the project's check that gates the agent's finish (P-045 part 2,
# docs/VIBE-AGENT-EDIT-CHECKS-DESIGN.md). The key is on that entry only; only mark_check sets it, on the owner's click.

MAX_ALLOWED_COMMANDS = 20


def allowed_commands(pid: str) -> list[dict]:
    pdir = record_dir(pid)
    raw = _read_meta(pdir).get("allowed_commands")
    out: list[dict] = []
    if isinstance(raw, list):
        for e in raw:
            if isinstance(e, dict) and isinstance(e.get("cmd"), str) and e["cmd"]:
                out.append({"cmd": e["cmd"], "network": bool(e.get("network"))})
                if e.get("check") is True:          # the JSON value true only
                    for o in out[:-1]:              # a hand edit with several: the newest one marked counts
                        o.pop("check", None)
                    out[-1]["check"] = True
    return _capped(out)


def _capped(items: list) -> list:
    """The newest MAX_ALLOWED_COMMANDS entries, dropping the oldest one that is not the check first."""
    items = list(items)
    while len(items) > MAX_ALLOWED_COMMANDS:
        drop = next((i for i, e in enumerate(items) if not e.get("check")), 0)
        items.pop(drop)
    return items


def check_entry(pid: str) -> dict | None:
    """The command marked as the project's check ({cmd, network, check}), or None (P-045 part 2)."""
    return next((e for e in allowed_commands(pid) if e.get("check")), None)


def allow_command(pid: str, command: str, network: bool = False) -> list[dict]:
    if not isinstance(command, str) or not command.strip() or len(command) > 500:
        raise VibeError(400, "Bad command")
    pdir = record_dir(pid)
    with _lock(pid):
        old = allowed_commands(pid)
        was_check = any(e["cmd"] == command and e.get("check") for e in old)
        items = [e for e in old if e["cmd"] != command]
        items.append({"cmd": command, "network": bool(network), **({"check": True} if was_check else {})})
        items = _capped(items)
        meta = _read_meta(pdir)
        meta["allowed_commands"] = items
        _write_json_atomic(_meta_path(pdir), meta)
    _audit("vibe_agent_allow", "Vibe command allowed for a project", {"project": pid, "action": "allow", "network": bool(network)})
    return items


def mark_check(pid: str, command: str, on: bool) -> list[dict]:
    """The owner's click (P-045 part 2): mark this exact allowed command as the check, clearing any other mark, or
    clear its mark. Called only by the Mac-only route POST .../agent/commands/check, never by a run, a skill or a file."""
    if not isinstance(command, str) or not isinstance(on, bool):
        raise VibeError(400, "Send the command text and on: true or false")
    pdir = record_dir(pid)
    with _lock(pid):
        items = allowed_commands(pid)
        entry = next((e for e in items if e["cmd"] == command), None)
        if entry is None:
            raise VibeError(404, "That command is not on the list")
        for e in items:
            if on or e is entry:
                e.pop("check", None)
        if on:
            entry["check"] = True
        meta = _read_meta(pdir)
        meta["allowed_commands"] = items
        _write_json_atomic(_meta_path(pdir), meta)
    _audit("vibe_agent_allow", "Vibe check marked for a project" if on else "Vibe check cleared for a project",
           {"project": pid, "action": "check" if on else "uncheck", "network": bool(entry["network"])})
    return items


def forget_command(pid: str, command: str) -> list[dict]:
    pdir = record_dir(pid)
    with _lock(pid):
        items = allowed_commands(pid)
        kept = [e for e in items if e["cmd"] != command]
        if len(kept) == len(items):
            raise VibeError(404, "That command is not on the list")
        meta = _read_meta(pdir)
        meta["allowed_commands"] = kept
        _write_json_atomic(_meta_path(pdir), meta)
    _audit("vibe_agent_allow", "Vibe command forgotten for a project", {"project": pid, "action": "forget", "network": False})
    return kept


# ── the owner's rules, like "git diff *" (P-048, docs/VIBE-ALLOW-RULES-DESIGN.md) ──
# Checked by codec_vibe_agent_rules when added, and again (its static part) every time they are read, so a rule put
# into project.json by hand that the editor would refuse is ignored. A rule never carries the web.

MAX_ALLOWED_RULES = 20


def allowed_rules(pid: str) -> list[dict]:
    import codec_vibe_agent_rules as rules
    raw = _read_meta(record_dir(pid)).get("allowed_rules")
    out: list[dict] = []
    if isinstance(raw, list):
        for e in raw:
            text = e.get("rule") if isinstance(e, dict) else None
            try:
                ok = rules.check_rule(text)
            except rules.RuleRefused:
                continue
            if ok == text and all(x["rule"] != ok for x in out):
                out.append({"rule": ok})
    return out[-MAX_ALLOWED_RULES:]


def allow_rule(pid: str, rule) -> list[dict]:
    """Only on the owner's click at this Mac (routes/vibe_agent.py). 400 with the reason when the rule is refused."""
    import codec_vibe_agent_rules as rules
    pdir = record_dir(pid)
    try:
        text = rules.check_rule(rule, project=files_dir(pid))
    except rules.RuleRefused as e:
        raise VibeError(400, str(e)) from None
    with _lock(pid):
        items = [e for e in allowed_rules(pid) if e["rule"] != text]
        items.append({"rule": text})
        meta = _read_meta(pdir)
        meta["allowed_rules"] = items[-MAX_ALLOWED_RULES:]
        _write_json_atomic(_meta_path(pdir), meta)
    _audit("vibe_agent_allow", "Vibe command rule allowed for a project", {"project": pid, "action": "allow_rule", "network": False})
    return items[-MAX_ALLOWED_RULES:]


def forget_rule(pid: str, rule: str) -> list[dict]:
    pdir = record_dir(pid)
    with _lock(pid):
        items = allowed_rules(pid)
        kept = [e for e in items if e["rule"] != rule]
        if len(kept) == len(items):
            raise VibeError(404, "That rule is not on the list")
        meta = _read_meta(pdir)
        meta["allowed_rules"] = kept
        _write_json_atomic(_meta_path(pdir), meta)
    _audit("vibe_agent_allow", "Vibe command rule forgotten for a project", {"project": pid, "action": "forget_rule", "network": False})
    return kept


# ── helpers for the agent run that holds the project (the 409 guard does not apply to them) ──

def agent_checkpoint(pid: str, label: str, kind: str, run: str, step: int, *, force: bool = False) -> dict | None:
    """A checkpoint tagged with the run and step. None when nothing changed (unless forced)."""
    if kind not in ("before", "ai"):
        raise VibeError(400, "Bad checkpoint kind")
    ctx = _ctx(pid)
    if ctx.attached:
        with _lock(pid):
            _not_over(ctx)
            _ensure_start_locked(ctx)
            rec = _snapshot_locked(pid, ctx.rdir, str(label or "")[:200], kind, force=force,
                                   extra={"run": str(run)[:20], "step": int(step)}, fdir=ctx.fdir)
        return _public(rec) if rec else None
    pdir = project_dir(pid)
    with _lock(pid):
        rec = _snapshot_locked(pid, pdir, str(label or "")[:200], kind, force=force,
                               extra={"run": str(run)[:20], "step": int(step)})
    return _public(rec) if rec else None


def _agent_rel(rel: str, *, attached: bool = False, who: str = "The agent") -> str:
    """safe_rel, plus the folders only the owner may change. For the agent's file tools, and for chat apply in an
    attached folder (R-15, who="The AI"). attached: an attached folder's longer paths."""
    rel = _attached_rel(rel) if attached else safe_rel(rel)
    for seg in rel.split("/"):
        if seg.casefold() in _AGENT_BLOCKED_SEGMENTS:
            raise VibeError(400, f"{who} may not change '{seg}': editors and tools run code or take settings "
                                 "from it later. The owner can edit it in the editor.")
    if rel.casefold() in _AGENT_NOTES_FILES:
        raise VibeError(400, f"'{rel}' is the owner's project notes file: {who.lower()} reads it but may not change "
                             "it. Say in your report what should change in it.")
    return rel


def _not_playbooks(target: Path) -> None:
    """The agent never writes a playbook folder or the playbook approval file (P-071), even for a project folder that
    sits inside one. Fails closed when the check cannot run."""
    try:
        import codec_vibe_playbooks as vpb
        protected = vpb.is_protected(target)
    except Exception:
        protected = True
    if protected:
        raise VibeError(400, "That path is in the owner's playbook store: the agent may not change it.")


def agent_write(pid: str, rel: str, content: str) -> dict:
    """Write one project file. Same path, size and link rules as every other write, plus _agent_rel."""
    ctx = _ctx(pid)
    if ctx.attached:
        rel = _agent_rel(rel, attached=True)
        _not_playbooks(ctx.fdir / rel)
        data = _check_content(content)
        with _lock(pid):
            _room(ctx, rel, len(data))
            _ensure_start_locked(ctx)
            existed = (ctx.fdir / rel).is_file()
            size = _write_bytes_locked(ctx.fdir, rel, data, attached=True)
        return {"path": rel, "size": size, "created": not existed}
    pdir = project_dir(pid)
    rel = _agent_rel(rel)
    _not_playbooks(pdir / rel)
    with _lock(pid):
        existed = (pdir / rel).is_file()
        size = _write_file_locked(pdir, rel, content)
    return {"path": rel, "size": size, "created": not existed}


def agent_delete(pid: str, rel: str) -> dict:
    ctx = _ctx(pid)
    if ctx.attached:
        rel = _agent_rel(rel, attached=True)
        _not_playbooks(ctx.fdir / rel)
        with _lock(pid):
            _not_over(ctx)
            _ensure_start_locked(ctx)
            _no_symlinks(ctx.fdir, rel, include_last=True)
            target = ctx.fdir / rel
            try:
                st = os.lstat(target)
            except OSError:
                raise VibeError(404, "No such file") from None
            if not stat.S_ISREG(st.st_mode):
                raise VibeError(404, "No such file")
            target.unlink()         # empty folders are left in an attached folder
        return {"path": rel}
    pdir = project_dir(pid)
    rel = _agent_rel(rel)
    _not_playbooks(pdir / rel)
    with _lock(pid):
        _no_symlinks(pdir, rel, include_last=True)
        target = pdir / rel
        if not target.is_file():
            raise VibeError(404, "No such file")
        target.unlink()
        _prune_empty_dirs(pdir, target.parent)
    return {"path": rel}


def diff(pid: str, a: str, b: str, path: str | None = None) -> dict:
    """Unified diffs between two checkpoints; b may be 'working' for the files as they are now."""
    ctx = _ctx(pid)
    pdir = ctx.rdir
    cps = _load_checkpoints(pdir)
    old = _find(cps, a)["files"] if a else {}
    working: dict[str, bytes] = {}
    if b == "working" and ctx.attached:
        new = {}
        for rel, (_s, sha) in _scan_attached(pdir, ctx.fdir).items():
            new[rel] = sha
            if not (_objects(pdir) / sha).exists():     # only files not stored yet are read (R-12)
                working[sha] = _read_nofollow(ctx.fdir / rel)
    elif b == "working":
        new = {}
        for rel, (_s, sha) in _scan(pdir).items():
            new[rel] = sha
            working[sha] = (pdir / rel).read_bytes()
    else:
        new = _find(cps, b)["files"]

    def load(sha: str) -> str:
        return _text(working[sha]) if sha in working else _text(_blob(pdir, sha))

    only = (_attached_rel(path) if ctx.attached else safe_rel(path)) if path else None
    out = []
    for rel in sorted(set(old) | set(new)):
        if only and rel != only:
            continue
        o, n = old.get(rel), new.get(rel)
        if o == n:
            continue
        status = "added" if o is None else "removed" if n is None else "changed"
        text = _unified(load(o) if o else "", load(n) if n else "", rel)
        if len(text) > MAX_DIFF_CHARS:
            text = text[:MAX_DIFF_CHARS] + "\n… (diff cut)\n"
        out.append({"path": rel, "status": status, "diff": text})
        if len(out) >= MAX_DIFF_FILES:
            break
    return {"from": a or None, "to": b, "files": out}
