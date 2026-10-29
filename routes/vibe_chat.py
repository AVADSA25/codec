"""CODEC Vibe chat route (docs/VIBE-CHAT-ROUTE-DESIGN.md, D-22, P-042).

Thin HTTP layer over codec_vibe_chat. Sits behind the dashboard's normal login, CSRF and Host checks like every
other /api route (codec_dashboard.AuthMiddleware). Vibe chat never uses the general chat's persona, memory,
skills or web: this module imports none of them.

``config.json`` ``vibe.chat_route`` = ``"chat"`` is the rollback: this route then answers 404 and the page goes
back to /api/chat. Read at every request, so no restart is needed.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse

import codec_llm
import codec_vibe_chat as vc

router = APIRouter()


def _off():
    return JSONResponse({"error": "Vibe chat has its own route switched off (vibe.chat_route)"}, status_code=404)


@router.get("/api/vibe/chat/status")
async def status():
    return {"route": vc.route_mode()}


@router.post("/api/vibe/chat")
async def chat(request: Request):
    if vc.route_mode() == "chat":
        return _off()
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        return JSONResponse({"error": "Send a JSON object"}, status_code=400)
    try:
        prepared = await asyncio.to_thread(vc.prepare, body)
    except vc.ChatError as e:
        return JSONResponse({"error": e.message}, status_code=e.status)
    vc.audit_sent(prepared)
    if prepared.stream:
        return StreamingResponse(vc.sse_stream(prepared), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    try:
        return await asyncio.to_thread(vc.complete, prepared)
    except codec_llm.LLMError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
