"""CODEC Tailscale — turn Tailscale on or off on this Mac, or say whether it is on.

Uses the command line inside the Tailscale app (App Store and standalone
variants), else a `tailscale` on PATH. "On" is a plain `tailscale up`, which
keeps every setting; "off" is `tailscale down`. Questions only read status."""
SKILL_NAME = "tailscale"
SKILL_TRIGGERS = [
    "turn on tailscale", "turn tailscale on", "tailscale on", "start tailscale",
    "connect tailscale", "enable tailscale",
    "turn off tailscale", "turn tailscale off", "tailscale off", "stop tailscale",
    "disconnect tailscale", "disable tailscale",
    "tailscale status", "is tailscale on", "is tailscale running", "check tailscale",
    "turn on tail scale", "tail scale on", "turn off tail scale", "tail scale off",
    "tail scale status",
]
SKILL_DESCRIPTION = "Turns Tailscale on or off on this Mac, or says whether it is connected"
SKILL_MCP_EXPOSE = False

import json
import os
import re
import shutil
import subprocess
import time

_APP_CLI = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"


def _cli():
    return _APP_CLI if os.path.exists(_APP_CLI) else shutil.which("tailscale")


def _intent(task):
    low = task.lower().replace("tail scale", "tailscale")
    if re.search(r"\b(status|is|check)\b", low):
        return "status"
    if re.search(r"\b(off|stop|disconnect|disable|down)\b", low):
        return "down"
    if re.search(r"\b(on|start|connect|enable|up)\b", low):
        return "up"
    return "status"


def _status(cli):
    """Parsed `tailscale status --json`, or None when the app does not answer."""
    r = subprocess.run([cli, "status", "--json"], capture_output=True, text=True, timeout=15)
    try:
        return json.loads(r.stdout)
    except ValueError:
        return None


def _describe(st):
    state = st.get("BackendState", "unknown")
    if state == "Running":
        peers = list((st.get("Peer") or {}).values())
        online = sum(1 for p in peers if p.get("Online"))
        return f"Tailscale is on. {online} of {len(peers)} other devices online."
    if state == "Stopped":
        return "Tailscale is off."
    if state == "NeedsLogin":
        return "Tailscale needs a login: open the Tailscale app on the Mac."
    return f"Tailscale state: {state}."


def run(task, app="", ctx=""):
    cli = _cli()
    if not cli:
        return "Tailscale is not installed on this Mac."
    try:
        st = _status(cli)
        if st is None:  # the app is not running: start it, then look again
            subprocess.run(["open", "-a", "Tailscale"], capture_output=True, timeout=15)
            time.sleep(5)
            st = _status(cli)
            if st is None:
                return "Tailscale is not answering. Open the Tailscale app on the Mac."
        intent = _intent(task)
        if intent == "status" or st.get("BackendState") == "NeedsLogin":
            return _describe(st)
        r = subprocess.run([cli, intent], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return f"Tailscale {intent} failed: {(r.stderr or r.stdout).strip()[:200]}"
        return _describe(_status(cli) or st)
    except subprocess.TimeoutExpired:
        return "Tailscale did not answer in time."
    except Exception as e:
        return f"Tailscale error: {e}"
