"""CODEC Notes: the owner's own shelf (docs/NOTES-DESIGN.md).

One JSON file per note in ~/.codec/notes (CODEC_NOTES_DIR overrides it, which the tests use). The folder
is 0700, the files 0600, every write is atomic. Delete moves the file to .trash/ (nothing is erased).

A note is the owner's text. Nothing in the AI paths (skills, agents, MCP) reads or writes this folder,
and the audit log gets ids, sizes and the source, never a title or any text.
NOTES_ENABLED=false switches the routes off.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from codec_jsonstore import atomic_write_json

MAX_TITLE = 120
MAX_BODY = 100_000
MAX_NOTES = 2000
AUTO_TITLE = 80
PREVIEW = 140
SOURCES = ("chat", "manual")
ID_RE = re.compile(r"^[0-9a-f]{12}$")

_LOCK = threading.RLock()


class NoteError(Exception):
    """A refusal with a plain sentence for the owner and the HTTP status that goes with it."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def enabled() -> bool:
    return os.environ.get("NOTES_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


def root() -> Path:
    env = os.environ.get("CODEC_NOTES_DIR", "").strip()
    return Path(env).expanduser() if env else Path.home() / ".codec" / "notes"


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_last_ms = 0


def _now() -> str:
    """The time in milliseconds, and never twice the same: notes saved in one instant still keep their order."""
    global _last_ms
    with _LOCK:
        _last_ms = max(int(time.time() * 1000), _last_ms + 1)
        ms = _last_ms
    return (_EPOCH + timedelta(milliseconds=ms)).isoformat(timespec="milliseconds")


def _audit(event: str, message: str, extra: dict) -> None:
    """Ids, sizes and the source only. Never a title or any text."""
    try:
        from codec_audit import log_event
        log_event(event, "codec-notes", message, extra=extra)
    except Exception:
        pass


def _mkdir(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def _path(note_id) -> Path:
    # The id is the only part of a path that comes from outside, so it must be exactly 12 hex digits.
    if not isinstance(note_id, str) or not ID_RE.match(note_id):
        raise NoteError("That note was not found.", 404)
    return root() / f"{note_id}.json"


def _clean_text(value, what: str) -> str:
    if not isinstance(value, str):
        raise NoteError(f"The {what} must be text.")
    return value.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")


def _clean_title(value) -> str:
    if value is None:
        return ""
    title = " ".join(_clean_text(value, "title").split())
    if len(title) > MAX_TITLE:
        raise NoteError(f"A title can hold {MAX_TITLE} characters at most.")
    return title


def _clean_body(value) -> str:
    body = _clean_text(value, "text")
    if not body.strip():
        raise NoteError("A note needs some text.")
    if len(body) > MAX_BODY:
        raise NoteError(f"A note can hold {MAX_BODY:,} characters at most.")
    return body


def auto_title(body: str) -> str:
    """The first line with text, without Markdown marks, cut at 80 characters."""
    for line in body.split("\n"):
        t = re.sub(r"^[\s#>*\-_]+|[\s#*_]+$", "", line)
        t = " ".join(t.split())
        if t:
            return t[:AUTO_TITLE].rstrip()
    return ""


def _read(path: Path):
    """A note from its file, or None when the file is missing, damaged or not a note."""
    try:
        with open(path, encoding="utf-8") as f:
            n = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(n, dict) or n.get("id") != path.stem:
        return None
    if not all(isinstance(n.get(k), str) for k in ("title", "body", "created", "updated")):
        return None
    n.setdefault("source", "manual")
    return n


def _files(folder: Path):
    if not folder.is_dir():
        return []
    return [p for p in folder.glob("*.json") if ID_RE.match(p.stem)]


def _summary(n: dict) -> dict:
    flat = " ".join(n["body"].split())
    return {"id": n["id"], "title": n["title"], "preview": flat[:PREVIEW], "source": n.get("source", "manual"),
            "created": n["created"], "updated": n["updated"], "chars": len(n["body"])}


def create(body, title="", source="manual") -> dict:
    body = _clean_body(body)
    title = _clean_title(title)
    if source not in SOURCES:
        raise NoteError("That source is not known.")
    with _LOCK:
        folder = _mkdir(root())
        if len(_files(folder)) >= MAX_NOTES:
            raise NoteError(f"Notes are full ({MAX_NOTES:,}). Delete one first.", 409)
        for _ in range(8):
            note_id = secrets.token_hex(6)
            if not (folder / f"{note_id}.json").exists():
                break
        else:
            raise NoteError("Could not make an id for the note. Try again.", 500)
        now = _now()
        note = {"id": note_id, "title": title or auto_title(body) or "Untitled", "body": body,
                "source": source, "created": now, "updated": now}
        atomic_write_json(folder / f"{note_id}.json", note)
    _audit("note_saved", "Note saved", {"id": note_id, "source": source, "chars": len(body)})
    return note


def get(note_id) -> dict:
    path = _path(note_id)
    note = _read(path)
    if note is None:
        raise NoteError("That note was not found.", 404)
    return note


def update(note_id, title=None, body=None) -> dict:
    if title is None and body is None:
        raise NoteError("Send a title or a text to change.")
    new_title = _clean_title(title) if title is not None else None
    new_body = _clean_body(body) if body is not None else None
    with _LOCK:
        note = get(note_id)
        changed = False
        if new_body is not None and new_body != note["body"]:
            note["body"] = new_body
            changed = True
        if new_title is not None:
            # An empty title asks for the automatic one, made from the text as it is now.
            wanted = new_title or auto_title(note["body"]) or "Untitled"
            if wanted != note["title"]:
                note["title"] = wanted
                changed = True
        if changed:
            note["updated"] = _now()
            atomic_write_json(_path(note_id), note)
    if changed:
        _audit("note_updated", "Note updated", {"id": note_id, "source": note.get("source", "manual"),
                                                 "chars": len(note["body"])})
    return note


def trash(note_id) -> None:
    """Move the note to .trash/. Nothing is erased."""
    src = _path(note_id)
    with _LOCK:
        note = _read(src)
        if note is None:
            raise NoteError("That note was not found.", 404)
        dest_dir = _mkdir(root() / ".trash")
        os.replace(src, dest_dir / f"{note_id}-{int(time.time())}.json")
    _audit("note_deleted", "Note moved to the trash", {"id": note_id, "source": note.get("source", "manual"),
                                                       "chars": len(note["body"])})


def list_notes(q: str = "", limit: int = 200) -> dict:
    """Newest first. Every word in q must appear in the title or the text (any case)."""
    limit = max(1, min(int(limit), 500))
    words = [w for w in str(q or "").lower().split() if w][:8]
    rows, total = [], 0
    for path in _files(root()):
        n = _read(path)
        if n is None:
            continue
        total += 1
        if words:
            hay = (n["title"] + "\n" + n["body"]).lower()
            if not all(w in hay for w in words):
                continue
        rows.append(n)
    rows.sort(key=lambda n: (n["updated"], n["id"]), reverse=True)
    return {"notes": [_summary(n) for n in rows[:limit]], "total": total, "matched": len(rows)}
