"""CODEC config API — Settings UI backing.

F1 / SR-50: extracted from codec_dashboard.py. The 22-rule validation
matrix + sensitive-field masking helpers move with the GET/PUT endpoints
so the whole config-surface lives in one place.

  - GET /api/config  returns grouped sections with sensitive fields masked
  - PUT /api/config  validates per-rule + merges, skipping masked values

UI phase 2 P2.10 (docs/P2.10-DESIGN.md): the Settings page also edits the
nested blocks (shift_report, observer, daybreak, ask_user, step_budget, image,
ui_prefs). PUT merges those into their block instead of flattening them, with
their own rules. /api/config/raw is the Advanced editor: the whole file,
secrets masked; a save merges and never deletes a key.
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
    "tts_speed":           ((int, float), False, lambda v: (0.5 <= v <= 2.0, "tts_speed must be between 0.5 and 2.0")),
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


def _in(*allowed):
    return lambda v: (v in allowed, "must be one of " + ", ".join(str(a) for a in allowed))


def _between(lo, hi):
    return lambda v: (lo <= v <= hi, f"must be between {lo} and {hi}")


_NUM = (int, float)
_OPT_STR = (str, type(None))

# P2.10: nested blocks the Settings page edits. block -> {field: (type, check, default)}.
# The defaults mirror the modules that read them (skills/shift_report.py,
# codec_observer.py, codec_daybreak.py, codec_ask_user.py, codec_chat_pipeline.py,
# codec_image.py); GET fills them in for fields the file does not have yet.
NESTED_BLOCKS = {
    "shift_report": {
        "enabled": (bool, None, True),
        "daily_at_hour": (int, _between(0, 23), 18),
        "daily_at_minute": (int, _between(0, 59), 0),
        "idle_minutes": (int, _between(5, 240), 30),
        "lookback_hours": (int, _between(1, 72), 24),
        "auto_save_path": (_OPT_STR, None, None),
    },
    "observer": {
        "enabled": (bool, None, True),
        "ocr_enabled": (bool, None, True),
        # AGENTS.md §10: never below 30 s (OCR cost).
        "cadence_active_s": (int, _between(30, 600), 60),
        "cadence_idle_s": (int, _between(60, 1800), 300),
    },
    "daybreak": {
        "include_calendar": (bool, None, True),
        "include_email": (bool, None, True),
        "include_weather": (bool, None, True),
        "include_reminders": (bool, None, True),
        "time_budget_seconds": (_NUM, _between(2, 30), 8),
    },
    "ask_user": {
        "timeout_seconds": (int, _between(30, 3600), 600),
    },
    "step_budget": {
        # AGENTS.md §10: "tune up before tuning out" — 5 (default), 8 or 10.
        "chat": (int, _in(5, 8, 10), 5),
        "voice": (int, _in(5, 8, 10), 5),
    },
    "image": {
        "steps": (int, _between(4, 50), 20),
        "max_count": (int, _between(1, 4), 4),
        "min_free_gb": (_NUM, _between(8, 128), 36),
    },
    "ui_prefs": {},  # the Page Customization switches: any id -> bool
}

# Keys the Advanced editor never changes: masked secrets, and the model switch's
# own record of the local model to go back to (AGENTS.md §10).
_RAW_READ_ONLY = _SENSITIVE_FIELDS | {"llm_local_restore"}


def _validate_block(block: str, values) -> list:
    if not isinstance(values, dict):
        return [f"{block}: expected an object"]
    rules = NESTED_BLOCKS[block]
    errors = []
    for key, value in values.items():
        if block == "ui_prefs":
            if not isinstance(value, bool):
                errors.append(f"ui_prefs.{key}: expected true or false")
            continue
        rule = rules.get(key)
        if not rule:
            continue  # forward compat, like the flat rules
        expected, check, _default = rule
        if isinstance(value, bool) and expected is not bool:  # True is an int to Python
            errors.append(f"{block}.{key}: expected a number, got true/false")
            continue
        if not isinstance(value, expected):
            errors.append(f"{block}.{key}: wrong type ({type(value).__name__})")
            continue
        if check:
            ok, msg = check(value)
            if not ok:
                errors.append(f"{block}.{key} {msg}")
    return errors


def _read_config() -> dict:
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def _write_config(config: dict) -> None:
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


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
            "tts_speed": config.get("tts_speed", 1.1),
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
    # P2.10: the nested blocks, with defaults for fields the file does not have.
    for block, rules in NESTED_BLOCKS.items():
        saved = config.get(block) if isinstance(config.get(block), dict) else {}
        vals = {k: rule[2] for k, rule in rules.items()}
        vals.update({k: v for k, v in saved.items() if block == "ui_prefs" or k in rules})
        result[block] = vals
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

        if not isinstance(updates, dict):
            return JSONResponse({"error": "JSON object expected"}, status_code=400)
        # Flatten the old grouped sections for validation and merge; P2.10's
        # nested blocks are merged into their own block instead.
        flat, nested = {}, {}
        for section, section_vals in updates.items():
            if section in NESTED_BLOCKS:
                nested[section] = section_vals
            elif isinstance(section_vals, dict):
                for k, v in section_vals.items():
                    flat[k] = v

        # Validate all incoming values
        errors = _validate_config_updates(flat)
        for block, vals in nested.items():
            errors += _validate_block(block, vals)
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
        for block, vals in nested.items():
            current = config.get(block) if isinstance(config.get(block), dict) else {}
            config[block] = {**current, **vals}
            changed_keys += [f"{block}.{k}" for k in vals]

        _write_config(config)
        return {
            "saved": True,
            "message": f"Configuration saved successfully ({len(changed_keys)} field(s) updated).",
            "updated_fields": changed_keys,
        }
    except json.JSONDecodeError:
        return JSONResponse({"error": "Invalid JSON in request body"}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


def _masked_config(config: dict) -> dict:
    out = dict(config)
    for key in _SENSITIVE_FIELDS:
        if isinstance(out.get(key), str) and out[key]:
            out[key] = _mask_sensitive(out[key])
    return out


@router.get("/api/config/raw")
async def get_config_raw():
    """P2.10 Advanced editor: the whole config.json, secrets masked."""
    return {"config": _masked_config(_read_config()), "read_only": sorted(_RAW_READ_ONLY)}


@router.put("/api/config/raw")
async def put_config_raw(request: Request):
    """Merge an edited config.json. Keys in the body replace the saved ones; keys
    left out are kept (this never deletes a key); read-only keys stay as saved."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON in request body"}, status_code=400)
    edited = body.get("config") if isinstance(body, dict) else None
    if not isinstance(edited, dict):
        return JSONResponse({"error": "config must be a JSON object"}, status_code=400)
    config = _read_config()
    changes = {k: v for k, v in edited.items() if k not in _RAW_READ_ONLY and config.get(k) != v}
    errors = _validate_config_updates({k: v for k, v in changes.items() if k not in NESTED_BLOCKS})
    for block in NESTED_BLOCKS:
        if block in changes:
            errors += _validate_block(block, changes[block])
    if errors:
        return JSONResponse({"error": "Validation failed", "details": errors}, status_code=422)
    config.update(changes)
    _write_config(config)
    return {"saved": True, "updated_fields": sorted(changes),
            "message": f"config.json saved ({len(changes)} key(s) changed)."}
