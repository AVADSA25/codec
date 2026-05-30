"""CODEC vision API route — Qwen Vision model proxy with chat persistence.

F4 / SR-53: extracted from codec_dashboard.py. Sends an image to the
local vision model, returns the description, and optionally persists
both the user prompt (with a thumbnail sentinel) and the assistant
reply to the conversations table so the chat panel renders them.
"""
from __future__ import annotations

import json
import logging
import re as _re_b64
from datetime import datetime

import requests as rq
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from codec_audit import log_event
from routes._shared import CONFIG_PATH, _audit_write, get_db

router = APIRouter()
log = logging.getLogger("codec_dashboard")


@router.post("/api/vision")
async def vision_analyze(request: Request):
    """Send image to Qwen Vision model for analysis.

    If `session_id` is provided, the user prompt + assistant response are
    persisted to the conversations table so the chat panel renders them.
    A small `thumb` (base64 jpeg, max ~256px on the client side) can be
    embedded inline in the user message via a `[CODEC_IMG_THUMB:...]`
    sentinel — loadChat() in codec_dashboard.html parses the sentinel
    and renders the thumbnail. Storage cost: ~10–20 KB per image message.
    """
    body = await request.json()
    image_b64 = body.get("image", "")
    prompt = body.get("prompt", "Describe and analyze this image in detail.")
    session_id = (body.get("session_id") or "").strip()
    thumb_b64 = body.get("thumb", "")
    if not image_b64:
        return JSONResponse({"error": "No image data"}, status_code=400)
    try:
        config = {}
        try:
            with open(CONFIG_PATH) as f:
                config = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            log.warning(f"Config read failed; proceeding without overrides: {e}")
        vision_url = config.get("vision_base_url", "http://localhost:8083/v1")
        vision_model = config.get("vision_model", "mlx-community/Qwen2.5-VL-7B-Instruct-4bit")
        payload = {
            "model": vision_model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                    {"type": "text", "text": prompt}
                ]
            }],
            "max_tokens": 4000,
            "temperature": 0.7
        }
        headers = {"Content-Type": "application/json"}
        r = rq.post(f"{vision_url}/chat/completions", json=payload, headers=headers, timeout=120)
        data = r.json()
        answer = data["choices"][0]["message"]["content"].strip()
        _audit_write(f"[{datetime.now().isoformat()}] VISION: {prompt[:100]}\n")
        log_event("chat_vision", "codec-dashboard",
                  f"Vision analysis: {prompt[:60]}",
                  extra={"prompt_preview": prompt[:200]})

        # Persist user msg (with thumbnail sentinel) + assistant msg so the
        # image appears in the chat panel. Defensive: never let a save error
        # break the vision response — log + return the answer regardless.
        if session_id:
            try:
                now_iso = datetime.now().isoformat()
                # Cap thumbnail size to ~60KB of base64 (about a 256x256 jpeg
                # @ q=0.4); silently drop if larger to keep DB rows lean.
                # Validate the whole string is base64 so any injection attempt
                # (e.g. ']<script>') falls through to the no-thumb branch.
                _is_b64 = bool(thumb_b64) and len(thumb_b64) <= 60_000 \
                    and _re_b64.fullmatch(r'[A-Za-z0-9+/]+={0,2}', thumb_b64) is not None
                if _is_b64:
                    user_content = (
                        (prompt or "Analyze this image")[:2000]
                        + f"\n[CODEC_IMG_THUMB:data:image/jpeg;base64,{thumb_b64}]"
                    )
                else:
                    user_content = (prompt or "Analyze this image")[:2000]
                c = get_db()
                c.execute(
                    "INSERT INTO conversations (session_id, timestamp, role, content) VALUES (?,?,?,?)",
                    (session_id, now_iso, "user", user_content),
                )
                c.execute(
                    "INSERT INTO conversations (session_id, timestamp, role, content) VALUES (?,?,?,?)",
                    (session_id, now_iso, "assistant", answer[:5000]),
                )
                c.commit()
            except Exception as save_err:
                log.warning(f"[Vision] save to conversations failed: {save_err}")

        return {"response": answer, "model": vision_model}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)
