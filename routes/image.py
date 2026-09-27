"""Create image endpoints for the chat's Image mode (docs/CHAT-CREATE-IMAGE-DESIGN.md).

Thin wrappers over codec_image, behind the normal dashboard auth. Handlers are
plain `def`, so FastAPI runs them in its threadpool and decoding references
never blocks the event loop.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

import codec_image

router = APIRouter()


@router.get("/api/image/status")
def image_status():
    """Whether this Mac can create images, plus the queue and the options."""
    return codec_image.status()


@router.post("/api/image/jobs")
def create_image_job(body: dict):
    """Queue one job: {prompt, refs: [base64 or data URL], size, count, seed, transparent}."""
    body = body or {}
    try:
        job = codec_image.submit(prompt=body.get("prompt", ""), refs=body.get("refs") or [],
                                 size=body.get("size"), count=body.get("count", 1),
                                 seed=body.get("seed"), rgba=bool(body.get("transparent")))
    except codec_image.ImageError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    return {"ok": True, "job": job}


@router.get("/api/image/jobs/{job_id}")
def get_image_job(job_id: str):
    job = codec_image.get(job_id)
    if job is None:
        return JSONResponse({"ok": False, "error": "unknown job"}, status_code=404)
    return {"ok": True, "job": job}


@router.post("/api/image/jobs/{job_id}/cancel")
def cancel_image_job(job_id: str):
    job = codec_image.cancel(job_id)
    if job is None:
        return JSONResponse({"ok": False, "error": "unknown job"}, status_code=404)
    return {"ok": True, "job": job}


@router.get("/api/image/file/{job_id}/{name}")
def get_image_file(job_id: str, name: str):
    path = codec_image.file_path(job_id, name)
    if path is None:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    return FileResponse(path, media_type="image/png",
                        headers={"Cache-Control": "private, max-age=86400"})
