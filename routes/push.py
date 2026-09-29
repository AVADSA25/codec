"""CODEC Web Push routes (UI phase 3, P3.13; docs/P3.13-DESIGN.md).

Behind the dashboard login like every /api route; the POSTs are CSRF-checked
by AuthMiddleware. The replies never contain a device's endpoint or keys.

  - GET  /api/push              public key, switches, device list
  - POST /api/push/subscribe    {"subscription": PushSubscription.toJSON()}
  - POST /api/push/unsubscribe  {"endpoint": ...} or {"id": ...}
  - POST /api/push/types        {"approvals": bool, ...}
  - POST /api/push/test         send the test notification now

The handlers are plain `def`: FastAPI runs them in its thread pool, so the
Keychain read and the posts to the push services never block the event loop.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import codec_push

router = APIRouter()


def _body(payload) -> dict:
    return payload if isinstance(payload, dict) else {}


@router.get("/api/push")
def push_config():
    state = codec_push.read_state()
    return {
        "enabled": codec_push.enabled(),
        "public_key": codec_push.ensure_key(),
        "types": state["types"],
        "switches": list(codec_push.SWITCHES),
        "devices": codec_push.public_devices(state),
    }


@router.post("/api/push/subscribe")
def push_subscribe(request: Request, payload: dict | None = None):
    try:
        dev_id = codec_push.add_device(_body(payload).get("subscription"),
                                       request.headers.get("user-agent", ""))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"ok": True, "id": dev_id, "devices": codec_push.public_devices()}


@router.post("/api/push/unsubscribe")
def push_unsubscribe(payload: dict | None = None):
    body = _body(payload)
    endpoint, dev_id = body.get("endpoint"), body.get("id")
    if not isinstance(endpoint, str) and not isinstance(dev_id, str):
        return JSONResponse({"error": "endpoint or id required"}, status_code=400)
    removed = codec_push.remove_device(endpoint=endpoint if isinstance(endpoint, str) else None,
                                       dev_id=dev_id if isinstance(dev_id, str) else None)
    return {"ok": True, "removed": removed, "devices": codec_push.public_devices()}


@router.post("/api/push/types")
def push_types(payload: dict | None = None):
    return {"ok": True, "types": codec_push.set_types(_body(payload))}


@router.post("/api/push/test")
def push_test():
    if not codec_push.enabled():
        return JSONResponse({"error": "Push is switched off on this Mac (PUSH_ENABLED=false)."}, status_code=409)
    result = codec_push.send("test")
    return {"ok": True, **result, "devices_list": codec_push.public_devices()}
