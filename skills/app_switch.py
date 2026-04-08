"""CODEC Skill: App Switcher"""
SKILL_NAME = "app_switch"
SKILL_DESCRIPTION = "Switch to any running app by name"
SKILL_TRIGGERS = ["switch to", "go to", "focus on", "bring up", "activate",
                   "show me", "open app", "open ", "switch up to", "switch up"]
import subprocess

# Common app name aliases
ALIASES = {
    "chrome": "Google Chrome", "safari": "Safari", "firefox": "Firefox",
    "terminal": "Terminal", "finder": "Finder", "mail": "Mail",
    "messages": "Messages", "whatsapp": "WhatsApp", "telegram": "Telegram",
    "slack": "Slack", "discord": "Discord", "spotify": "Spotify",
    "music": "Music", "notes": "Notes", "calendar": "Calendar",
    "photos": "Photos", "preview": "Preview", "vscode": "Visual Studio Code",
    "code": "Visual Studio Code", "xcode": "Xcode", "iterm": "iTerm2",
    "notion": "Notion", "figma": "Figma", "zoom": "zoom.us",
    "teams": "Microsoft Teams", "outlook": "Microsoft Outlook",
    "webui": "Google Chrome", "brave": "Brave Browser",
}

def run(task, app="", ctx=""):
    low = task.lower()
    # Extract app name
    target = low
    for remove in ["switch to", "go to", "focus on", "bring up", "activate",
                    "show me", "open app", "open ", "please", "can you", "the", "app",
                    "hey codec", "hey codec,", "codec"]:
        target = target.replace(remove, "")
    target = target.strip()
    if not target or len(target) < 2:
        return None

    # Check aliases
    app_name = ALIASES.get(target, None)
    if not app_name:
        # Try partial match
        for alias, full in ALIASES.items():
            if alias in target or target in alias:
                app_name = full
                break
    if not app_name:
        # Use the raw name with title case
        app_name = target.title()

    try:
        r = subprocess.run(["osascript", "-e",
            f'tell application "{app_name}" to activate'],
            capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return f"Switched to {app_name}."
        else:
            # Try open -a as fallback
            subprocess.run(["open", "-a", app_name], capture_output=True, timeout=5)
            return f"Opening {app_name}."
    except:
        return f"Couldn't find {app_name}."
