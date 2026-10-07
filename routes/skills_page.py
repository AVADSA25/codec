"""Skills page (UI phase 3, P3.6; docs/P3.6-DESIGN.md).

Every registered skill with where it works (Chat, the voice call, Claude over
MCP), whether it is ready (keys, the Google sign-in, macOS permissions), its
trigger phrases and its on/off switch. Read-only except the switch, which writes
config.json:skills_off through codec_skill_switches (every path enforces it).
"""
from __future__ import annotations

import ctypes
import os
import sys

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import codec_skill_switches

router = APIRouter()

# The voice call's own skip list (codec_voice.VoicePipeline._VOICE_SKIP_SKILLS; a test keeps them equal).
VOICE_SKIP = {"calculator", "app_switch", "brightness", "clipboard"}
GOOGLE_TOKEN = os.path.expanduser("~/.codec/google_token.json")
SCREEN_RECORDING = {"screenshot_text"}
ACCESSIBILITY = {"ax_control", "mouse_control"}
PERMISSIONS_NOTE = ("Permissions are the dashboard's, which runs Chat's skills; the voice call and the wake word run "
                    "in CODEC's own process, which has its own.")


def _mac_check(framework: str, fn: str):
    """True or False from a macOS call that never prompts; None where it cannot be asked."""
    if sys.platform != "darwin":
        return None
    try:
        lib = ctypes.cdll.LoadLibrary(f"/System/Library/Frameworks/{framework}.framework/{framework}")
        f = getattr(lib, fn)
        f.restype = ctypes.c_bool
        return bool(f())
    except (OSError, AttributeError):
        return None


def screen_recording_ok():
    return _mac_check("CoreGraphics", "CGPreflightScreenCaptureAccess")


def accessibility_ok():
    return _mac_check("ApplicationServices", "AXIsProcessTrusted")


def _keys() -> dict:
    out = {}
    try:
        import codec_config
        out["serper"] = bool(codec_config.get_serper_api_key())
        out["pexels"] = bool(codec_config.get_pexels_api_key())
    except Exception:
        out = {"serper": False, "pexels": False}
    return out


def readiness(name: str, ctx: dict) -> tuple:
    """(ready | limited | setup, the reason in plain words)."""
    if name.startswith("google_") and not ctx.get("google"):
        return "setup", "Google is not connected. Connect it in Settings > Connectors."
    if name in SCREEN_RECORDING and ctx.get("screen") is False:
        return "setup", ("CODEC has no Screen Recording permission (System Settings > Privacy & Security > "
                         "Screen Recording).")
    if name in ACCESSIBILITY and ctx.get("accessibility") is False:
        return "setup", ("CODEC has no Accessibility permission (System Settings > Privacy & Security > "
                         "Accessibility).")
    if name == "web_search" and not ctx.get("serper"):
        return "limited", "Works with DuckDuckGo; a Serper key gives better results."
    if name == "google_docs" and not ctx.get("pexels"):
        return "limited", "Works; a Pexels key adds stock images to documents."
    return "ready", ""


def _label(name: str) -> str:
    s = name.replace("_", " ").strip()
    return s[:1].upper() + s[1:]


@router.get("/api/skills/catalog")
def catalog():
    import codec_config
    from codec_dispatch import registry
    from routes.chat import CHAT_SKILL_ALLOWLIST
    from routes.palette import SKILL_GROUPS, skill_group
    if not registry.names():  # metadata only (AST); no skill code runs
        registry.scan()
    try:
        http_blocked = set(codec_config._mcp_blocked_tools("http", codec_config.load_config()))
    except Exception:
        http_blocked = set(codec_config._HTTP_BLOCKED) | set(codec_config._HTTP_ONLY_BLOCKED)
    asks = set(codec_config._HTTP_CONSENT_REQUIRED)
    ctx = dict(_keys(), google=os.path.exists(GOOGLE_TOKEN), screen=screen_recording_ok(),
               accessibility=accessibility_ok())
    off = codec_skill_switches.off_set()
    # Read the trigger overrides fresh: the registry loads them once at scan, so right after
    # Save it still holds the old list and the page would show the edit as lost. The running
    # matchers take the new list at restart, as the Save message says.
    custom = registry._load_custom_triggers()
    order = [g for g, _ in SKILL_GROUPS] + ["Other"]
    skills = []
    for name in registry.names():
        meta = registry.get_meta(name) or {}
        default = list(meta.get("SKILL_TRIGGERS", []))
        triggers = custom.get(name, default)
        mcp = bool(registry.get_mcp_expose(name)) and name not in http_blocked
        state, reason = readiness(name, ctx)
        skills.append({
            "name": name, "label": _label(name), "description": str(registry.get_description(name) or "")[:300],
            "group": skill_group(name), "triggers": [str(t) for t in triggers][:40],
            "customized": list(triggers) != default,
            "paths": {"chat": name in CHAT_SKILL_ALLOWLIST, "voice": name not in VOICE_SKIP,
                      "mcp": mcp, "mcp_asks": mcp and name in asks},
            "ready": state, "reason": reason, "on": name not in off,
        })
    skills.sort(key=lambda s: (order.index(s["group"]), s["label"].lower()))
    return {"skills": skills, "groups": order, "permissions_note": PERMISSIONS_NOTE}


@router.put("/api/skills/switch")
async def switch(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    name, on = (body or {}).get("name"), (body or {}).get("on")
    from codec_dispatch import registry
    if not registry.names():
        registry.scan()
    if not isinstance(name, str) or name not in registry.names() or not isinstance(on, bool):
        return JSONResponse({"error": "Give a known skill name and on: true or false."}, status_code=400)
    codec_skill_switches.set_on(name, on)
    try:
        from codec_audit import log_event
        log_event("skill_switched", "codec-dashboard", f"Skill {'on' if on else 'off'}", tool=name, extra={"on": on})
    except Exception:
        pass
    return {"name": name, "on": on}
