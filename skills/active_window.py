"""CODEC Skill: Return the currently focused app + window title"""
SKILL_NAME = "active_window"
SKILL_DESCRIPTION = "Return the frontmost app name, window title, and bundle id"
SKILL_TRIGGERS = [
    "active window", "frontmost app", "focused app", "what app",
    "what window", "current window", "which app is open",
    "what's on screen", "what am i looking at",
]
SKILL_MCP_EXPOSE = True

import subprocess, json

_SCRIPT = '''
tell application "System Events"
    set frontApp to first application process whose frontmost is true
    set appName to name of frontApp
    set bundleId to bundle identifier of frontApp
    try
        set winName to name of front window of frontApp
    on error
        set winName to ""
    end try
end tell
return appName & "||" & bundleId & "||" & winName
'''


def run(task="", app="", ctx=""):
    try:
        r = subprocess.run(["osascript", "-e", _SCRIPT],
                           capture_output=True, text=True, timeout=5)
        out = (r.stdout or "").strip()
        if not out:
            return json.dumps({"error": r.stderr.strip() or "no output"})
        parts = out.split("||")
        while len(parts) < 3:
            parts.append("")
        return json.dumps({
            "app": parts[0],
            "bundle_id": parts[1],
            "window_title": parts[2],
        })
    except Exception as e:
        return json.dumps({"error": str(e)})
