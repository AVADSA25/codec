"""
CODEC Skill — Philips Hue
==========================
Control Philips Hue lights: on/off, brightness, color, scenes.
Uses the Hue Bridge REST API v1 (local network, no HTTPS required).

Config keys in ~/.codec/config.json:
  hue_bridge_ip       - IP address of the Hue Bridge
  hue_bridge_username  - API username (from bridge registration)
"""

import json
import os
import random
import re

import requests

SKILL_NAME = "philips_hue"
SKILL_DESCRIPTION = "Control Philips Hue lights — on, off, brightness, color, scenes"
SKILL_MCP_EXPOSE = True

SKILL_TRIGGERS = [
    "lights on", "lights off", "turn on lights", "turn off lights",
    "dim lights", "bright lights", "set lights to",
    "living room lights", "bedroom lights", "kitchen lights",
    "light color", "lights red", "lights blue", "lights green",
    "lights purple", "lights orange", "lights pink",
    "warm lights", "cool lights",
    "light scene", "party mode", "reading mode", "movie mode",
    "relax mode", "energize mode", "codec mode",
    "all lights", "hue lights",
]

# ---------------------------------------------------------------------------
# Color presets: name -> (hue, saturation, brightness)
# ---------------------------------------------------------------------------
COLOR_PRESETS = {
    "red":        {"hue": 0,     "sat": 254, "bri": 200},
    "blue":       {"hue": 46920, "sat": 254, "bri": 200},
    "green":      {"hue": 25500, "sat": 254, "bri": 200},
    "warm":       {"hue": 8000,  "sat": 140, "bri": 254},
    "warm white": {"hue": 8000,  "sat": 140, "bri": 254},
    "cool":       {"hue": 34000, "sat": 50,  "bri": 254},
    "cool white": {"hue": 34000, "sat": 50,  "bri": 254},
    "purple":     {"hue": 50000, "sat": 254, "bri": 200},
    "orange":     {"hue": 5000,  "sat": 254, "bri": 254},
    "pink":       {"hue": 56000, "sat": 200, "bri": 200},
}

# ---------------------------------------------------------------------------
# Scene presets: name -> state dict sent to the bridge
# ---------------------------------------------------------------------------
SCENE_PRESETS = {
    "party":    {"on": True, "hue": random.randint(0, 65535), "sat": 254, "bri": 254},
    "reading":  {"on": True, "hue": 8000,  "sat": 140, "bri": 254},
    "movie":    {"on": True, "hue": 8000,  "sat": 140, "bri": 50},
    "relax":    {"on": True, "hue": 8000,  "sat": 140, "bri": 152},
    "energize": {"on": True, "hue": 34000, "sat": 50,  "bri": 254},
    "codec":    {"on": True, "hue": 5000,  "sat": 254, "bri": 254},
}

CONFIG_PATH = os.path.expanduser("~/.codec/config.json")
REQUEST_TIMEOUT = 5  # seconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config():
    """Return (bridge_ip, username) or (None, None) if not configured."""
    if not os.path.exists(CONFIG_PATH):
        return None, None
    try:
        with open(CONFIG_PATH, "r") as f:
            cfg = json.load(f)
        ip = cfg.get("hue_bridge_ip")
        user = cfg.get("hue_bridge_username")
        if ip and user:
            return ip, user
    except (json.JSONDecodeError, IOError):
        pass
    return None, None


def _setup_message():
    return (
        "Philips Hue is not configured yet. To set it up:\n"
        "\n"
        "1. Find your Hue Bridge IP:\n"
        "   - Visit https://discovery.meethue.com/ in a browser, or\n"
        "   - Check your router's connected-device list.\n"
        "\n"
        "2. Press the physical link button on top of your Hue Bridge.\n"
        "\n"
        "3. Within 30 seconds, register a new user:\n"
        '   curl -X POST http://<bridge-ip>/api '
        "-d '{\"devicetype\":\"codec#device\"}'\n"
        "   Copy the 'username' value from the response.\n"
        "\n"
        "4. Save both values in ~/.codec/config.json:\n"
        '   {\n'
        '     "hue_bridge_ip": "<bridge-ip>",\n'
        '     "hue_bridge_username": "<username>"\n'
        '   }\n'
        "\n"
        "Then try again!"
    )


def _api_url(ip, user, path=""):
    """Build a Hue API URL."""
    return f"http://{ip}/api/{user}{path}"


def _get(ip, user, path=""):
    """GET from the bridge. Returns parsed JSON or raises."""
    resp = requests.get(_api_url(ip, user, path), timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _put(ip, user, path, body):
    """PUT to the bridge. Returns parsed JSON or raises."""
    resp = requests.put(
        _api_url(ip, user, path),
        json=body,
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def _find_light_by_name(lights, name):
    """Return light id (str) matching *name* (case-insensitive), or None."""
    name_lower = name.lower()
    for lid, info in lights.items():
        if info.get("name", "").lower() == name_lower:
            return lid
    return None


def _find_group_by_name(groups, name):
    """Return group id (str) matching *name* (case-insensitive), or None."""
    name_lower = name.lower()
    for gid, info in groups.items():
        if info.get("name", "").lower() == name_lower:
            return gid
    return None


def _resolve_target(task_lower, ip, user):
    """
    Determine what lights to control based on the task text.

    Returns (target_type, target_id, target_name) where target_type is
    "all", "light", or "group".
    """
    # Check for "all lights" or no specific target
    if "all lights" in task_lower or "all" in task_lower.split():
        return "all", "0", "all lights"

    # Try to find a room/group name in the task
    try:
        groups = _get(ip, user, "/groups")
        for gid, info in groups.items():
            gname = info.get("name", "")
            if gname.lower() in task_lower:
                return "group", gid, gname
    except Exception:
        pass

    # Try to find a specific light name in the task
    try:
        lights = _get(ip, user, "/lights")
        for lid, info in lights.items():
            lname = info.get("name", "")
            if lname.lower() in task_lower:
                return "light", lid, lname
    except Exception:
        pass

    # Check for a light number like "light 3"
    m = re.search(r"light\s*#?\s*(\d+)", task_lower)
    if m:
        return "light", m.group(1), f"light {m.group(1)}"

    # Default: all lights (group 0)
    return "all", "0", "all lights"


def _apply_state(ip, user, target_type, target_id, state):
    """Send a state dict to the appropriate endpoint."""
    if target_type == "light":
        return _put(ip, user, f"/lights/{target_id}/state", state)
    else:
        # group 0 = all lights; other ids = specific groups
        return _put(ip, user, f"/groups/{target_id}/action", state)


# ---------------------------------------------------------------------------
# Task parsing
# ---------------------------------------------------------------------------

def _parse_action(task_lower):
    """
    Parse the user task into a state dict and a human-friendly description.
    Returns (state_dict, description_str).
    """
    # --- Scenes ---
    for scene_name, scene_state in SCENE_PRESETS.items():
        if scene_name in task_lower:
            # Regenerate random hue for party each time
            if scene_name == "party":
                scene_state = dict(scene_state)
                scene_state["hue"] = random.randint(0, 65535)
            return dict(scene_state), f"{scene_name} scene"

    # --- On / Off ---
    if re.search(r"\boff\b", task_lower):
        return {"on": False}, "off"
    if re.search(r"\bon\b", task_lower):
        return {"on": True}, "on"

    # --- Brightness by percentage ---
    m = re.search(r"(\d{1,3})\s*%", task_lower)
    if m:
        pct = min(int(m.group(1)), 100)
        bri = max(1, int(pct * 254 / 100))
        return {"on": True, "bri": bri}, f"{pct}% brightness"

    if "dim" in task_lower:
        return {"on": True, "bri": 50}, "dimmed"
    if "bright" in task_lower and "brightness" not in task_lower:
        return {"on": True, "bri": 254}, "full brightness"

    # --- Colors ---
    for color_name, color_vals in COLOR_PRESETS.items():
        if color_name in task_lower:
            state = {"on": True}
            state.update(color_vals)
            return state, f"{color_name} color"

    # Fallback: just turn on
    return {"on": True}, "on"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(task, app="", ctx=""):
    """Process a Philips Hue command and return a status string."""
    ip, user = _load_config()
    if not ip or not user:
        return _setup_message()

    task_lower = task.lower()

    try:
        # Determine target (all, specific light, or group/room)
        target_type, target_id, target_name = _resolve_target(task_lower, ip, user)

        # Determine desired state
        state, description = _parse_action(task_lower)

        # Apply
        result = _apply_state(ip, user, target_type, target_id, state)

        # Check for errors in the bridge response
        errors = [
            item.get("error", {}).get("description", "")
            for item in (result if isinstance(result, list) else [result])
            if isinstance(item, dict) and "error" in item
        ]
        if errors:
            return f"Hue Bridge error: {'; '.join(errors)}"

        return f"Set {target_name} to {description}."

    except requests.ConnectionError:
        return (
            f"Could not reach Hue Bridge at {ip}. "
            "Check that the bridge is on and your device is on the same network."
        )
    except requests.Timeout:
        return f"Hue Bridge at {ip} timed out. The bridge may be busy or unreachable."
    except requests.HTTPError as exc:
        return f"Hue Bridge HTTP error: {exc}"
    except Exception as exc:
        return f"Hue error: {exc}"
