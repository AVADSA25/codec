"""CODEC Heartbeat API routes.

C2 / SR-37: extracted from codec_dashboard.py. Heartbeat alert management
endpoints. All state is ~/.codec/config.json via the shared CONFIG_PATH; saves
go through codec_config.update_config (file lock, refuses an unreadable file:
P-054), so no module-level locks are needed. The heartbeat_enabled /
heartbeat_interval / heartbeat_tasks settings and their routes were removed
(P-035): nothing read them; the heartbeat runs inside codec-dashboard every 30
minutes.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from routes._shared import CONFIG_PATH

router = APIRouter()


def _save(change):
    """Apply `change(config)` under the config lock; None when saved, else the
    409 answer for a config.json that cannot be read (nothing written)."""
    import codec_config
    try:
        codec_config.update_config(change, path=CONFIG_PATH)
    except codec_config.ConfigUnreadable as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    return None


@router.get("/api/heartbeat/alerts")
async def get_heartbeat_alerts():
    """Get heartbeat alerts configuration."""
    config = {}
    try:
        with open(CONFIG_PATH) as f:
            config = json.load(f)
    except (OSError, ValueError):
        pass
    return {"alerts": config.get("heartbeat_alerts", [])}


@router.put("/api/heartbeat/alerts")
async def update_heartbeat_alerts(request: Request):
    """Update heartbeat alerts configuration."""
    body = await request.json()
    alerts = body.get("alerts", [])
    if not isinstance(alerts, list):
        return JSONResponse({"error": "alerts must be a list"}, status_code=422)

    def _apply(config):
        config["heartbeat_alerts"] = alerts

    refused = _save(_apply)
    if refused is not None:
        return refused
    return {"saved": True, "message": f"{len(alerts)} alert(s) saved."}
