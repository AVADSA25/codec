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


# Absolute / clock-time asks the buffer can NEVER answer: it's a RAM ring of the
# last N snapshots (~10 min at the active cadence), not a day log. Without this
# check, "between 11am and 1pm" parsed as no-window → summarised the last few
# minutes and looked like a confident, wrong answer.
_ABSOLUTE_TIME_RE = re.compile(
    r"\b\d{1,2}\s*(?:am|pm)\b"
    r"|\b\d{1,2}:\d{2}\b"
    r"|\b(?:today|yesterday|this morning|this afternoon|this evening|"
    r"last night|earlier today|tonight)\b",
    re.IGNORECASE,
)


def _is_absolute_time_request(task: str) -> bool:
    """True for clock-time / calendar-day asks ("between 11am and 1pm", "today",
    "this morning") — windows the ring buffer structurally cannot cover."""
    return bool(_ABSOLUTE_TIME_RE.search(task or ""))


def _coverage(entries: list) -> str:
    """Human span the buffer actually covers, e.g. "06:59–07:32 (33 min)"."""
    if not entries:
        return "nothing"
    first, last = _parse_ts(entries[0]), _parse_ts(entries[-1])
    if not first or not last:
        return f"{len(entries)} snapshots"
    mins = max(0, int((last - first).total_seconds() // 60))
    return (f"{first.astimezone().strftime('%H:%M')}–"
            f"{last.astimezone().strftime('%H:%M')}, about {mins} min")


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


def _fmt_span(entries: list) -> str:
    """Natural opener for how long the snapshots span, e.g. "Over the last
    25 minutes" / "In the last couple of minutes". "" if it can't be derived."""
    if len(entries) < 2:
        return ""
    first, last = _parse_ts(entries[0]), _parse_ts(entries[-1])
    if not first or not last:
        return ""
    mins = int((last - first).total_seconds() // 60)
    if mins <= 1:
        return "In the last minute or so"
    if mins < 5:
        return "Over the last couple of minutes"
    return f"Over the last {mins} minutes"


def _summarise(entries: list) -> str:
    """A compact, human timeline of the entries (oldest→newest)."""
    if not entries:
        return "nothing recorded in that window."

    lines: list[str] = []
    last_app = None
    last_title_seen = ""
    apps_seen: list[str] = []
    files_seen: set[str] = set()
    ocr_bits: list[str] = []

    for e in entries:
        ts = _parse_ts(e)
        stamp = ts.astimezone().strftime("%H:%M") if ts else "??:??"
        win = e.get("active_window") or {}
        app = win.get("app")
        title = (win.get("title") or "").strip()
        if title:
            last_title_seen = title
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

    # Conversational prose, not a machine dump. The old format printed
    # "You were in: X. / Timeline: / 12:20 X / Files touched: y" — accurate but
    # robotic. CODEC should answer like a person who was watching over your
    # shoulder. Deterministic templating (no second LLM call) keeps it instant
    # and, crucially, keeps it from inventing anything.
    out = []
    if apps_seen:
        span = _fmt_span(entries)
        if len(apps_seen) == 1:
            body = f"you were in {apps_seen[0]} the whole time"
        elif len(apps_seen) == 2:
            body = f"you went back and forth between {apps_seen[0]} and {apps_seen[1]}"
        else:
            shown = apps_seen[:4]
            body = ("you moved around a fair bit — "
                    + ", ".join(shown[:-1]) + f" and {shown[-1]}")
        opener = f"{span}, {body}" if span else body[0].upper() + body[1:]
        if last_title_seen:
            opener += f". Last thing on screen was \"{last_title_seen[:70]}\""
        out.append(opener + ".")
    if files_seen:
        fl = sorted(files_seen)
        if len(fl) == 1:
            out.append(f"You touched one file: {fl[0]}.")
        else:
            out.append(f"You touched {len(fl)} files: " + ", ".join(fl[:6])
                       + ("…" if len(fl) > 6 else "") + ".")
    if ocr_bits:
        out.append(f"Text on screen included: \"{ocr_bits[-1].strip()}\"")
    if not out:
        # Entries exist but carry no window/title/OCR/file content — the observer
        # is polling but capturing nothing. This is almost always a macOS
        # permission gap (Screen Recording / Automation / Accessibility not
        # granted to the codec-observer process). Say so honestly rather than
        # returning an empty string that lets a caller fabricate an answer.
        return (
            f"The observer captured {len(entries)} snapshot(s) in that window but "
            "no window titles, on-screen text, or files — it's polling but seeing "
            "nothing. Grant the codec-observer process Screen Recording + "
            "Automation + Accessibility in System Settings → Privacy & Security, "
            "then restart it (pm2 restart codec-observer)."
        )
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

    # Clock-time / calendar asks ("between 11am and 1pm", "today", "this
    # morning") are structurally unanswerable: the observer is a RAM ring of the
    # last N snapshots, not a day log. Say so plainly — the old code fell through
    # to "summarise everything" and returned the last few minutes as if it had
    # answered the question.
    if _is_absolute_time_request(task):
        return (
            f"I can't recall that window. The observer is a rolling in-memory "
            f"buffer of the last {len(entries)} snapshots — right now it only "
            f"covers {_coverage(entries)} — not a full-day log, and it's wiped "
            f"whenever the service restarts. Here's everything it currently "
            f"holds:\n\n{_summarise(entries)}"
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

    # _summarise already opens conversationally ("Over the last 25 minutes, you
    # were in Claude…"), so no robotic "Here's X (from CODEC's observer):" header.
    return _summarise(kept)
