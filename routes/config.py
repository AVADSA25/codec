"""CODEC config API — Settings UI backing.

F1 / SR-50: extracted from codec_dashboard.py. The 22-rule validation
matrix + sensitive-field masking helpers move with the GET/PUT endpoints
so the whole config-surface lives in one place.

  - GET /api/config  returns grouped sections with sensitive fields masked
  - PUT /api/config  validates per-rule + merges, skipping masked values
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from routes._shared import CONFIG_PATH

router = APIRouter()


def _mask_sensitive(value: str) -> str:
    """Mask sensitive field values, showing only last 4 characters."""
    if not value or not isinstance(value, str):
        return ""
    if len(value) <= 4:
        return "****"
    return "*" * (len(value) - 4) + value[-4:]


# Fields that contain secrets and must be masked in GET responses
_SENSITIVE_FIELDS = {"llm_api_key", "dashboard_token", "auth_pin_hash"}

# Validation rules: field -> (type, required, extra_checks)
# extra_checks is a callable returning (ok, error_msg)
_VALIDATION_RULES = {
    "agent_name":          (str,  True,  lambda v: (len(v.strip()) > 0, "agent_name cannot be empty")),
    "llm_provider":        (str,  True,  lambda v: (len(v.strip()) > 0, "llm_provider cannot be empty")),
    "llm_model":           (str,  False, None),
    "llm_base_url":        (str,  False, lambda v: (v == "" or v.startswith("http"), "llm_base_url must be a valid URL")),
    "llm_api_key":         (str,  False, None),
    "streaming":           (bool, False, None),
    "vision_base_url":     (str,  False, lambda v: (v == "" or v.startswith("http"), "vision_base_url must be a valid URL")),
    "vision_model":        (str,  False, None),
    "tts_engine":          (str,  False, None),
    "tts_url":             (str,  False, lambda v: (v == "" or v.startswith("http"), "tts_url must be a valid URL")),
    "tts_model":           (str,  False, None),
    "tts_voice":           (str,  False, None),
    "stt_engine":          (str,  False, None),
    "stt_url":             (str,  False, lambda v: (v == "" or v.startswith("http"), "stt_url must be a valid URL")),
    "key_toggle":          (str,  True,  lambda v: (len(v.strip()) > 0, "key_toggle cannot be empty")),
    "key_voice":           (str,  True,  lambda v: (len(v.strip()) > 0, "key_voice cannot be empty")),
    "key_text":            (str,  True,  lambda v: (len(v.strip()) > 0, "key_text cannot be empty")),
    "wake_word_enabled":   (bool, False, None),
    "wake_phrases":        (list, False, None),
    "wake_energy":         ((int, float), False, lambda v: (v >= 0, "wake_energy cannot be negative")),
    "auth_enabled":        (bool, False, None),
    "auth_session_hours":  ((int, float), False, lambda v: (v > 0, "auth_session_hours must be positive")),
    "dashboard_token":     (str,  False, None),
}


def _validate_config_updates(flat: dict) -> list:
    """Validate flattened config values. Returns list of error strings."""
    errors = []
    for key, value in flat.items():
        rule = _VALIDATION_RULES.get(key)
        if not rule:
            continue  # allow unknown keys through (forward compat)
        expected_type, required, check_fn = rule
        # Skip masked sensitive values (client didn't change them)
        if key in _SENSITIVE_FIELDS and isinstance(value, str) and value.startswith("*"):
            continue
        if not isinstance(value, expected_type):
            errors.append(f"{key}: expected {expected_type.__name__ if isinstance(expected_type, type) else 'number'}, got {type(value).__name__}")
            continue
        if check_fn:
            ok, msg = check_fn(value)
            if not ok:
                errors.append(msg)
    return errors


@router.get("/api/config")
async def get_config():
    """Return full editable config for Settings UI (sensitive fields masked)."""
    config = {}
    try:
        with open(CONFIG_PATH) as f:
            config = json.load(f)
    except Exception:
        pass
    # Group into sections for the UI
    result = {
        "llm": {
            "llm_provider": config.get("llm_provider", "mlx"),
            "llm_model": config.get("llm_model", ""),
            "llm_base_url": config.get("llm_base_url", "http://localhost:8083/v1"),
            "llm_api_key": config.get("llm_api_key", ""),
            "streaming": config.get("streaming", True),
        },
        "vision": {
            "vision_base_url": config.get("vision_base_url", "http://localhost:8083/v1"),
            "vision_model": config.get("vision_model", ""),
        },
        "tts": {
            "tts_engine": config.get("tts_engine", "kokoro"),
            "tts_url": config.get("tts_url", "http://localhost:8085/v1/audio/speech"),
            "tts_model": config.get("tts_model", ""),
            "tts_voice": config.get("tts_voice", "am_adam"),
        },
        "stt": {
            "stt_engine": config.get("stt_engine", "whisper_http"),
            "stt_url": config.get("stt_url", "http://localhost:8084/v1/audio/transcriptions"),
        },
        "keys": {
            "key_toggle": config.get("key_toggle", "f13"),
            "key_voice": config.get("key_voice", "f18"),
            "key_text": config.get("key_text", "f16"),
        },
        "wake": {
            "wake_word_enabled": config.get("wake_word_enabled", True),
            "wake_phrases": config.get("wake_phrases", []),
            "wake_energy": config.get("wake_energy", 200),
        },
        "auth": {
            "auth_enabled": config.get("auth_enabled", False),
            "auth_session_hours": config.get("auth_session_hours", 24),
            "dashboard_token": config.get("dashboard_token", ""),
        },
        "identity": {
            "agent_name": config.get("agent_name", "C"),
        },
    }
    # Mask sensitive fields before sending to the client
    for section in result.values():
        if isinstance(section, dict):
            for key in section:
                if key in _SENSITIVE_FIELDS:
                    section[key] = _mask_sensitive(section[key])
    return result


@router.put("/api/config")
async def update_config(request: Request):
    """Update config.json from Settings UI with input validation."""
    try:
        updates = await request.json()
        config = {}
        try:
            with open(CONFIG_PATH) as f:
                config = json.load(f)
        except Exception:
            pass

        # Flatten sections for validation and merge
        flat = {}
        for section_vals in updates.values():
            if isinstance(section_vals, dict):
                for k, v in section_vals.items():
                    flat[k] = v

        # Validate all incoming values
        errors = _validate_config_updates(flat)
        if errors:
            return JSONResponse({"error": "Validation failed", "details": errors}, status_code=422)

        # Merge validated values, skipping masked sensitive fields
        changed_keys = []
        for k, v in flat.items():
            # If a sensitive field is still masked, the user did not change it — skip
            if k in _SENSITIVE_FIELDS and isinstance(v, str) and v.startswith("*"):
                continue
            config[k] = v
            changed_keys.append(k)

        with open(CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=2)
        return {
            "saved": True,
            "message": f"Configuration saved successfully ({len(changed_keys)} field(s) updated).",
            "updated_fields": changed_keys,
        }
    except json.JSONDecodeError:
        return JSONResponse({"error": "Invalid JSON in request body"}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
