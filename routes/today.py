"""Today cards API (UI phase 3, P3.3; docs/P3.3-DESIGN.md). Behind the dashboard
login like every /api route; the POST is CSRF-checked by AuthMiddleware."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

import codec_today

router = APIRouter()


@router.get("/api/today")
def today_cards():
    return {"cards": codec_today.list_cards()}


@router.post("/api/today/{card_id}/dismiss")
def dismiss_card(card_id: str):
    if not codec_today.dismiss(card_id):
        return JSONResponse({"error": "Not found"}, status_code=404)
    return {"ok": True}
