"""Network information — IP, wifi, connectivity"""
SKILL_NAME = "network_info"
SKILL_TRIGGERS = ["my ip", "ip address", "wifi", "network", "internet speed", "am i online", "what network"]
SKILL_DESCRIPTION = "Show network info, IP address, wifi status"

import subprocess, requests

def run(task, app="", ctx=""):
    info = []
    try:
        r = subprocess.run(["ipconfig", "getifaddr", "en0"], capture_output=True, text=True, timeout=5)
        local_ip = r.stdout.strip()
        if local_ip:
            info.append(f"Local IP: {local_ip}")
    except: pass
    try:
        r = requests.get("https://api.ipify.org", timeout=5)
        info.append(f"Public IP: {r.text}")
    except:
        info.append("Public IP: offline or unreachable")
    try:
        r = subprocess.run(["networksetup", "-getairportnetwork", "en0"], capture_output=True, text=True, timeout=5)
        wifi = r.stdout.strip().replace("Current Wi-Fi Network: ", "")
        info.append(f"WiFi: {wifi}")
    except: pass
    return "\n".join(info) if info else "Could not get network info"
