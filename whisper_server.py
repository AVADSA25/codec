#!/usr/bin/env python3
"""CODEC Whisper STT Server — runs on port 8084
Usage: python3 whisper_server.py
"""
import mlx_whisper
from fastapi import FastAPI, UploadFile, File
import tempfile, os, uvicorn

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
    print(f"[Whisper] Listening on http://localhost:8084")
    uvicorn.run(app, host="0.0.0.0", port=8084)
