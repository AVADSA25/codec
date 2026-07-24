#!/usr/bin/env python3
"""
CODEC — Philips Hue bridge setup
=================================
One command to pair the Hue Bridge (v2 / square) with CODEC:

    python3 setup_hue.py

Steps it runs:
  1. Discover the bridge IP (cloud discovery → mDNS fallback → manual prompt)
  2. Ask you to press the round link button on top of the bridge
  3. Register a CODEC API user (retries for ~30 s while you press)
  4. Write hue_bridge_ip + hue_bridge_username into ~/.codec/config.json
  5. Verify by listing the lights it can see

The local Hue REST API (v1) works on both v1 and v2 bridges — no Hue cloud
account, no app, no subscription. Everything stays on your LAN.

Flags:
  --ip <addr>     skip discovery, use this bridge IP
  --yes           non-interactive: assume the button is already pressed
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

CONFIG_PATH = Path(os.path.expanduser("~/.codec/config.json"))
DEVICETYPE = "codec#mac-studio"
DISCOVERY_URL = "https://discovery.meethue.com/"


# ─── small HTTP helpers (stdlib only) ────────────────────────────────────────────

def _get_json(url: str, timeout: float = 6.0):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _post_json(url: str, body: dict, timeout: float = 6.0):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# ─── steps ───────────────────────────────────────────────────────────────────────

def discover_bridge_ip() -> str | None:
    """Cloud discovery first, then mDNS, then None (caller prompts)."""
    # 1. Philips cloud discovery (returns LAN IP of bridges on your network)
    try:
        data = _get_json(DISCOVERY_URL)
        if isinstance(data, list) and data:
            ip = data[0].get("internalipaddress")
            if ip:
                print(f"  ✓ Found bridge via cloud discovery: {ip}")
                return ip
    except Exception as e:
        print(f"  · cloud discovery failed ({e}) — trying mDNS…")

    # 2. mDNS (_hue._tcp) if zeroconf is available — optional dependency
    try:
        from zeroconf import Zeroconf, ServiceBrowser  # type: ignore

        found: list[str] = []

        class _L:
            def add_service(self, zc, type_, name):
                info = zc.get_service_info(type_, name)
                if info and info.addresses:
                    import socket
                    found.append(socket.inet_ntoa(info.addresses[0]))

            def update_service(self, *a):
                pass

            def remove_service(self, *a):
                pass

        zc = Zeroconf()
        ServiceBrowser(zc, "_hue._tcp.local.", _L())
        time.sleep(3)
        zc.close()
        if found:
            print(f"  ✓ Found bridge via mDNS: {found[0]}")
            return found[0]
    except Exception:
        pass

    return None


def register(ip: str, yes: bool = False) -> str | None:
    """Press-the-button registration loop. Returns the API username or None."""
    url = f"http://{ip}/api"
    deadline = time.time() + 35  # ~30 s window after the button press
    if not yes:
        print("\n  → Press the round link button on TOP of the bridge NOW.")
        print("    (You have ~30 seconds. I'll keep trying…)\n")
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            resp = _post_json(url, {"devicetype": DEVICETYPE})
            # Hue returns a list: [{"success": {...}}] or [{"error": {...}}]
            if isinstance(resp, list) and resp:
                item = resp[0]
                if "success" in item:
                    user = item["success"]["username"]
                    print(f"  ✓ Registered. API username: {user[:10]}…")
                    return user
                err = item.get("error", {})
                # type 101 = link button not pressed
                if err.get("type") == 101:
                    print(f"    [{attempt}] waiting for button press…")
                else:
                    print(f"    bridge error: {err.get('description', err)}")
        except Exception as e:
            print(f"    [{attempt}] {e}")
        time.sleep(2)
    return None


def verify(ip: str, user: str) -> int:
    """Return the number of lights the bridge reports (0 on failure)."""
    try:
        lights = _get_json(f"http://{ip}/api/{user}/lights")
        if isinstance(lights, dict):
            return len(lights)
    except Exception as e:
        print(f"  · verify failed: {e}")
    return 0


def write_config(ip: str, user: str) -> None:
    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
        except Exception:
            pass
    cfg["hue_bridge_ip"] = ip
    cfg["hue_bridge_username"] = user
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    print(f"  ✓ Wrote hue_bridge_ip + hue_bridge_username to {CONFIG_PATH}")


# ─── main ──────────────────────────────────────────────────────────────────────

def main(argv: list[str]) -> int:
    ip = None
    yes = False
    for i, a in enumerate(argv):
        if a == "--ip" and i + 1 < len(argv):
            ip = argv[i + 1]
        elif a == "--yes":
            yes = True
        elif a in ("-h", "--help"):
            print(__doc__)
            return 0

    print("┌─ CODEC Hue Setup ───────────────────────────────")
    if not ip:
        print("│ [1/4] Discovering bridge…")
        ip = discover_bridge_ip()
        if not ip:
            try:
                ip = input("│ Could not auto-discover. Enter bridge IP: ").strip()
            except EOFError:
                ip = ""
        if not ip:
            print("└─ ✗ No bridge IP. Re-run with --ip <addr>.")
            return 1
    else:
        print(f"│ [1/4] Using bridge IP: {ip}")

    print("│ [2/4] Registering CODEC with the bridge…")
    user = register(ip, yes=yes)
    if not user:
        print("└─ ✗ Registration failed (button not pressed in time?). Re-run and press the button.")
        return 1

    print("│ [3/4] Saving config…")
    write_config(ip, user)

    print("│ [4/4] Verifying…")
    n = verify(ip, user)
    print(f"│       ✓ Bridge reports {n} light(s).")
    print("└─ ✓ Hue is connected. Try: \"Hey CODEC, turn on the lights\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
