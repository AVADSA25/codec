"""Vibe projects: a folder of files with checkpoints (docs/VIBE-PROJECTS-DESIGN.md).

A project is a plain folder under ~/codec-workspace/vibe/<id>/. Next to its files
sits a hidden .vibe/ folder that holds the project's settings and its checkpoints:

    .vibe/project.json        title, created and updated times
    .vibe/checkpoints.jsonl   one line per checkpoint (id, kind, label, file -> hash)
    .vibe/objects/<sha256>    the content of every file version, stored once

A checkpoint is taken before and after every AI change. Restoring one writes its
files back and takes a new checkpoint; nothing is rewritten or deleted, so a
restore can itself be undone. No git is needed (customer Macs may not have it).

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
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

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
# Folders a scan skips (listing, checkpoints).
_SCAN_IGNORED_DIRS = {META_DIR, ".git", "node_modules", "__pycache__", ".venv", "venv", ".next", ".cache",
                      ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox"}      # what test runs leave behind
_BLOCKED_NAME_PREFIXES = (".env", "credentials", ".netrc", ".npmrc", ".pypirc", "id_rsa", "id_ed25519", "id_dsa", ".htpasswd")
_BLOCKED_EXTS = (".pem", ".key", ".p12", ".pfx", ".keystore")
_TMP_SUFFIX = ".vibe-tmp"
_IGNORED_FILES = {".ds_store"}

_PID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,60}$")
_CID_RE = re.compile(r"^c\d{4,}$")
_KINDS = {"start", "before", "ai", "restore", "before_restore", "manual"}

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_AGENT_RUNS: dict[str, str] = {}    # project id -> run id, while an agent run holds the project


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
    """Hold the project for one run. While held, every other writer gets a 409."""
    project_dir(pid)
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


def looks_secret(rel: str) -> bool:
    """True when the file name looks like a secret (.env, keys, credentials). Same list safe_rel uses."""
    base = str(rel).rsplit("/", 1)[-1].lower()
    return base.startswith(_BLOCKED_NAME_PREFIXES) or base.endswith(_BLOCKED_EXTS)


def safe_rel(rel: str) -> str:
    """Validate a path inside a project and return it normalised (posix, no leading ./)."""
    if not isinstance(rel, str) or not rel.strip() or len(rel) > MAX_PATH_LEN:
        raise VibeError(400, "Bad file path")
    if "\x00" in rel or "\\" in rel or rel.startswith(("/", "~")) or any(ord(c) < 32 for c in rel):
        raise VibeError(400, "Bad file path")
    norm = posixpath.normpath(rel.strip())
    if norm in (".", "") or norm == ".." or norm.startswith("../") or norm.startswith("/"):
        raise VibeError(400, "The path leaves the project")
    parts = norm.split("/")
    if len(parts) > MAX_DEPTH:
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


def list_files(pid: str) -> list[dict]:
    pdir = project_dir(pid)
    return [{"path": p, "size": s} for p, (s, _h) in sorted(_scan(pdir).items())]


# ── storage ──────────────────────────────────────────────────────────────

def _meta_path(pdir: Path) -> Path:
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


def _snapshot_locked(pid: str, pdir: Path, label: str, kind: str, *, force: bool = False, extra: dict | None = None) -> dict | None:
    if kind not in _KINDS:
        raise VibeError(400, "Bad checkpoint kind")
    scan = _scan(pdir)
    manifest = {p: h for p, (_s, h) in scan.items()}
    cps = _load_checkpoints(pdir)
    last = cps[-1] if cps else None
    if last is not None and not force and last["files"] == manifest:
        return None
    old = last["files"] if last else {}
    for path, (_s, sha) in scan.items():
        if not (_objects(pdir) / sha).exists():
            try:
                data = (pdir / path).read_bytes()
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
        meta = _read_meta(root / name)
        if meta.get("id") == name:
            out.append({"id": name, "title": meta.get("title", name), "created_at": meta.get("created_at", ""),
                        "updated_at": meta.get("updated_at", "")})
    out.sort(key=lambda m: m["updated_at"], reverse=True)
    return out[:max(1, min(int(limit), 200))]


def project_info(pid: str) -> dict:
    pdir = project_dir(pid)
    meta = _read_meta(pdir)
    cps = _load_checkpoints(pdir)
    return {
        "id": pid, "title": meta.get("title", pid), "created_at": meta.get("created_at", ""),
        "updated_at": meta.get("updated_at", ""), "files": list_files(pid),
        "checkpoint_count": len(cps), "last_checkpoint": _public(cps[-1]) if cps else None,
        "can_undo": _undo_target(cps) is not None,
    }


def trash_project(pid: str) -> dict:
    pdir = project_dir(pid)
    with _lock(pid):
        _refuse_during_agent_run(pid)
        dest_root = trash_root()
        dest_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        dest = dest_root / f"{pid}-{time.strftime('%Y%m%d-%H%M%S')}"
        n = 1
        while dest.exists():
            n += 1
            dest = dest_root / f"{pid}-{time.strftime('%Y%m%d-%H%M%S')}-{n}"
        shutil.move(str(pdir), str(dest))
    _audit("vibe_project_trash", "Vibe project moved to the trash", {"project": pid})
    return {"ok": True, "trash": str(dest)}


# ── files ────────────────────────────────────────────────────────────────

def _check_content(content) -> bytes:
    if not isinstance(content, str):
        raise VibeError(400, "File content must be text")
    data = content.encode("utf-8")
    if len(data) > MAX_FILE_BYTES:
        raise VibeError(413, "A file can be at most 1 MB")
    return data


def _write_bytes_locked(pdir: Path, rel: str, data: bytes) -> int:
    """The one place a project file is written: checked path, no links, atomic replace."""
    rel = safe_rel(rel)
    _no_symlinks(pdir, rel, include_last=True)
    target = pdir / rel
    if target.is_dir():
        raise VibeError(400, "That path is a folder")
    if not target.exists() and _count_files(pdir) >= MAX_FILES:
        raise VibeError(413, f"A project can hold at most {MAX_FILES} files")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _no_symlinks(pdir, rel, include_last=True)
    tmp = target.with_name(target.name + _TMP_SUFFIX)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return len(data)


def _write_file_locked(pdir: Path, rel: str, content: str) -> int:
    return _write_bytes_locked(pdir, rel, _check_content(content))


def write_file(pid: str, rel: str, content: str) -> dict:
    pdir = project_dir(pid)
    with _lock(pid):
        _refuse_during_agent_run(pid)
        size = _write_file_locked(pdir, rel, content)
    return {"path": safe_rel(rel), "size": size}


def read_file(pid: str, rel: str) -> dict:
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


def _prepare_blocks(blocks, existing: set[str]) -> list[tuple[str, str]]:
    """Validate every block before anything is written. Later blocks win over earlier ones for the same path."""
    if not isinstance(blocks, list) or len(blocks) > MAX_BLOCKS:
        raise VibeError(400, f"Send at most {MAX_BLOCKS} files at a time")
    merged: dict[str, str] = {}
    for b in blocks:
        if not isinstance(b, dict):
            raise VibeError(400, "Each file needs a path and content")
        rel = safe_rel(b.get("path"))
        _check_content(b.get("content"))
        merged[rel] = b["content"]
    if len(existing | set(merged)) > MAX_FILES:
        raise VibeError(413, f"A project can hold at most {MAX_FILES} files")
    return list(merged.items())


def checkpoint_now(pid: str, label: str = "Saved version") -> dict | None:
    pdir = project_dir(pid)
    with _lock(pid):
        rec = _snapshot_locked(pid, pdir, label, "manual")
    return _public(rec) if rec else None


def apply_blocks(pid: str, blocks: list[dict], label: str = "AI change") -> dict:
    """Write every file or none: checkpoint the state before, write, checkpoint after."""
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


# ── checkpoints ──────────────────────────────────────────────────────────

def list_checkpoints(pid: str, limit: int = 100) -> list[dict]:
    pdir = project_dir(pid)
    cps = _load_checkpoints(pdir)
    return [_public(c) for c in reversed(cps)][:max(1, min(int(limit), 500))]


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


def _restore_locked(pid: str, pdir: Path, target: dict, label: str, extra: dict) -> dict:
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
    rec = _snapshot_locked(pid, pdir, label, "restore", extra=extra)
    if rec is None:    # restored to the state we were already in
        rec = (_load_checkpoints(pdir) or [None])[-1]
    _audit("vibe_restore", "Vibe restored a checkpoint", {
        "project": pid, "restored_to": target["id"], "checkpoint": rec["id"] if rec else None,
        "saved_before": saved["id"] if saved else None})
    return {"checkpoint": _public(rec) if rec else None, "restored_to": target["id"],
            "saved_before": saved["id"] if saved else None}


def _prune_empty_dirs(pdir: Path, start: Path) -> None:
    cur = start
    real = os.path.realpath(pdir)
    while os.path.realpath(cur) != real and str(cur).startswith(str(pdir)):
        try:
            cur.rmdir()
        except OSError:
            break
        cur = cur.parent


def restore(pid: str, cid: str) -> dict:
    pdir = project_dir(pid)
    with _lock(pid):
        _refuse_during_agent_run(pid)
        cps = _load_checkpoints(pdir)
        target = _find(cps, cid)
        return _restore_locked(pid, pdir, target, "Restored: " + (target["label"] or target["id"])[:180],
                               {"restored_to": target["id"]})


def undo_last_ai(pid: str) -> dict:
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


# ── commands the owner has allowed for this project (docs/VIBE-AGENT-DESIGN.md, section 7) ──
# Stored as exact text in .vibe/project.json: a command with other spacing asks again.

MAX_ALLOWED_COMMANDS = 20


def allowed_commands(pid: str) -> list[dict]:
    pdir = project_dir(pid)
    raw = _read_meta(pdir).get("allowed_commands")
    out: list[dict] = []
    if isinstance(raw, list):
        for e in raw:
            if isinstance(e, dict) and isinstance(e.get("cmd"), str) and e["cmd"]:
                out.append({"cmd": e["cmd"], "network": bool(e.get("network"))})
    return out[-MAX_ALLOWED_COMMANDS:]


def allow_command(pid: str, command: str, network: bool = False) -> list[dict]:
    if not isinstance(command, str) or not command.strip() or len(command) > 500:
        raise VibeError(400, "Bad command")
    pdir = project_dir(pid)
    with _lock(pid):
        items = [e for e in allowed_commands(pid) if e["cmd"] != command]
        items.append({"cmd": command, "network": bool(network)})
        meta = _read_meta(pdir)
        meta["allowed_commands"] = items[-MAX_ALLOWED_COMMANDS:]
        _write_json_atomic(_meta_path(pdir), meta)
    _audit("vibe_agent_allow", "Vibe command allowed for a project", {"project": pid, "action": "allow", "network": bool(network)})
    return items[-MAX_ALLOWED_COMMANDS:]


def forget_command(pid: str, command: str) -> list[dict]:
    pdir = project_dir(pid)
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


# ── helpers for the agent run that holds the project (the 409 guard does not apply to them) ──

def agent_checkpoint(pid: str, label: str, kind: str, run: str, step: int, *, force: bool = False) -> dict | None:
    """A checkpoint tagged with the run and step. None when nothing changed (unless forced)."""
    if kind not in ("before", "ai"):
        raise VibeError(400, "Bad checkpoint kind")
    pdir = project_dir(pid)
    with _lock(pid):
        rec = _snapshot_locked(pid, pdir, str(label or "")[:200], kind, force=force,
                               extra={"run": str(run)[:20], "step": int(step)})
    return _public(rec) if rec else None


def _agent_rel(rel: str) -> str:
    """safe_rel, plus the folders only the owner may change. For the agent's file tools only."""
    rel = safe_rel(rel)
    for seg in rel.split("/"):
        if seg.casefold() in _AGENT_BLOCKED_SEGMENTS:
            raise VibeError(400, f"The agent may not change '{seg}': editors and tools run code or take settings "
                                 "from it later. The owner can edit it in the editor.")
    return rel


def agent_write(pid: str, rel: str, content: str) -> dict:
    """Write one project file. Same path, size and link rules as every other write, plus _agent_rel."""
    pdir = project_dir(pid)
    rel = _agent_rel(rel)
    with _lock(pid):
        existed = (pdir / rel).is_file()
        size = _write_file_locked(pdir, rel, content)
    return {"path": rel, "size": size, "created": not existed}


def agent_delete(pid: str, rel: str) -> dict:
    pdir = project_dir(pid)
    rel = _agent_rel(rel)
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
    pdir = project_dir(pid)
    cps = _load_checkpoints(pdir)
    old = _find(cps, a)["files"] if a else {}
    working: dict[str, bytes] = {}
    if b == "working":
        new = {}
        for rel, (_s, sha) in _scan(pdir).items():
            new[rel] = sha
            working[sha] = (pdir / rel).read_bytes()
    else:
        new = _find(cps, b)["files"]

    def load(sha: str) -> str:
        return _text(working[sha]) if sha in working else _text(_blob(pdir, sha))

    only = safe_rel(path) if path else None
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
