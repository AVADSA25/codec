"""CODEC Skill: App Switcher"""
SKILL_NAME = "app_switch"
SKILL_DESCRIPTION = "Switch to any running app by name. If a URL is present, open it in the named browser."
SKILL_TRIGGERS = ["switch to", "go to app", "focus on", "bring up", "activate app",
                   "open app", "open the app", "switch up to", "switch up"]
import subprocess, re

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

    # ── URL handling: if an http(s):// URL or bare domain is present, open it
    #   in the requested browser (defaults to Safari). Keeps app focus honored.
    url_match = re.search(r'https?://\S+', task)
    if not url_match:
        # Bare-domain fallback — "time.com", "github.com/foo", etc.
        dom_match = re.search(r'\b([a-zA-Z0-9-]+\.(?:com|org|net|io|dev|ai|co|app|us|uk|fr|es|de)(?:/\S*)?)\b', task)
        if dom_match:
            url_match = dom_match
    if url_match:
        url = url_match.group(0)
        if not url.startswith("http"):
            url = "https://" + url
        # Pick browser: default Safari unless Chrome / Firefox / Brave / Arc named
        browser = "Safari"
        for name, full in [("chrome", "Google Chrome"), ("firefox", "Firefox"),
                           ("brave", "Brave Browser"), ("arc", "Arc")]:
            if name in low:
                browser = full
                break
        try:
            subprocess.run(["open", "-a", browser, url], capture_output=True, timeout=5)
            return f"Opened {url} in {browser}."
        except Exception:
            return f"Couldn't open {url} in {browser}."

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
