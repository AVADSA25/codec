"""CODEC's three hotkeys: field names, defaults, keyboard presets (P-029).

codec.py listens for key_toggle (start / stop), key_voice (hold to talk) and
key_text (type a command); codec_config.resolve_hotkeys turns the names into
pynput keys. Settings, the first-run screen and setup_codec.py change them.
Stdlib only, so the dashboard, the installer and codec_config can import it.
Design: docs/HOTKEYS-QUICK-ACTIONS-DESIGN.md.
"""
from __future__ import annotations

import re

FIELDS = ("key_toggle", "key_voice", "key_text")
DEFAULTS = {"key_toggle": "f13", "key_voice": "f18", "key_text": "f16"}
ROLES = {"key_toggle": "start / stop", "key_voice": "push to talk", "key_text": "type a command"}

# Keys another CODEC service already listens for. codec_dictate.py toggles live
# dictation on F5 (tests/test_hotkeys.py reads that file, so the two cannot drift).
RESERVED = {"f5": "live dictation (Dictate)"}

_FN_HELP = ("MacBook keyboards stop at F12. Turn on Use F1, F2, etc. keys as standard "
            "function keys in System Settings > Keyboard, or hold fn while you press them.")
PRESETS = (
    {"id": "extended", "label": "Extended keyboard (F13 and up)",
     "help": "F13 starts and stops CODEC, hold F18 to talk, F16 to type.",
     "keys": dict(DEFAULTS)},
    {"id": "laptop", "label": "Laptop keyboard (F1 to F12)",
     "help": "F6 starts and stops CODEC, hold F8 to talk, F9 to type. " + _FN_HELP,
     "keys": {"key_toggle": "f6", "key_voice": "f8", "key_text": "f9"}},
)

_NAME = re.compile(r"^f([1-9]|1[0-9]|20)$")  # pynput on macOS has Key.f1 to Key.f20


def normalize(name) -> str:
    return str(name if name is not None else "").strip().lower()


def is_valid(name) -> bool:
    return bool(_NAME.match(normalize(name)))


def label(name) -> str:
    """'f13' -> 'F13' (what the overlays and the console print)."""
    return normalize(name).upper()


def names(cfg) -> dict:
    """The three names the listener uses. A missing or unusable name falls back to
    its default, so a typo never leaves CODEC without a start key."""
    cfg = cfg if isinstance(cfg, dict) else {}
    out = {}
    for field in FIELDS:
        value = normalize(cfg.get(field))
        out[field] = value if is_valid(value) else DEFAULTS[field]
    return out


def field_problem(field: str, value) -> str | None:
    """Why one Settings value is refused, or None."""
    if not isinstance(value, str) or not is_valid(value):
        return f"{field}: choose a key from F1 to F20"
    if normalize(value) in RESERVED:
        return f"{field}: {label(value)} is already used for {RESERVED[normalize(value)]}"
    return None


def clash(keys: dict) -> str | None:
    """A message when two of the three hotkeys are the same key, else None."""
    seen = {}
    for field in FIELDS:
        name = normalize(keys.get(field))
        if name in seen:
            return (f"{field}: {label(name)} is already the key for "
                    f"{ROLES[seen[name]]}; pick a different key")
        seen[name] = field
    return None


def preset(preset_id) -> dict | None:
    for p in PRESETS:
        if p["id"] == preset_id:
            return dict(p["keys"])
    return None


def active_preset(cfg) -> str | None:
    current = names(cfg)
    for p in PRESETS:
        if p["keys"] == current:
            return p["id"]
    return None


def presets_payload(cfg) -> dict:
    """What GET /api/hotkeys/presets answers (Settings and the first-run screen)."""
    return {
        "presets": [{"id": p["id"], "label": p["label"], "help": p["help"], "keys": dict(p["keys"])}
                    for p in PRESETS],
        "current": names(cfg),
        "active": active_preset(cfg),
    }
