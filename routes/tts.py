"""CODEC TTS + response polling API routes.

F3 / SR-52: extracted from codec_dashboard.py. /api/tts proxies to
Kokoro; /api/response is the long-poll endpoint for the Flash Chat
request_id correlation (C-2 / PR-4B).

P2.8 (docs/P2.8-DESIGN.md): every /api/tts request gets its own audio bytes
(the shared ~/.codec/pwa_audio.mp3 is gone), POST takes up to 1000 characters
with an optional listed voice and a speed, and the handlers are plain `def` so
the Kokoro call runs in the thread pool. /api/tts/voices lists Kokoro's
built-in voices from the local model cache plus ~/.codec/voices/kokoro/.

The _latest_response_for_session helper lives here too — it's only
called from /api/response.
"""
from __future__ import annotations

import glob
import json
import logging
import os
from typing import Optional

import requests as rq
from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

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


_DEFAULT_MODEL = "mlx-community/Kokoro-82M-bf16"
_DEFAULT_VOICE = "am_adam"
_DEFAULT_SPEED = 1.1
POST_MAX_CHARS = 1000
GET_MAX_CHARS = 500
CUSTOM_VOICE_DIR = os.path.expanduser("~/.codec/voices/kokoro")
_LANGS = {"a": "American English", "b": "British English", "e": "Spanish", "f": "French", "h": "Hindi",
          "i": "Italian", "j": "Japanese", "p": "Brazilian Portuguese", "z": "Mandarin Chinese"}


def _tts_config() -> dict:
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.warning(f"Config read failed; proceeding without overrides: {e}")
        return {}


def _hf_hub() -> str:
    return os.environ.get("HF_HUB_CACHE") or os.path.join(
        os.environ.get("HF_HOME") or os.path.expanduser("~/.cache/huggingface"), "hub")


def voice_catalog(config: Optional[dict] = None) -> list:
    """Kokoro's built-in voices found in the local model cache, then the custom
    voices in CUSTOM_VOICE_DIR (their id is the file path, as tts_voice stores it)."""
    config = config if config is not None else _tts_config()
    model = str(config.get("tts_model") or _DEFAULT_MODEL)
    pattern = os.path.join(_hf_hub(), "models--" + model.replace("/", "--"), "snapshots", "*", "voices", "*.safetensors")
    names = sorted({os.path.basename(p)[:-len(".safetensors")] for p in glob.glob(pattern)})
    voices = []
    for n in names:
        if len(n) < 4 or n[2] != "_":
            continue
        lang, gender = _LANGS.get(n[0]), {"f": "female", "m": "male"}.get(n[1])
        label = n[3:].replace("_", " ").title()
        voices.append({"id": n, "label": f"{label}, {lang or 'other'}, {gender or 'voice'}",
                       "group": "English" if n[0] in "ab" else "Other languages", "custom": False})
    for p in sorted(glob.glob(os.path.join(CUSTOM_VOICE_DIR, "*.safetensors"))):
        stem = os.path.basename(p)[:-len(".safetensors")]
        voices.append({"id": p, "label": f"Your voice: {stem}", "group": "Your voices", "custom": True})
    return voices


def _speak(text: str, voice: str, speed: float, config: dict):
    """Kokoro's audio for `text` as a Response of its own, or a JSON error."""
    url = config.get("tts_url", "http://localhost:8085/v1/audio/speech")
    try:
        r = rq.post(url, json={"model": config.get("tts_model", _DEFAULT_MODEL), "input": text,
                               "voice": voice, "speed": speed}, timeout=60)
    except rq.RequestException as e:
        log.warning("[tts] Kokoro unreachable: %s", type(e).__name__)
        return JSONResponse({"error": "Read aloud needs CODEC's voice service (kokoro-82m) on the Mac, "
                                      "and it is not answering."}, status_code=503)
    if r.status_code != 200 or not r.content:
        return JSONResponse({"error": "The voice service could not read this text."}, status_code=502)
    kind = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    media = "audio/mpeg" if kind in ("", "audio/mp3", "audio/mpeg") or not kind.startswith("audio/") else kind
    return Response(content=r.content, media_type=media, headers={"Cache-Control": "no-store"})


@router.get("/api/tts")
def tts(text: str = ""):
    """Speech for a short text (old pages): the first GET_MAX_CHARS characters."""
    if not text.strip():
        return JSONResponse({"error": "No text"}, status_code=400)
    config = _tts_config()
    try:
        speed = float(config.get("tts_speed", _DEFAULT_SPEED))
    except (TypeError, ValueError):
        speed = _DEFAULT_SPEED
    return _speak(text[:GET_MAX_CHARS], str(config.get("tts_voice") or _DEFAULT_VOICE), speed, config)


@router.post("/api/tts")
def tts_post(payload: dict | None = None):
    """Speech for up to POST_MAX_CHARS characters, in a listed voice and a speed of 0.5-2.0."""
    body = payload if isinstance(payload, dict) else {}
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        return JSONResponse({"error": "No text"}, status_code=400)
    if len(text) > POST_MAX_CHARS:
        return JSONResponse({"error": f"Send at most {POST_MAX_CHARS} characters at a time."}, status_code=413)
    config = _tts_config()
    voice = body.get("voice")
    current = str(config.get("tts_voice") or _DEFAULT_VOICE)
    if voice is None or voice == current:
        voice = current
    elif not isinstance(voice, str) or voice not in {v["id"] for v in voice_catalog(config)}:
        return JSONResponse({"error": "Unknown voice."}, status_code=400)
    speed = body.get("speed", config.get("tts_speed", _DEFAULT_SPEED))
    try:
        speed = float(speed)
    except (TypeError, ValueError):
        return JSONResponse({"error": "speed must be a number"}, status_code=400)
    if not 0.5 <= speed <= 2.0:
        return JSONResponse({"error": "speed must be between 0.5 and 2.0"}, status_code=400)
    return _speak(text, voice, speed, config)


@router.get("/api/tts/voices")
def tts_voices():
    config = _tts_config()
    try:
        speed = float(config.get("tts_speed", _DEFAULT_SPEED))
    except (TypeError, ValueError):
        speed = _DEFAULT_SPEED
    return {"current": str(config.get("tts_voice") or _DEFAULT_VOICE), "speed": speed,
            "voices": voice_catalog(config)}
