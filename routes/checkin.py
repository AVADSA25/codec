"""Proactive check-in settings (UI phase 3, P3.5; docs/P3.5-DESIGN.md).
Behind the dashboard login like every /api route; PUT is CSRF-checked."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import codec_checkin

router = APIRouter()


@router.get("/api/checkin")
def checkin_settings():
    return {**codec_checkin.settings(), **codec_checkin.status()}


@router.put("/api/checkin")
async def checkin_update(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        return JSONResponse({"error": "Send the settings as a JSON object."}, status_code=400)
    try:
        saved = codec_checkin.save_settings(body)
    except (ValueError, TypeError) as e:
        return JSONResponse({"error": str(e) or "Those settings are not valid."}, status_code=400)
    return {**saved, **codec_checkin.status()}
