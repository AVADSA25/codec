"""Model picker endpoints.

Chat re-reads ~/.codec/config.json per request, so switching `llm_model` takes
effect on the next message — no restart. The heavy lifting (validation, probe,
auto-revert) lives in codec_models; these are thin wrappers.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse

import codec_models
from codec_cookbook import offer as model_offer

router = APIRouter()


@router.get("/api/models")
async def list_models():
    """Locally available chat models + which one is active."""
    return codec_models.list_models()


@router.post("/api/model")
async def set_model(body: dict):
    """Switch the active chat model.

    Synchronous on purpose: the server unloads the old model and loads the new
    one, and the caller needs to know whether that succeeded. On failure the
    previous model is restored and reloaded before this returns, so CODEC is
    never left without a brain.
    """
    model_id = (body or {}).get("model") or (body or {}).get("id") or ""
    if not model_id:
        return JSONResponse({"ok": False, "error": "missing 'model'"},
                            status_code=400)
    result = codec_models.set_active(str(model_id))
    return JSONResponse(result, status_code=200 if result.get("ok") else 409)


# ── Best model for this Mac (P-039, docs/MODEL-FIT-OFFER-DESIGN.md) ──────────
# Settings > Language model. The guards (fits this Mac, licence, image-job
# lock, disk space) live in codec_cookbook.offer; a switch is set_active.

def _model_id(body) -> str:
    return str((body or {}).get("id") or "") if isinstance(body, dict) else ""


@router.get("/api/models/fit")
async def model_fit():
    """The largest catalog model that fits this Mac, and the others that fit."""
    return await asyncio.to_thread(model_offer.build)


@router.post("/api/models/fit/download")
async def model_fit_download(body: dict):
    model_id = _model_id(body)
    if not model_id:
        return JSONResponse({"ok": False, "error": "missing 'id'"}, status_code=400)
    result = await asyncio.to_thread(model_offer.start_download, model_id)
    return JSONResponse(result, status_code=200 if result.get("ok") else 409)


@router.get("/api/models/fit/download")
async def model_fit_download_status(id: str = ""):
    result = model_offer.download_progress(id) if id else None
    if result is None:
        return JSONResponse({"ok": False, "error": "unknown model"}, status_code=404)
    return result


@router.post("/api/models/fit/use")
async def model_fit_use(body: dict):
    """Switch to an offered, downloaded model: set_active's switch, probe and revert."""
    model_id = _model_id(body)
    if not model_id:
        return JSONResponse({"ok": False, "error": "missing 'id'"}, status_code=400)
    result = await asyncio.to_thread(model_offer.use, model_id)
    return JSONResponse(result, status_code=200 if result.get("ok") else 409)
