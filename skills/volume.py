"""CODEC Skill: Brightness & Volume Control"""
SKILL_NAME = "volume_brightness"
SKILL_DESCRIPTION = "Control volume and brightness by voice"
SKILL_TRIGGERS = ["volume up", "volume down", "set volume", "mute", "unmute",
                   "louder", "quieter", "brightness up", "brightness down",
                   "turn up", "turn down", "volume to", "max volume", "silence"]
import subprocess, re

def run(task, app="", ctx=""):
    low = task.lower()
    # Volume
    if "mute" in low or "silence" in low:
        subprocess.run(["osascript", "-e", "set volume output volume 0"], timeout=5)
        return "Muted."
    if "unmute" in low:
        subprocess.run(["osascript", "-e", "set volume output volume 50"], timeout=5)
        return "Unmuted."
    if "max volume" in low:
        subprocess.run(["osascript", "-e", "set volume output volume 100"], timeout=5)
        return "Volume set to maximum."
    if any(k in low for k in ["volume up", "louder", "turn up the volume", "turn it up"]):
        r = subprocess.run(["osascript", "-e", "output volume of (get volume settings)"], capture_output=True, text=True, timeout=5)
        cur = int(r.stdout.strip()) if r.stdout.strip().isdigit() else 50
        new = min(100, cur + 15)
        subprocess.run(["osascript", "-e", f"set volume output volume {new}"], timeout=5)
        return f"Volume up to {new}%."
    if any(k in low for k in ["volume down", "quieter", "turn down the volume", "turn it down"]):
        r = subprocess.run(["osascript", "-e", "output volume of (get volume settings)"], capture_output=True, text=True, timeout=5)
        cur = int(r.stdout.strip()) if r.stdout.strip().isdigit() else 50
        new = max(0, cur - 15)
        subprocess.run(["osascript", "-e", f"set volume output volume {new}"], timeout=5)
        return f"Volume down to {new}%."
    num = re.search(r'volume\s*(?:to|at)?\s*(\d+)', low)
    if num:
        v = min(100, int(num.group(1)))
        subprocess.run(["osascript", "-e", f"set volume output volume {v}"], timeout=5)
        return f"Volume set to {v}%."
    # Brightness
    if "brightness up" in low:
        subprocess.run(["bash", "-c", "brightness=$(osascript -e 'tell application \"System Events\" to tell appearance preferences to get dark mode'); echo $brightness"], timeout=5)
        return "Brightness control requires System Preferences access. Try: Hey CODEC, open display settings."
    if "brightness down" in low:
        return "Brightness control requires System Preferences access. Try: Hey CODEC, open display settings."
    return None
