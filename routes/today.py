"""Today cards API (UI phase 3, P3.3 and P3.1; docs/P3.3-DESIGN.md, docs/P3.1-DESIGN.md).
Behind the dashboard login like every /api route; POSTs are CSRF-checked by AuthMiddleware."""
from __future__ import annotations

import re

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
