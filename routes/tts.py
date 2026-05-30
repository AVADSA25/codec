"""CODEC TTS + response polling API routes.

F3 / SR-52: extracted from codec_dashboard.py. /api/tts proxies to
Kokoro; /api/response is the long-poll endpoint for the Flash Chat
request_id correlation (C-2 / PR-4B).

The _latest_response_for_session helper lives here too — it's only
called from /api/response.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

import requests as rq
from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from routes._shared import CONFIG_PATH, get_db

router = APIRouter()
log = logging.getLogger("codec_dashboard")


def _latest_response_for_session(db, session_id, after_id="", after_ts="") -> Optional[str]:
    """Newest assistant reply for the caller's turn, or None. (C-2 / PR-4B.)

    Correlation is server-authoritative via conversations.id (`after_id` = the
    user row's autoincrement id, returned to the client as request_id). The
    turn's assistant row always has id > after_id, so `id > after_id ORDER BY id
    ASC LIMIT 1` selects the immediate-next assistant reply — no client-clock dep,
    exactly correct for the dominant single-tab + sequential flows. This
    replaces the racy ~/.codec/pwa_response.json file (non-atomic write, no
    writer mutex, no correlation, racy mtime/unlink) AND the latent
    clock/RTT-skew miss of the old `timestamp > after` query.

    `after_ts` (a wall-clock string) is a backward-compat fallback for an
    un-refreshed PWA tab that predates after_id. Never raises."""
    if not session_id:
        return None
    try:
        aid = int(after_id or 0)
    except (TypeError, ValueError):
        aid = 0
    try:
        if aid > 0:
            row = db.execute(
                "SELECT content FROM conversations "
                "WHERE session_id=? AND role='assistant' AND id>? "
                "ORDER BY id ASC LIMIT 1",
                (session_id, aid),
            ).fetchone()
        elif after_ts:
            row = db.execute(
                "SELECT content FROM conversations "
                "WHERE session_id=? AND role='assistant' AND timestamp>? "
                "ORDER BY timestamp DESC LIMIT 1",
                (session_id, after_ts),
            ).fetchone()
        else:
            return None
        if row and row[0]:
            return row[0]
        return None
    except Exception:
        return None


@router.get("/api/response")
async def get_response(session_id: str = "", after: str = "", after_id: str = ""):
    """Get the PWA command response from the conversations DB (C-2 / PR-4B).

    Correlation is server-authoritative via `after_id` (= the request_id the
    /api/command response carried = the user row's conversations.id). `after`
    (legacy wall-clock timestamp) is kept only as a fallback for an un-refreshed
    PWA tab. The old ~/.codec/pwa_response.json file path is gone."""
    headers = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"}
    try:
        ans = _latest_response_for_session(get_db(), session_id, after_id=after_id, after_ts=after)
        if ans:
            log.info(f"[Response] Delivered (db): {str(ans)[:80]}")
            return JSONResponse(content={"response": ans}, headers=headers)
        return JSONResponse(content={"response": None}, headers=headers)
    except Exception as e:
        log.warning(f"[Response] Error reading response: {e}")
        return JSONResponse(content={"response": None}, headers=headers)


@router.get("/api/tts")
async def tts(text: str = ""):
    """Generate speech and return audio file."""
    if not text:
        return JSONResponse({"error": "No text"}, status_code=400)
    try:
        config = {}
        try:
            with open(CONFIG_PATH) as f:
                config = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            log.warning(f"Config read failed; proceeding without overrides: {e}")
        tts_url = config.get("tts_url", "http://localhost:8085/v1/audio/speech")
        tts_model = config.get("tts_model", "mlx-community/Kokoro-82M-bf16")
        tts_voice = config.get("tts_voice", "am_adam")
        r = rq.post(tts_url, json={"model": tts_model, "input": text[:500], "voice": tts_voice, "speed": 1.1}, timeout=30)
        if r.status_code == 200:
            audio_path = os.path.expanduser("~/.codec/pwa_audio.mp3")
            with open(audio_path, "wb") as f:
                f.write(r.content)
            return FileResponse(audio_path, media_type="audio/mpeg")
        return JSONResponse({"error": "TTS failed"}, status_code=500)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
