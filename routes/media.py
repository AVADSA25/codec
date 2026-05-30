"""CODEC Media API routes — webcam, screenshot, clipboard.

D5 / SR-46: extracted from codec_dashboard.py. These are simple shell-
adjacent media endpoints (capture / read clipboard / take screenshot).

NOT included here: /api/upload and /api/upload_image. Those involve the
B1 / SR-15 size-cap defense + the B1 / SR-16 fence-marker helper +
permitted-path checks — kept in codec_dashboard.py to keep the security-
hardening review surface tight. Future PR may extract them as a single
upload module.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from routes._shared import CONFIG_PATH, _audit_write

router = APIRouter()


@router.post("/api/webcam")
async def webcam_capture(request: Request):
    """Save webcam photo and optionally analyze with vision model."""
    body = await request.json()
    image_b64 = body.get("image", "")
    analyze = body.get("analyze", False)
    prompt = body.get("prompt", "Describe what you see in this webcam photo.")
    if not image_b64:
        return JSONResponse({"error": "No image data"}, status_code=400)
    try:
        # Save photo
        photo_dir = os.path.expanduser("~/.codec/photos")
        os.makedirs(photo_dir, exist_ok=True)
        filename = f"webcam_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        filepath = os.path.join(photo_dir, filename)
        with open(filepath, "wb") as f:
            f.write(base64.b64decode(image_b64))
        result = {"saved": filepath, "filename": filename}
        # Optional vision analysis
        if analyze:
            try:
                import requests as rq
                config = {}
                try:
                    with open(CONFIG_PATH) as f:
                        config = json.load(f)
                except Exception:
                    pass
                vision_url = config.get("vision_base_url", "http://localhost:8083/v1")
                vision_model = config.get("vision_model", "mlx-community/Qwen2.5-VL-7B-Instruct-4bit")
                payload = {
                    "model": vision_model,
                    "messages": [{"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                        {"type": "text", "text": prompt}
                    ]}],
                    "max_tokens": 4000, "temperature": 0.7
                }
                r = rq.post(f"{vision_url}/chat/completions", json=payload,
                            headers={"Content-Type": "application/json"}, timeout=120)
                result["analysis"] = r.json()["choices"][0]["message"]["content"].strip()
                result["model"] = vision_model
            except Exception as e:
                result["analysis_error"] = str(e)
        _audit_write(f"[{datetime.now().isoformat()}] WEBCAM: {filename} analyze={analyze}\n")
        return result
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/webcam/stream")
async def webcam_stream():
    """MJPEG stream from the Mac's webcam — for remote viewing from phone/tablet."""
    import cv2
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        return JSONResponse({"error": "Cannot open webcam"}, status_code=500)
    _executor = ThreadPoolExecutor(max_workers=1)

    def _read_frame():
        ret, frame = cap.read()
        if not ret:
            return None
        _, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        return jpeg.tobytes()

    async def generate():
        loop = asyncio.get_event_loop()
        try:
            while True:
                data = await loop.run_in_executor(_executor, _read_frame)
                if data is None:
                    break
                yield (b"--frame\r\n"
                       b"Content-Type: image/jpeg\r\n\r\n" + data + b"\r\n")
                await asyncio.sleep(0.066)  # ~15 fps
        finally:
            cap.release()
            _executor.shutdown(wait=False)

    return StreamingResponse(generate(), media_type="multipart/x-mixed-replace; boundary=frame")


@router.get("/api/webcam/snapshot")
async def webcam_snapshot():
    """Capture a single frame from the Mac's webcam and return as JPEG."""
    import cv2

    def _capture():
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            return None, None
        ret, frame = cap.read()
        cap.release()
        if not ret:
            return None, None
        _, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return jpeg.tobytes(), True

    loop = asyncio.get_event_loop()
    data, ok = await loop.run_in_executor(ThreadPoolExecutor(1), _capture)
    if not ok:
        return JSONResponse({"error": "Cannot capture from webcam"}, status_code=500)
    b64 = base64.b64encode(data).decode()
    # Save
    photo_dir = os.path.expanduser("~/.codec/photos")
    os.makedirs(photo_dir, exist_ok=True)
    filename = f"webcam_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    filepath = os.path.join(photo_dir, filename)
    with open(filepath, "wb") as f:
        f.write(data)
    return {"image": b64, "saved": filepath, "filename": filename}


@router.get("/api/screenshot")
async def screenshot():
    """Take screenshot of Mac Studio and return image."""
    try:
        path = os.path.expanduser("~/.codec/pwa_screenshot.png")
        subprocess.run(["screencapture", "-x", path], timeout=5)
        if os.path.exists(path):
            return FileResponse(path, media_type="image/png")
        return JSONResponse({"error": "Screenshot failed"}, status_code=500)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/clipboard")
async def get_clipboard():
    """Get Mac Studio clipboard content."""
    try:
        r = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=3)
        return {"content": r.stdout[:2000]}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/clipboard")
async def set_clipboard(request: Request):
    """Set Mac Studio clipboard content."""
    body = await request.json()
    text = body.get("text", "")
    try:
        p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
        p.communicate(text.encode())
        return {"status": "copied"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
