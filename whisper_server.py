#!/usr/bin/env python3
"""CODEC Whisper STT Server — runs on port 8084
Usage: python3 whisper_server.py
Binds 127.0.0.1 by default (no auth on this endpoint). Set CODEC_STT_HOST
to another address only if a trusted LAN client must reach it.
"""
import mlx_whisper
from fastapi import FastAPI, UploadFile, File
import tempfile
import os
import uvicorn

app = FastAPI()
MODEL = os.environ.get("WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo")

@app.post("/v1/audio/transcriptions")
async def transcribe(file: UploadFile = File(...)):
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.write(await file.read())
    tmp.close()
    try:
        result = mlx_whisper.transcribe(tmp.name, path_or_hf_repo=MODEL)
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
