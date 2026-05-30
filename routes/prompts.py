"""CODEC Prompt Overrides API routes — view + edit all system prompts.

D4 / SR-45: extracted from codec_dashboard.py. The 3 prompt endpoints
plus the 3 helper functions that back them. Overrides persist to
~/.codec/prompt_overrides.json.

`_get_all_prompts` collects defaults from the source files (identity,
voice, chat, vibe, textassist) and overlays the operator's overrides.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from routes._shared import DASHBOARD_DIR

router = APIRouter()


# ── System Prompts API — view and edit all CODEC personality prompts ─────
PROMPTS_FILE = os.path.join(str(Path.home()), ".codec", "prompt_overrides.json")


def _load_prompt_overrides():
    try:
        with open(PROMPTS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_prompt_overrides(data):
    os.makedirs(os.path.dirname(PROMPTS_FILE), exist_ok=True)
    with open(PROMPTS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _get_all_prompts():
    """Collect all system prompts from source files + any user overrides."""
    overrides = _load_prompt_overrides()
    prompts = {}

    # 1. CODEC Identity (base)
    try:
        from codec_identity import CODEC_IDENTITY
        prompts["identity_base"] = {
            "label": "CODEC Identity (Base)",
            "description": "Core identity shared by all interfaces — who CODEC is, personality, memory rules",
            "file": "codec_identity.py",
            "default": CODEC_IDENTITY.strip(),
        }
    except Exception:
        pass

    # 2. Voice prompt
    try:
        from codec_identity import CODEC_VOICE_PROMPT
        prompts["voice"] = {
            "label": "Voice Mode",
            "description": "Real-time voice calls — spoken output rules, concise answers, TTS formatting",
            "file": "codec_voice.py",
            "default": CODEC_VOICE_PROMPT.strip(),
        }
    except Exception:
        pass

    # 3. Chat prompt — lazy import from codec_dashboard to avoid a
    #    module-load-time cycle (codec_dashboard imports this router).
    try:
        from codec_dashboard import CHAT_SYSTEM_PROMPT
        prompts["chat"] = {
            "label": "Chat Mode",
            "description": "Web chat interface — skill awareness, tool calling, personality",
            "file": "codec_dashboard.py",
            "default": CHAT_SYSTEM_PROMPT.strip(),
        }
    except Exception:
        pass

    # 4. Vibe IDE prompt (multi-line JS string concatenation)
    try:
        vibe_path = os.path.join(DASHBOARD_DIR, "codec_vibe.html")
        import re as _re
        with open(vibe_path, "r") as f:
            content = f.read()
        # Match: var SYSP = "..." + \n"..." + ... "...";
        m = _re.search(r'var SYSP\s*=\s*((?:"[^"]*"\s*\+?\s*\n?\s*)+);', content)
        if m:
            raw_block = m.group(1)
            # Extract all quoted strings and join them
            parts = _re.findall(r'"([^"]*)"', raw_block)
            joined = "".join(parts)
            # Unescape \n
            joined = joined.replace('\\n', '\n')
            prompts["vibe"] = {
                "label": "Vibe IDE",
                "description": "AI coding assistant — code output rules, operational modes, Canvas requirements",
                "file": "codec_vibe.html",
                "default": joined.strip(),
            }
    except Exception:
        pass

    # 5. Text Assist modes
    ta_prompts = {
        "textassist_proofread": ("Proofread", "Fix spelling, grammar, punctuation — keep same tone"),
        "textassist_elevate": ("Elevate", "Polish text to professional quality"),
        "textassist_explain": ("Explain", "Simplify and summarize text"),
        "textassist_reply": ("Reply", "Craft a natural reply matching tone"),
        "textassist_translate": ("Translate", "Translate any language to English"),
        "textassist_prompt": ("Prompt Engineer", "Optimize text as an AI prompt"),
    }
    try:
        # Read the prompts dict from the file directly
        ta_path = os.path.join(DASHBOARD_DIR, "codec_textassist.py")
        with open(ta_path, "r") as f:
            ta_content = f.read()
        import ast
        tree = ast.parse(ta_content)
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
                if "proofread" in keys and "elevate" in keys:
                    for k, v in zip(node.keys, node.values):
                        if isinstance(k, ast.Constant) and isinstance(v, ast.Constant):
                            key = f"textassist_{k.value}"
                            if key in ta_prompts:
                                label, desc = ta_prompts[key]
                                prompts[key] = {
                                    "label": f"Text Assist: {label}",
                                    "description": desc,
                                    "file": "codec_textassist.py",
                                    "default": v.value.strip(),
                                }
                    break
    except Exception:
        pass

    # Apply overrides
    for key, prompt_data in prompts.items():
        prompt_data["value"] = overrides.get(key, prompt_data["default"])
        prompt_data["modified"] = key in overrides

    return prompts


@router.get("/api/prompts")
async def get_prompts():
    """Return all system prompts with defaults and any user overrides."""
    try:
        return _get_all_prompts()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.put("/api/prompts")
async def update_prompts(request: Request):
    """Save user prompt overrides. Send {key: new_value} pairs."""
    try:
        updates = await request.json()
        overrides = _load_prompt_overrides()
        all_prompts = _get_all_prompts()
        for key, value in updates.items():
            if key not in all_prompts:
                continue
            # If value matches default, remove override
            if value.strip() == all_prompts[key]["default"]:
                overrides.pop(key, None)
            else:
                overrides[key] = value.strip()
        _save_prompt_overrides(overrides)
        return {"ok": True, "overrides_count": len(overrides)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/prompts/reset")
async def reset_prompt(request: Request):
    """Reset a prompt to its default. Send {key: "prompt_key"}."""
    try:
        body = await request.json()
        key = body.get("key")
        overrides = _load_prompt_overrides()
        overrides.pop(key, None)
        _save_prompt_overrides(overrides)
        return {"ok": True, "reset": key}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
