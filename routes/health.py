"""CODEC public health + manifest + metrics + status routes.

E2 / SR-48: extracted from codec_dashboard.py. Five low-level endpoints
that don't touch business logic — they describe the running dashboard:

  - /api/health + /health   public health check (no auth)
  - /manifest.json          PWA install manifest
  - /metrics                Prometheus metrics scrape
  - /api/status             alive check + config snapshot

/api/services/status stays in codec_dashboard.py because it reads the
_bg_status / _bg_tasks globals owned by the background-runner startup
hooks.
"""
from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from routes._shared import CONFIG_PATH

router = APIRouter()
log = logging.getLogger("codec_dashboard")


class HealthResponse(BaseModel):
    status: str = Field(description="Service status", json_schema_extra={"example": "ok"})
    service: str = Field(description="Service name", json_schema_extra={"example": "CODEC Dashboard"})
    timestamp: str = Field(description="ISO timestamp", json_schema_extra={"example": "2026-05-30T12:00:00"})


@router.get("/manifest.json")
async def manifest():
    return JSONResponse({
        "name": "CODEC",
        "short_name": "CODEC",
        "description": "CODEC — Your Open-Source Intelligent Command Layer",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0a0a0a",
        "theme_color": "#E8711A",
        # B5 / SR-28: 192/512 icon entries declared so Android Add-to-Home-
        # Screen installers don't warn about missing standard sizes.
        "icons": [
            {"src": "/favicon.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/favicon.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
            {"src": "/favicon.png", "sizes": "2048x2048", "type": "image/png"},
        ]
    })


@router.get("/metrics")
async def prometheus_metrics():
    from codec_metrics import metrics
    return PlainTextResponse(metrics.render(), media_type="text/plain; version=0.0.4")


@router.get("/api/health", response_model=HealthResponse, tags=["Health"])
@router.get("/health", response_model=HealthResponse, include_in_schema=False)
async def health_check():
    """Public health check — returns service status. No authentication required."""
    return {"status": "ok", "service": "CODEC Dashboard", "timestamp": datetime.now().isoformat()}


@router.get("/api/status")
async def status():
    """Check if CODEC is running and return config."""
    config = {}
    try:
        with open(CONFIG_PATH) as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.warning(f"Config read failed; returning partial status: {e}")

    # Check if CODEC process is alive
    try:
        r = subprocess.run(["pgrep", "-f", "codec.py"], capture_output=True, text=True, timeout=3)
        alive = bool(r.stdout.strip())
    except (OSError, subprocess.SubprocessError) as e:
        log.warning(f"pgrep failed; assuming process not alive: {e}")
        alive = False

    return {
        "alive": alive,
        "config": {
            "llm_provider": config.get("llm_provider", "unknown"),
            "llm_model": config.get("llm_model", "unknown"),
            "tts_engine": config.get("tts_engine", "unknown"),
            "tts_voice": config.get("tts_voice", "unknown"),
            "key_toggle": config.get("key_toggle", "f13"),
            "key_voice": config.get("key_voice", "f18"),
            "key_text": config.get("key_text", "f16"),
            "wake_word_enabled": config.get("wake_word_enabled", False),
            "streaming": config.get("streaming", True),
        }
    }
