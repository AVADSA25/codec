"""Phase 2 Step 6 — PWA endpoints for the Trigger System.

Three endpoints, all auth-gated by codec_dashboard's existing /api/*
middleware:

  GET  /api/triggers
       List all registered triggers + their state. Returns metadata
       only — no skill source code, no live snapshot data.

  GET  /api/triggers/{trigger_key}
       Trigger detail (last_fired_at, cooldown_remaining, killed?).

  POST /api/triggers/{trigger_key}/kill
       Toggle the killed state. Body: {"killed": true|false}.
       Returns the new state.

The dashboard frontend renders a "Triggers" tab consuming these.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

log = logging.getLogger("routes.triggers")

router = APIRouter(prefix="/api/triggers", tags=["triggers"])


def _trigger_summary(trig) -> dict:
    """Render a Trigger to a JSON-safe dict for the API."""
    from codec_triggers import cooldown_remaining, is_killed
    return {
        "trigger_key": trig.key,
        "skill_name": trig.skill_name,
        "type": trig.type,
        "summary": trig.short_summary(),
        "cooldown_seconds": trig.cooldown_seconds,
        "cooldown_remaining": cooldown_remaining(trig.key, trig.cooldown_seconds),
        "require_confirmation": trig.require_confirmation,
        "destructive": trig.destructive,
        "killed": is_killed(trig.key),
    }


def _list_triggers() -> list:
    """Discover triggers via the dispatch registry. Returns list of
    summary dicts. Empty list if registry unavailable."""
    try:
        from codec_dispatch import registry
        from codec_triggers import discover_triggers
    except Exception as e:
        log.debug("trigger discovery skipped: %s", e)
        return []
    try:
        triggers = discover_triggers(registry)
    except Exception as e:
        log.debug("discover_triggers failed: %s", e)
        return []
    return [_trigger_summary(t) for t in triggers]


@router.get("")
async def list_triggers(request: Request):
    """Return all registered triggers + global enable state."""
    try:
        from codec_triggers import _enabled, _load_killed
    except Exception:
        return {"triggers": [], "global_enabled": False, "error": "module not loaded"}
    triggers = _list_triggers()
    return {
        "triggers": triggers,
        "global_enabled": _enabled(),
        "total": len(triggers),
        "killed_count": sum(1 for t in triggers if t["killed"]),
    }


@router.get("/{trigger_key}")
async def get_trigger(trigger_key: str, request: Request):
    """Detail for one trigger by key. 404 if not registered."""
    matches = [t for t in _list_triggers() if t["trigger_key"] == trigger_key]
    if not matches:
        raise HTTPException(status_code=404, detail=f"trigger {trigger_key} not registered")
    return matches[0]


@router.post("/{trigger_key}/kill")
async def toggle_kill(trigger_key: str, request: Request):
    """Body: {"killed": bool}. Toggles the killed state. Persists to
    ~/.codec/triggers_killed.json."""
    try:
        from codec_triggers import set_killed, is_killed
    except Exception:
        raise HTTPException(status_code=500, detail="codec_triggers unavailable")
    try:
        body = await request.json()
    except Exception:
        body = {}
    desired = bool(body.get("killed", not is_killed(trigger_key)))
    set_killed(trigger_key, desired)
    return {
        "trigger_key": trigger_key,
        "killed": is_killed(trigger_key),
    }
