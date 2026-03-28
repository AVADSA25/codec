"""Control screen brightness"""
SKILL_NAME = "brightness"
SKILL_TRIGGERS = ["brightness", "screen bright", "dim screen", "brighten", "dark screen"]
SKILL_DESCRIPTION = "Adjust screen brightness"

import subprocess

def run(task, app="", ctx=""):
    t = task.lower()
    if any(w in t for w in ["max", "full", "100"]):
        level = 1.0
    elif any(w in t for w in ["dim", "low", "dark", "minimum"]):
        level = 0.2
    elif any(w in t for w in ["half", "50", "medium"]):
        level = 0.5
    else:
        # Try to extract number
        import re
        nums = re.findall(r'\d+', t)
        if nums:
            level = min(int(nums[0]), 100) / 100
        else:
            level = 0.7
    subprocess.run(["osascript", "-e", f'tell application "System Preferences" to quit'], capture_output=True)
    # Use AppleScript brightness control
    subprocess.run(["osascript", "-e", f"""
        do shell script "brightness {level}" 
    """], capture_output=True)
    return f"Brightness set to {int(level*100)}%"
