#!/usr/bin/env python3
"""CODEC Whisper STT Server — runs on port 8084
Usage: python3 whisper_server.py
Binds 127.0.0.1 by default (no auth on this endpoint). Set CODEC_STT_HOST
to another address only if a trusted LAN client must reach it.
"""
import mlx_whisper
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse
import math
import shutil
import tempfile
import os
import uvicorn

app = FastAPI()
MODEL = os.environ.get("WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo")
SAMPLE_RATE = 16000


def _audio_input(path):
    """What to hand mlx_whisper: the file path, or decoded samples when ffmpeg is absent.

    mlx_whisper reads a path by running the ffmpeg command, and a buyer's Mac has no
    ffmpeg (GPL, not shipped). Where ffmpeg exists the path goes through as before, so
    any format it reads still works. Without it the file is decoded here (WAV, FLAC and
    the other formats soundfile reads) to 16 kHz mono float samples. Raises ValueError
    for audio that cannot be read that way.
    """
    if shutil.which("ffmpeg"):
        return path
    import soundfile as sf
    try:
        data, rate = sf.read(path, dtype="float32", always_2d=True)
    except Exception as e:
        raise ValueError(f"cannot read this audio without ffmpeg: {e}") from e
    mono = data.mean(axis=1)
    if rate != SAMPLE_RATE:
        from scipy.signal import resample_poly
        g = math.gcd(rate, SAMPLE_RATE)
        mono = resample_poly(mono, SAMPLE_RATE // g, rate // g).astype("float32")
    return mono


@app.post("/v1/audio/transcriptions")
async def transcribe(file: UploadFile = File(...), pin_language: str = Form("")):
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.write(await file.read())
    tmp.close()
    try:
        try:
            audio = _audio_input(tmp.name)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=415)
        # pin_language (dictation sends "en") skips language detection; other clients
        # do not send it and keep the automatic detection they always had.
        extra = {"language": pin_language} if pin_language else {}
        result = mlx_whisper.transcribe(audio, path_or_hf_repo=MODEL, **extra)
        text = result.get("text", "").strip()
        return {"text": text}
    finally:
        os.unlink(tmp.name)

@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL}

if __name__ == "__main__":
    print(f"[Whisper] Starting server with model: {MODEL}")
    host = os.environ.get("CODEC_STT_HOST", "127.0.0.1")
    print(f"[Whisper] Listening on http://{host}:8084")
    uvicorn.run(app, host=host, port=8084)
