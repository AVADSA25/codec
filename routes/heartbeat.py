"""CODEC Heartbeat API routes.

C2 / SR-37: extracted from codec_dashboard.py. Heartbeat config + alert
management endpoints. All state is ~/.codec/config.json via the shared
CONFIG_PATH; saves go through codec_config.update_config (file lock, refuses
an unreadable file: P-054), so no module-level locks are needed.
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


@router.get("/api/heartbeat/config")
async def get_heartbeat_config():
    """Get heartbeat configuration."""
    config = {}
    try:
        with open(CONFIG_PATH) as f:
            config = json.load(f)
    except (OSError, ValueError):
        pass
    return {
        "enabled": config.get("heartbeat_enabled", True),
        "interval_minutes": config.get("heartbeat_interval", 5),
        "tasks": config.get("heartbeat_tasks", ["status_check"])
    }


@router.put("/api/heartbeat/config")
async def update_heartbeat_config(request: Request):
    """Update heartbeat configuration with validation."""
    body = await request.json()
    # Validate
    errors = []
    if "enabled" in body and not isinstance(body["enabled"], bool):
        errors.append("enabled must be a boolean")
    if "interval_minutes" in body:
        iv = body["interval_minutes"]
        if not isinstance(iv, (int, float)):
            errors.append("interval_minutes must be a number")
        elif iv <= 0:
            errors.append("interval_minutes must be positive")
    if "tasks" in body and not isinstance(body["tasks"], list):
        errors.append("tasks must be a list")
    if errors:
        return JSONResponse({"error": "Validation failed", "details": errors}, status_code=422)

    changed = []

    def _apply(config):
        if "enabled" in body:
            config["heartbeat_enabled"] = body["enabled"]
            changed.append("heartbeat_enabled")
        if "interval_minutes" in body:
            config["heartbeat_interval"] = body["interval_minutes"]
            changed.append("heartbeat_interval")
        if "tasks" in body:
            config["heartbeat_tasks"] = body["tasks"]
            changed.append("heartbeat_tasks")

    refused = _save(_apply)
    if refused is not None:
        return refused
    return {
        "saved": True,
        "message": f"Heartbeat config saved ({len(changed)} field(s) updated).",
        "updated_fields": changed,
    }


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
