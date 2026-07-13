"""CODEC Skill: Observer Recall — "what was I doing?"

CODEC's observer daemon watches the active window, screen text (OCR), clipboard,
and recent files, keeping the last ~10 minutes in a ring buffer. That buffer is
RAM-only inside the daemon, so chat/voice/terminal (different processes) couldn't
read it — asking "what was I doing 20 minutes ago?" returned "I don't have an
observer skill". The daemon now mirrors the buffer to ~/.codec/observer_buffer.json
and this skill reads it, filters to the time window you ask about, and summarises.

Understands windows like: "20 minutes ago", "the last hour", "5 min", "just now".
Defaults to the whole buffer if no window is given.
"""
SKILL_NAME = "observer_recall"
SKILL_DESCRIPTION = (
    "Recall what the user was recently doing on their Mac (active apps, windows, "
    "on-screen text, clipboard, files) from CODEC's observer buffer. Answers "
    "'what was I doing 20 minutes ago / in the last hour / just now'."
)
SKILL_TRIGGERS = [
    "what was i doing", "what was i working on", "what did i do",
    "what have i been doing", "recall what", "observer", "my recent activity",
    "what was on my screen", "remind me what i was",
]
SKILL_MCP_EXPOSE = False  # local recall of the user's screen activity; not for remote callers

import json
import os
import re
from datetime import datetime, timezone

_BUFFER_PATH = os.path.expanduser("~/.codec/observer_buffer.json")


def _load_entries() -> tuple[list, str]:
    """Return (entries, updated_iso). entries are oldest→newest snapshot dicts."""
    if not os.path.exists(_BUFFER_PATH):
        return [], ""
    try:
        with open(_BUFFER_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("entries", []) or [], data.get("updated", "")
    except (OSError, ValueError):
        return [], ""


def _window_seconds(task: str) -> int | None:
    """Parse a lookback window from the task. Returns seconds, or None for 'all'.

    'just now' / 'right now' → last 2 min. A bare number+unit → that span. No
    match → None (summarise the whole buffer)."""
    low = (task or "").lower()
    if "just now" in low or "right now" in low:
        return 120
    m = re.search(r"(\d+)\s*(second|sec|minute|min|hour|hr|h)s?\b", low)
    if not m:
        if "hour" in low:
            return 3600
        if "minute" in low or " min" in low:
            return 600
        return None
    n = int(m.group(1))
    unit = m.group(2)
    if unit.startswith("s"):
        return n
    if unit.startswith(("h",)):
        return n * 3600
    return n * 60  # minute


def _parse_ts(entry: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(entry["ts"].replace("Z", "+00:00"))
    except (KeyError, ValueError, AttributeError):
        return None


def _summarise(entries: list) -> str:
    """A compact, human timeline of the entries (oldest→newest)."""
    if not entries:
        return "nothing recorded in that window."

    lines: list[str] = []
    last_app = None
    apps_seen: list[str] = []
    files_seen: set[str] = set()
    ocr_bits: list[str] = []

    for e in entries:
        ts = _parse_ts(e)
        stamp = ts.astimezone().strftime("%H:%M") if ts else "??:??"
        win = e.get("active_window") or {}
        app = win.get("app")
        title = (win.get("title") or "").strip()
        if app and app != last_app:
            label = app + (f" — {title[:60]}" if title else "")
            lines.append(f"  {stamp}  {label}")
            last_app = app
            if app not in apps_seen:
                apps_seen.append(app)
        for rf in (e.get("recent_files") or []):
            p = rf.get("path") if isinstance(rf, dict) else rf
            if p:
                files_seen.add(os.path.basename(str(p)))
        ocr = (e.get("screenshot_ocr") or "").strip()
        if ocr and len(ocr) > 20:
            ocr_bits.append(ocr[:120])

    out = []
    if apps_seen:
        out.append("You were in: " + ", ".join(apps_seen[:6]) + ".")
    if lines:
        out.append("Timeline:\n" + "\n".join(lines[:12]))
    if files_seen:
        out.append("Files touched: " + ", ".join(sorted(files_seen)[:8]) + ".")
    if ocr_bits:
        out.append('On screen (excerpt): "' + ocr_bits[-1] + '"')
    return "\n".join(out)


def run(task: str, context: str = "") -> str:
    entries, updated = _load_entries()
    if not entries:
        return (
            "The observer has nothing recorded yet. Make sure the codec-observer "
            "service is running (pm2 status) and that Observer is enabled in "
            "~/.codec/config.json — it captures the active window, screen text, "
            "clipboard, and recent files each minute."
        )

    win = _window_seconds(task)
    if win is not None:
        cutoff = datetime.now(timezone.utc).timestamp() - win
        kept = [e for e in entries if (_parse_ts(e) or datetime.min.replace(tzinfo=timezone.utc)).timestamp() >= cutoff]
        span = f"the last {win // 60} min" if win >= 60 else f"the last {win}s"
    else:
        kept = entries
        span = "recent activity"

    if not kept:
        # Window older than the buffer keeps (RAM ~10 min). Be honest.
        oldest = _parse_ts(entries[0])
        depth = ""
        if oldest:
            mins = int((datetime.now(timezone.utc) - oldest).total_seconds() // 60)
            depth = f" The buffer only goes back about {mins} min."
        return (f"Nothing in {span} — the observer keeps roughly the last 10 "
                f"minutes, so I can't see that far back.{depth}")

    return f"Here's {span} (from CODEC's observer):\n{_summarise(kept)}"
