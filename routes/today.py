"""Today cards API (UI phase 3, P3.3 and P3.1; docs/P3.3-DESIGN.md, docs/P3.1-DESIGN.md), and
the Today home (P3.4, docs/P3.4-DESIGN.md): open threads, agents, yesterday's shift report, the
next calendar events and the quick tiles.
Behind the dashboard login like every /api route; POSTs are CSRF-checked by AuthMiddleware."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import codec_today

router = APIRouter()


@router.get("/api/today")
def today_cards():
    return {"cards": codec_today.list_cards()}


@router.post("/api/today/threads/done")
async def thread_done(request: Request):
    """Close a Daybreak thread shown on a briefing card, and drop it from the card."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    key, card_id = str((body or {}).get("key") or ""), str((body or {}).get("card_id") or "")
    if not re.fullmatch(r"thread:[a-z_]+:[\w-]{1,120}", key):
        return JSONResponse({"error": "Not a thread."}, status_code=400)
    import codec_daybreak
    result = codec_daybreak.close_thread(key)
    if card_id:
        codec_today.drop_thread(card_id, key)
    return {"ok": result.startswith("Closed"), "message": result}


@router.post("/api/today/threads/snooze")
async def thread_snooze(request: Request):
    """Hide an open thread from Today for some hours (P3.4); the fact itself is untouched."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    key = str((body or {}).get("key") or "")
    if not re.fullmatch(r"thread:[a-z_]+:[\w-]{1,120}", key):
        return JSONResponse({"error": "Not a thread."}, status_code=400)
    try:
        hours = int((body or {}).get("hours", 24))
    except (TypeError, ValueError):
        return JSONResponse({"error": "hours must be a number"}, status_code=400)
    if not 1 <= hours <= 168:
        return JSONResponse({"error": "hours must be between 1 and 168"}, status_code=400)
    return {"ok": True, "until": codec_today.snooze_thread(key, hours)}


# ── Today home (P3.4) ─────────────────────────────────────────────────────────
_ACTIVE = {"running", "paused", "approved", "crashed_resumed", "awaiting_approval"}
_FINISHED = {"completed", "aborted", "plan_failed"}


def _epoch(value) -> float:
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return 0.0
    return (dt if dt.tzinfo else dt.astimezone()).timestamp()


def _home_threads() -> list:
    import codec_daybreak
    hidden = codec_today.snoozed_threads()
    return [{"key": t["key"], "kind": t["kind"], "text": str(t.get("text") or "")[:300], "since": t.get("since") or ""}
            for t in codec_daybreak.get_open_threads() if t.get("key") not in hidden][:12]


def _home_agents(now: float | None = None) -> list:
    import codec_agent_plan as cap
    now = now or time.time()
    out = []
    try:
        dirs = [d for d in cap._AGENTS_DIR.iterdir() if d.is_dir()] if cap._AGENTS_DIR.exists() else []
    except OSError:
        dirs = []
    for d in dirs:
        m = cap.load_manifest(d.name)
        if not m:
            continue
        status = str(m.get("status") or "")
        when = m.get("updated_at") or m.get("created_at") or ""
        active = status in _ACTIVE or status.startswith("blocked")
        if active or (status in _FINISHED and now - _epoch(when) <= 24 * 3600):
            out.append({"agent_id": str(m.get("agent_id") or d.name), "title": str(m.get("title") or "")[:120],
                        "status": status, "updated_at": when, "active": active})
    out.sort(key=lambda a: (a["active"], _epoch(a["updated_at"])), reverse=True)
    return out[:6]


def _home_yesterday(today=None) -> dict | None:
    """The first lines of yesterday's shift report. Reads the file as it is (no sample data)."""
    from routes import _shared
    from routes.inbox import _item_id
    try:
        with open(_shared.NOTIFICATIONS_PATH) as f:
            notifs = json.load(f)
    except (OSError, ValueError):
        return None
    day = ((today or datetime.now().date()) - timedelta(days=1)).isoformat()
    for n in notifs if isinstance(notifs, list) else []:
        if isinstance(n, dict) and n.get("type") == "shift_report" and str(n.get("created") or "").startswith(day):
            lines = []
            for raw in str(n.get("body") or "").splitlines():
                s = raw.strip().lstrip("#").strip().lstrip("-*").strip()
                if s and len(lines) < 4:
                    lines.append(s[:200])
            return {"id": _item_id(n), "title": str(n.get("title") or "Shift report")[:120], "lines": lines}
    return None


@router.get("/api/today/home")
def today_home():
    """The Today home's own sections (P3.4); Needs you comes from the shell's Inbox poll."""
    return {"threads": _home_threads(), "agents": _home_agents(), "yesterday": _home_yesterday()}


# Next up: today's calendar through the calendar skill (the chat's explicit-skill path:
# allowlist, consent, hooks, audit), parsed, cached for five minutes.
_NEXT = {"at": 0.0, "data": None}
NEXT_TTL = 300
_EVENT = re.compile(r"^\s+(\d{1,2}:\d{2} [AP]M|All day) \u2014 (.+)$")


def _parse_events(text: str, now: datetime | None = None) -> dict:
    text = str(text or "")
    if text.startswith("Calendar error") or "can't be run" in text:
        return {"events": [], "connected": False, "note": "The calendar is not connected."}
    now = now or datetime.now()
    all_day, timed = [], []
    for line in text.splitlines():
        m = _EVENT.match(line)
        if not m:
            continue
        when, title = m.group(1), m.group(2).strip()[:120]
        if when == "All day":
            all_day.append({"time": "All day", "title": title})
            continue
        t = datetime.strptime(when, "%I:%M %p").replace(year=now.year, month=now.month, day=now.day)
        if t >= now - timedelta(minutes=10):  # one that just started still counts
            timed.append({"time": when, "title": title})
    events = (all_day + timed)[:3]
    return {"events": events, "connected": True, "note": "" if events else "Nothing more today."}


@router.get("/api/today/next")
def today_next():
    if _NEXT["data"] is not None and time.time() - _NEXT["at"] < NEXT_TTL:
        return _NEXT["data"]
    from routes.chat import _try_explicit_skill
    _, result = _try_explicit_skill("google_calendar", "what's on my calendar today")
    data = _parse_events(result)
    _NEXT.update(at=time.time(), data=data)
    return data


# Quick tiles: the client names a tile; the skill and its words are fixed here.
TILES = {
    "now_playing": ("music", "what is playing"),
    "music_pause": ("music", "pause music"),
    "music_resume": ("music", "resume music"),
    "lights_relax": ("philips_hue", "relax mode"),
    "lights_off": ("philips_hue", "lights off"),
    "volume_down": ("volume_brightness", "volume down"),
    "volume_up": ("volume_brightness", "volume up"),
    "timer": ("timer", "set a timer for {minutes} minutes"),
}


@router.post("/api/today/tile")
async def today_tile(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    tile = str((body or {}).get("tile") or "")
    if tile not in TILES:
        return JSONResponse({"error": "Unknown tile."}, status_code=400)
    skill, task = TILES[tile]
    if "{minutes}" in task:
        try:
            minutes = int((body or {}).get("minutes"))
        except (TypeError, ValueError):
            minutes = 0
        if not 1 <= minutes <= 240:
            return JSONResponse({"error": "minutes must be between 1 and 240"}, status_code=400)
        task = task.format(minutes=minutes)
    import asyncio

    from routes.chat import _try_explicit_skill
    name, result = await asyncio.to_thread(_try_explicit_skill, skill, task)
    return {"tile": tile, "skill": name, "result": str(result or "")[:500]}


@router.get("/api/today/{card_id}")
def get_card(card_id: str):
    card = codec_today.get_card(card_id)
    if not card:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return card


@router.post("/api/today/{card_id}/dismiss")
def dismiss_card(card_id: str):
    if not codec_today.dismiss(card_id):
        return JSONResponse({"error": "Not found"}, status_code=404)
    return {"ok": True}


@router.post("/api/today/{card_id}/snooze")
async def snooze_card(card_id: str, request: Request):
    try:
        minutes = int(((await request.json()) or {}).get("minutes", 120))
    except Exception:
        minutes = 120
    if not codec_today.snooze(card_id, minutes):
        return JSONResponse({"error": "Not found"}, status_code=404)
    return {"ok": True}
