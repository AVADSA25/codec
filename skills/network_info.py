"""Network information — IP, wifi, connectivity"""
SKILL_NAME = "network_info"
SKILL_TRIGGERS = ["my ip", "ip address", "wifi", "network info", "internet speed",
                  "am i online", "what network"]
SKILL_DESCRIPTION = "Show network info, IP address, wifi status"

import subprocess, requests


def _first_ipv4() -> str:
    """Return the first non-loopback IPv4 from en0..en9."""
    for i in range(10):
        iface = f"en{i}"
        try:
            r = subprocess.run(["ipconfig", "getifaddr", iface],
                               capture_output=True, text=True, timeout=3)
            ip = r.stdout.strip()
            if ip and not ip.startswith("127."):
                return f"{ip} ({iface})"
        except Exception:
            continue
    return ""


def _wifi_info() -> str:
    """Try to get current Wi-Fi SSID — iterate interfaces, skip ethernet errors."""
    for i in range(10):
        iface = f"en{i}"
        try:
            r = subprocess.run(["networksetup", "-getairportnetwork", iface],
                               capture_output=True, text=True, timeout=3)
            out = (r.stdout or "").strip()
            if out and "not a Wi-Fi" not in out and "not associated" not in out.lower():
                return out.replace("Current Wi-Fi Network: ", f"[{iface}] ")
        except Exception:
            continue
    return "WiFi: not connected (wired/ethernet only)"


def run(task, app="", ctx=""):
    info = []
    ip = _first_ipv4()
    if ip:
        info.append(f"Local IP: {ip}")
    try:
        r = requests.get("https://api.ipify.org", timeout=5)
        info.append(f"Public IP: {r.text}")
    except Exception:
        info.append("Public IP: offline")
    info.append(_wifi_info())

    result = "\n".join(info) if info else "Could not get network info"
    # Open in Terminal
    safe = result.replace("'", "'\\''").replace("\n", "\\n")
    subprocess.Popen([
        "osascript", "-e",
        f"""tell application "Terminal"
            activate
            do script "echo ''; echo '\\033[38;2;232;113;26m━━━ CODEC NETWORK ━━━\\033[0m'; echo ''; printf '{safe}\\n'; echo ''"
        end tell"""
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result
