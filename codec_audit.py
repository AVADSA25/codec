"""
codec_audit.py -- Centralized audit logging for CODEC.

Writes JSON-line events to ~/.codec/audit_stream.jsonl with automatic
50 MB rotation (one backup kept).  Thread-safe, stdlib-only.
"""

import json
import os
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AUDIT_STREAM = os.path.join(str(Path.home()), ".codec", "audit_stream.jsonl")

# Category constants
COMMAND    = "command"
SKILL      = "skill"
LLM        = "llm"
AUTH       = "auth"
ERROR      = "error"
SCHEDULED  = "scheduled"
VOICE      = "voice"
VISION     = "vision"
TTS        = "tts"
STT        = "stt"
SYSTEM     = "system"
SECURITY   = "security"
CONFIG     = "config"
HOTKEY     = "hotkey"
SCREENSHOT = "screenshot"
DRAFT      = "draft"

_MAX_BYTES = 50 * 1024 * 1024  # 50 MB
_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _ensure_dir():
    os.makedirs(os.path.dirname(AUDIT_STREAM), exist_ok=True)


def _rotate_if_needed():
    """Rename current file to .1 when it exceeds _MAX_BYTES (keep one backup)."""
    try:
        if os.path.getsize(AUDIT_STREAM) >= _MAX_BYTES:
            backup = AUDIT_STREAM + ".1"
            if os.path.exists(backup):
                os.remove(backup)
            os.rename(AUDIT_STREAM, backup)
    except FileNotFoundError:
        pass


def log_event(
    category,       # type: str
    source,         # type: str
    summary,        # type: str
    details=None,   # type: Optional[dict]
    level="info",   # type: str
):
    """Append one JSON-line audit event (thread-safe)."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "cat": category,
        "src": source,
        "lvl": level,
        "sum": summary,
        "det": details,
        "pid": os.getpid(),
    }
    line = json.dumps(record, separators=(",", ":")) + "\n"

    with _lock:
        _ensure_dir()
        _rotate_if_needed()
        with open(AUDIT_STREAM, "a", encoding="utf-8") as f:
            f.write(line)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _tail_lines(path, max_lines=10000):
    # type: (str, int) -> List[str]
    """Read up to *max_lines* from the end of *path* efficiently."""
    try:
        size = os.path.getsize(path)
    except FileNotFoundError:
        return []

    # For small files just read everything
    if size <= 4 * 1024 * 1024:
        with open(path, "r", encoding="utf-8") as f:
            return f.readlines()[-max_lines:]

    # For larger files, read from the tail in chunks
    chunk = min(size, 8 * 1024 * 1024)
    with open(path, "rb") as f:
        f.seek(max(0, size - chunk))
        data = f.read().decode("utf-8", errors="replace")
    lines = data.splitlines(keepends=True)
    # First line may be partial -- drop it
    if lines and not data.startswith("\n") and size > chunk:
        lines = lines[1:]
    return lines[-max_lines:]


def read_events(
    categories=None,  # type: Optional[List[str]]
    level=None,       # type: Optional[str]
    search=None,      # type: Optional[str]
    since=None,       # type: Optional[str]
    until=None,       # type: Optional[str]
    limit=500,        # type: int
):
    # type: (...) -> List[Dict]
    """Return matching events from the audit stream, newest first.

    Parameters
    ----------
    categories : list of str or None
        Filter to these category strings (e.g. ["skill", "error"]).
    level : str or None
        Exact level match ("info", "warning", "error").
    search : str or None
        Case-insensitive substring match against the summary field.
    since, until : str or None
        ISO-8601 timestamp boundaries (inclusive).
    limit : int
        Maximum events to return (default 500).
    """
    lines = _tail_lines(AUDIT_STREAM)
    search_lower = search.lower() if search else None
    cats_set = set(categories) if categories else None
    # Normalize timezone suffixes for reliable string comparison
    _norm = lambda t: t.replace("+00:00", "Z").replace("+0000", "Z") if t else t
    since_n = _norm(since)
    until_n = _norm(until)

    results = []  # type: List[Dict]
    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue

        if cats_set and ev.get("cat") not in cats_set:
            continue
        if level and ev.get("lvl") != level:
            continue
        if search_lower and search_lower not in ev.get("sum", "").lower():
            continue
        ev_ts = _norm(ev.get("ts", ""))
        if since_n and ev_ts < since_n:
            continue
        if until_n and ev_ts > until_n:
            continue

        results.append(ev)
        if len(results) >= limit:
            break

    return results


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def get_stats(hours=24):
    # type: (int) -> Dict
    """Aggregate stats for the last *hours* hours."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    lines = _tail_lines(AUDIT_STREAM)

    total = 0
    errors = 0
    by_cat = {}  # type: Dict[str, int]
    by_lvl = {}  # type: Dict[str, int]

    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue

        if ev.get("ts", "") < cutoff:
            continue

        total += 1
        cat = ev.get("cat", "unknown")
        lvl = ev.get("lvl", "unknown")
        by_cat[cat] = by_cat.get(cat, 0) + 1
        by_lvl[lvl] = by_lvl.get(lvl, 0) + 1
        if lvl == "error":
            errors += 1

    return {
        "total_events": total,
        "errors_count": errors,
        "events_by_category": by_cat,
        "events_by_level": by_lvl,
    }
