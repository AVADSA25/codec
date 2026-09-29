"""Dictation through CODEC's local Whisper (UI phase 2, P2.5; docs/P2.5-DESIGN.md).

POST /api/transcribe takes one recording (multipart field `file`) from a mic
button and returns {"text", "dropped"}. It sits behind the dashboard login and
the CSRF check like every /api POST. The audio goes only to the configured
`stt_url` (whisper-stt on this Mac), with the configured model and no language
field, so a server that auto-detects can pick the spoken language. The body is
read with a hard cap, so an oversized upload is refused before Whisper is
called; nothing is kept or logged.
"""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile

from codec_voice_filters import discard_reason

router = APIRouter()
log = logging.getLogger("codec_dashboard")

MAX_BYTES = 8 * 1024 * 1024          # about 8 minutes of Opus; the recorder stops at 2
_BODY_LIMIT = MAX_BYTES + 64 * 1024  # room for the multipart framing
WHISPER_TIMEOUT = 120.0
DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"
_AUDIO_TYPES = ("audio/", "video/webm", "video/mp4")  # Chrome can label WebM audio as video/webm
_SUFFIX = (("mp4", ".m4a"), ("aac", ".m4a"), ("ogg", ".ogg"), ("wav", ".wav"), ("mpeg", ".mp3"), ("webm", ".webm"))
_TRANSPORT = None  # tests swap in an httpx.MockTransport

_NOT_RUNNING = "Dictation needs CODEC's speech service (whisper-stt) on the Mac, and it is not answering."
_UNREADABLE = "The speech service could not read this recording. Try again."


def _error(message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


async def _capped_body(request: Request):
    """The request body, or None once it passes _BODY_LIMIT (reading stops there)."""
    buf = bytearray()
    async for chunk in request.stream():
        buf.extend(chunk)
        if len(buf) > _BODY_LIMIT:
            return None
    return bytes(buf)


async def _whisper(audio: bytes, filename: str, content_type: str) -> httpx.Response:
    import codec_config
    model = (getattr(codec_config, "cfg", None) or {}).get("stt_model") or DEFAULT_MODEL
    async with httpx.AsyncClient(timeout=WHISPER_TIMEOUT, transport=_TRANSPORT) as client:
        return await client.post(codec_config.WHISPER_URL, data={"model": model},
                                 files={"file": (filename, audio, content_type)})


@router.post("/api/transcribe")
async def transcribe(request: Request):
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > _BODY_LIMIT:
        return _error("The recording is too long. Keep dictation under two minutes.", 413)
    body = await _capped_body(request)
    if body is None:
        return _error("The recording is too long. Keep dictation under two minutes.", 413)

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    try:
        form = await Request(request.scope, receive).form(max_files=1, max_fields=4)
    except Exception:
        return _error("Send the recording as multipart form data in a field named file.", 400)
    upload = form.get("file")
    if not isinstance(upload, UploadFile):
        return _error("Send the recording as multipart form data in a field named file.", 400)
    content_type = (upload.content_type or "").split(";")[0].strip().lower()
    if not content_type.startswith(_AUDIO_TYPES):
        return _error("Only audio recordings can be transcribed.", 415)
    audio = await upload.read()
    await upload.close()
    if not audio:
        return _error("The recording is empty.", 400)
    if len(audio) > MAX_BYTES:
        return _error("The recording is too long. Keep dictation under two minutes.", 413)
    suffix = next((s for key, s in _SUFFIX if key in content_type), ".webm")

    try:
        r = await _whisper(audio, "dictation" + suffix, content_type)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return _error(_NOT_RUNNING, 503)
    except httpx.HTTPError as e:
        log.warning("[transcribe] whisper request failed: %s", type(e).__name__)
        return _error(_UNREADABLE, 502)
    try:
        text = str((r.json() or {}).get("text") or "").strip() if r.status_code == 200 else None
    except ValueError:
        text = None
    # whisper-stt answers 200 with "Failed: <stderr>" when its CLI fails.
    if text is None or text.startswith("Failed:"):
        log.warning("[transcribe] whisper answered %s", r.status_code)
        return _error(_UNREADABLE, 502)
    reason = discard_reason(text)
    return {"text": "" if reason else text, "dropped": reason}
