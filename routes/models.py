"""Model picker endpoints.

Chat re-reads ~/.codec/config.json per request, so switching `llm_model` takes
effect on the next message — no restart. The heavy lifting (validation, probe,
auto-revert) lives in codec_models; these are thin wrappers.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

import codec_models

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
