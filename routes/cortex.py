"""CODEC Cortex API routes.

C3 / SR-38: extracted from codec_dashboard.py. Cortex is the live
neural architecture map UI; these endpoints feed it with per-service
health, skill list, PM2 logs, and restart actions.
"""
from __future__ import annotations

import json
import subprocess

from fastapi import APIRouter

from codec_audit import log_event

router = APIRouter()


@router.get("/api/cortex/health")
async def cortex_health():
    """Proxy health checks for CORTEX visualization."""
    import httpx
    checks = [
        {"id": "qwen", "port": 8083, "path": "/v1/models"},
        {"id": "vision", "port": 8083, "path": "/v1/models"},
        {"id": "whisper", "port": 8084, "path": "/"},
        {"id": "kokoro", "port": 8085, "path": "/v1/models"},
    ]
    results = {}
    async with httpx.AsyncClient(timeout=3.0) as client:
        for c in checks:
            try:
                r = await client.get(f"http://localhost:{c['port']}{c['path']}")
                results[c["id"]] = "ok" if r.status_code in (200, 404) else "err"
            except Exception:
                results[c["id"]] = "err"
    # Also check PM2 processes
    try:
        out = subprocess.check_output(
            ["/opt/homebrew/bin/pm2", "jlist"], timeout=5, stderr=subprocess.DEVNULL
        )
        procs = json.loads(out)
        pm2_map = {p["name"]: p["pm2_env"]["status"] for p in procs}
        for name, status in pm2_map.items():
            if "codec" in name.lower() or name in ("qwen35b", "qwen-vision", "whisper-stt", "kokoro-82m"):
                nid = name.replace("codec-", "").replace("-", "_")
                if nid not in results:
                    results[nid] = "ok" if status == "online" else "err"
    except Exception:
        pass
    results["dashboard"] = "ok"
    return results


@router.get("/api/cortex/skills")
async def cortex_skills():
    """Return all loaded skills for CORTEX visualization.

    A-4: reads from the canonical codec_dispatch registry (lazy AST scan +
    custom_triggers overlay) instead of the legacy codec_core.loaded_skills."""
    from codec_dispatch import registry
    if not registry.names():
        registry.scan()
    result = [
        {"name": name, "triggers": registry.get_triggers(name)}
        for name in registry.names()
    ]
    result.sort(key=lambda x: x["name"])
    return {"skills": result, "count": len(result)}


@router.get("/api/cortex/logs/{service}")
async def cortex_logs(service: str):
    """Return last 30 lines of PM2 logs for a service."""
    # Map CORTEX node IDs to PM2 process names
    PM2_MAP = {
        "qwen": "qwen35b", "vision": "qwen-vision", "whisper": "whisper-stt",
        "kokoro": "kokoro-82m", "dashboard": "codec-dashboard", "dispatch": "open-codec",
        "heartbeat": "codec-heartbeat", "watcher": "codec-hotkey",
        "f18": "open-codec", "f16": "open-codec", "f13": "open-codec",
        "wake": "open-codec", "screenshot": "open-codec", "document": "open-codec",
    }
    pm2_name = PM2_MAP.get(service, f"codec-{service}")
    try:
        result = subprocess.check_output(
            ["/opt/homebrew/bin/pm2", "logs", pm2_name, "--lines", "30", "--nostream"],
            timeout=5, stderr=subprocess.STDOUT
        ).decode("utf-8", errors="replace")
        return {"service": service, "pm2_name": pm2_name, "logs": result}
    except Exception as e:
        return {"service": service, "pm2_name": pm2_name, "logs": f"Error: {e}"}


@router.post("/api/cortex/restart/{service}")
async def cortex_restart(service: str):
    """Restart a PM2 service from CORTEX."""
    PM2_MAP = {
        "qwen": "qwen35b", "vision": "qwen-vision", "whisper": "whisper-stt",
        "kokoro": "kokoro-82m", "dashboard": "codec-dashboard", "dispatch": "open-codec",
        "heartbeat": "codec-heartbeat", "watcher": "codec-hotkey",
    }
    pm2_name = PM2_MAP.get(service)
    if not pm2_name:
        return {"ok": False, "error": f"Unknown service: {service}"}
    try:
        subprocess.check_output(
            ["/opt/homebrew/bin/pm2", "restart", pm2_name],
            timeout=10, stderr=subprocess.STDOUT
        )
        log_event("service_restart", "codec-dashboard",
                  f"Service restart: {service}",
                  extra={"service": service})
        return {"ok": True, "service": service, "pm2_name": pm2_name, "action": "restarted"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
