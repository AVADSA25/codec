"""Network information — IP, wifi, connectivity"""
SKILL_NAME = "network_info"
SKILL_TRIGGERS = ["my ip", "ip address", "wifi", "network info", "internet speed", "am i online", "what network"]
SKILL_DESCRIPTION = "Show network info, IP address, wifi status"

import subprocess, requests

def run(task, app="", ctx=""):
    info = []
    try:
        r = subprocess.run(["ipconfig", "getifaddr", "en0"], capture_output=True, text=True, timeout=5)
        local_ip = r.stdout.strip()
        if local_ip: info.append(f"Local IP: {local_ip}")
    except: pass
    try:
        r = requests.get("https://api.ipify.org", timeout=5)
        info.append(f"Public IP: {r.text}")
    except: info.append("Public IP: offline")
    try:
        r = subprocess.run(["networksetup", "-getairportnetwork", "en0"], capture_output=True, text=True, timeout=5)
        wifi = r.stdout.strip().replace("Current Wi-Fi Network: ", "")
        info.append(f"WiFi: {wifi}")
    except: pass
    result = "\\n".join(info) if info else "Could not get network info"
    # Open in Terminal
    safe = result.replace("'", "'\\''")
    subprocess.Popen(["osascript", "-e", f"""tell application "Terminal"
        activate
        do script "echo ''; echo '\\033[38;2;232;113;26m━━━ CODEC NETWORK ━━━\\033[0m'; echo ''; echo '{safe}'; echo ''"
    end tell"""])
    return result
