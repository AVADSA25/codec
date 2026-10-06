"""CODEC Observer API routes.

C5 / SR-40: extracted from codec_dashboard.py. Single endpoint exposing
the live observer ring buffer for debugging. Auth-gated (by the
dashboard's existing /api/* middleware) AND debug-flag gated. Emits an
`observer_buffer_inspected` audit event so privileged reads stay visible
in the audit log. NOT linked from the main UI — operator-only.

Return shape redacts the raw entries (which contain window titles, OCR
text, clipboard content) and exposes only metadata + a rendered summary.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/api/observer/buffer")
async def observer_buffer(request: Request, debug: int = 0):
    """Return the current ring buffer state. Q5.6 design: debug-only,
    auth-gated, audit-emitting."""
    if int(debug) != 1:
        return {"error": "set ?debug=1 to read live observer buffer"}
    try:
        from codec_observer import get_global_buffer
        from codec_audit import OBSERVER_BUFFER_INSPECTED, log_event as _le
        buf = get_global_buffer()
        snap = buf.snapshot()
        try:
            client_ip = request.client.host if request.client else "unknown"
        except Exception:
            client_ip = "unknown"
        try:
            _le(
                OBSERVER_BUFFER_INSPECTED, "codec-dashboard",
                "observer buffer inspected via /api/observer/buffer",
                extra={
                    "client_ip": client_ip,
                    "buffer_entries_returned": len(snap),
                },
                outcome="ok", level="info",
            )
        except Exception:
            pass
        # Return only the metadata + a redacted summary, NOT the raw entries
        # (raw entries contain titles + OCR text + clipboard content).
        return {
            "buffer_depth": len(snap),
            "summary": buf.render_summary(),
            "oldest_ts": snap[0].get("ts") if snap else None,
            "newest_ts": snap[-1].get("ts") if snap else None,
        }
    except Exception as e:
        return {"error": f"observer not available: {e}"}


# ── 'CODEC is watching' (UI P3.7, docs/P3.7-DESIGN.md) ─────────────────────
# The observer runs in its own process; these read what it leaves on disk (the
# mirror, ~/.codec/observer_buffer.json) and write the pause flag it reads.
WATCH_FRESH_S = 11 * 60  # it polls at least every 5 minutes when you are idle


def _mirror():
    import json as _json
    from codec_observer import _BUFFER_DISK_PATH
    try:
        st = _BUFFER_DISK_PATH.stat()
        with open(_BUFFER_DISK_PATH, encoding="utf-8") as f:
            data = _json.load(f)
    except (OSError, ValueError):
        return None, None
    return (data if isinstance(data, dict) else None), st.st_mtime


def _audit(event: str, message: str, extra: dict):
    try:
        from codec_audit import log_event as _le
        _le(event, "codec-dashboard", message, extra=extra, outcome="ok", level="info")
    except Exception:
        pass


@router.get("/api/observer/state")
async def observer_state():
    """Watching (the mirror is fresh), paused (the flag), or neither."""
    import time as _time

    from codec_observer import paused_until
    held = paused_until()
    data, mtime = _mirror()
    watching = held is None and mtime is not None and _time.time() - mtime < WATCH_FRESH_S
    return {"watching": watching, "paused_until": held.strftime("%Y-%m-%dT%H:%M:%S") if held else None,
            "updated": (data or {}).get("updated") if watching else None,
            "entries": len((data or {}).get("entries") or []) if watching else 0}


@router.post("/api/observer/pause")
async def observer_pause(request: Request):
    """{"minutes": 15 or 60} or {"until": "tomorrow"} (06:00 tomorrow, local)."""
    from datetime import datetime as _dt, timedelta as _td

    import codec_observer
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    now = _dt.now()
    if body.get("until") == "tomorrow":
        until = (now + _td(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)
    elif body.get("minutes") in (15, 60):
        until = now + _td(minutes=int(body["minutes"]))
    else:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": 'Give "minutes": 15 or 60, or "until": "tomorrow".'}, status_code=400)
    codec_observer.pause(until)
    _audit("observer_paused", "Observer paused from the pages", {"until": until.strftime("%Y-%m-%dT%H:%M")})
    return {"paused_until": until.strftime("%Y-%m-%dT%H:%M:%S")}


@router.post("/api/observer/resume")
async def observer_resume():
    import codec_observer
    codec_observer.resume()
    _audit("observer_resumed", "Observer resumed from the pages", {})
    return {"ok": True}


@router.get("/api/observer/now")
async def observer_now():
    """What the observer keeps right now, as metadata only: the frontmost app, the
    lengths of the window title and the screen text, the clipboard's type and length,
    how many files changed. Never titles, screen text, clipboard text or paths."""
    from codec_audit import OBSERVER_BUFFER_INSPECTED
    from codec_observer import paused_until
    held = paused_until()
    data, _ = _mirror()
    entries = [] if held is not None else ((data or {}).get("entries") or [])
    latest = entries[-1] if entries and isinstance(entries[-1], dict) else None
    out = {"paused_until": held.strftime("%Y-%m-%dT%H:%M:%S") if held else None,
           "updated": (data or {}).get("updated") if entries else None, "entries": len(entries), "latest": None}
    if latest:
        win = latest.get("active_window") or {}
        cb = latest.get("clipboard") or None
        out["latest"] = {
            "at": str(latest.get("ts") or ""),
            "app": str(win.get("app") or "")[:60],
            "title_length": len(str(win.get("title") or "")),
            "screen_text_length": len(str(latest.get("screenshot_ocr") or "")),
            "screen_read": not latest.get("ocr_skipped", False),
            "clipboard": {"type": str(cb.get("content_type") or "text"), "length": int(cb.get("length") or 0)}
            if isinstance(cb, dict) else None,
            "recent_files": len(latest.get("recent_files") or []),
        }
    _audit(OBSERVER_BUFFER_INSPECTED, "observer metadata read via /api/observer/now",
           {"buffer_entries_returned": len(entries), "source": "now"})
    return out
