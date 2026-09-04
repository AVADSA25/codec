"""First-run connection endpoints — see codec_setup for the why.

Thin wrappers on purpose: validation, provider choice and the verification call
all live in codec_setup so the installer, the app and the tests exercise one
implementation rather than three.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

import codec_setup

router = APIRouter()


@router.get("/api/setup/status")
async def setup_status():
    """Is a brain connected? Drives the first-run screen."""
    return codec_setup.status()


@router.post("/api/setup/provider")
async def set_provider(body: dict):
    """Record a provider choice. Deliberately does NOT mark it connected —
    only /api/setup/verify can, after a real request succeeds."""
    b = body or {}
    mode = str(b.get("provider") or b.get("mode") or "")
    result = codec_setup.set_provider(
        mode,
        model=str(b.get("model") or ""),
        base_url=str(b.get("base_url") or ""),
        api_key=str(b.get("api_key") or ""),
    )
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@router.post("/api/setup/verify")
async def verify():
    """Ask the configured endpoint a real question and report what happened."""
    result = codec_setup.verify()
    return JSONResponse(result, status_code=200 if result.get("ok") else 409)


@router.get("/api/setup/local_models")
async def local_models():
    return {"models": codec_setup.discover_local_models(),
            "bundled": codec_setup.BUNDLED_LOCAL_MODEL}
