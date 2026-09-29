"""Morning briefing settings and controls (UI phase 3, P3.1; docs/P3.1-DESIGN.md).
Behind the dashboard login like every /api route; POST/PUT are CSRF-checked."""
from __future__ import annotations

import threading

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import codec_briefing

router = APIRouter()


@router.get("/api/briefing")
def briefing_settings():
    return codec_briefing.settings()


@router.put("/api/briefing")
async def briefing_update(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        return JSONResponse({"error": "Send the settings as a JSON object."}, status_code=400)
    try:
        return codec_briefing.update({k: body[k] for k in ("enabled", "when", "speak") if k in body})
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.post("/api/briefing/run")
def briefing_run_now():
    """Try it now: run the briefing job at once (it must be set up)."""
    import codec_scheduler
    job = next((s for s in codec_scheduler.load_schedules() if s.get("id") == codec_briefing.BRIEFING_ID), None)
    if not job:
        return JSONResponse({"error": "Switch the briefing on first."}, status_code=409)
    threading.Thread(target=codec_scheduler.run_scheduled, args=(job,), kwargs={"manual": True},
                     name="codec-briefing-run", daemon=True).start()
    return {"status": "running"}


@router.post("/api/briefing/stop")
def briefing_stop():
    return {"ok": True, "was_speaking": codec_briefing.stop()}
