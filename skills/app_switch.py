"""CODEC Skill: App Switcher"""
SKILL_NAME = "app_switch"
SKILL_DESCRIPTION = "Switch to any running app by name. If a URL is present, open it in the named browser."
SKILL_MCP_EXPOSE = True
SKILL_TRIGGERS = ["switch to", "go to app", "focus on", "bring up", "activate app",
                   "open app", "open the app", "switch up to", "switch up",
                   # 2026-07-07: natural "open <site> in <browser>" phrasing
                   # (e.g. "open Time Magazine in Safari") wasn't matching
                   # anything, so it fell through to the general agent loop —
                   # visible Terminal window + a bare `open <url>` bash command
                   # that respects the SYSTEM default browser, not the one the
                   # user actually named. This skill already handles browser
                   # targeting correctly (explicit `open -a <Browser>`); it
                   # just needed a trigger to be reached.
                   "in safari", "in chrome", "in firefox", "in brave", "in the browser"]
import subprocess
import re

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

# Well-known site names that don't read as a bare domain ("Time Magazine" vs
# "time.com") — checked before the domain regex so "open Time Magazine in
# Safari" resolves without needing the literal URL spoken. Small and curated
# on purpose: anything not listed here still falls through to the general
# agent, which has the broader knowledge to resolve arbitrary site names.
SITE_ALIASES = {
    "time magazine": "time.com",
    "time": "time.com",
    "new york times": "nytimes.com",
    "nytimes": "nytimes.com",
    "wall street journal": "wsj.com",
    "washington post": "washingtonpost.com",
    "the verge": "theverge.com",
    "techcrunch": "techcrunch.com",
    "bbc": "bbc.com",
    "cnn": "cnn.com",
}

def run(task, app="", ctx=""):
    low = task.lower()

    # ── URL handling: if an http(s):// URL, bare domain, or known site name is
    #   present, open it in the requested browser (defaults to Safari).
    url_match = re.search(r'https?://\S+', task)
    if not url_match:
        # Bare-domain fallback — "time.com", "github.com/foo", etc.
        dom_match = re.search(r'\b([a-zA-Z0-9-]+\.(?:com|org|net|io|dev|ai|co|app|us|uk|fr|es|de)(?:/\S*)?)\b', task)
        if dom_match:
            url_match = dom_match
    site_url = None
    if url_match:
        site_url = url_match.group(0)
    else:
        # Known site NAME (not a domain) — "Time Magazine", "BBC", etc.
        # Longest match first so "new york times" wins over a shorter partial.
        for name in sorted(SITE_ALIASES, key=len, reverse=True):
            if re.search(r'\b' + re.escape(name) + r'\b', low):
                site_url = SITE_ALIASES[name]
                break
    if site_url:
        url = site_url if site_url.startswith("http") else "https://" + site_url
        # Pick browser: default Safari unless Chrome / Firefox / Brave / Arc named
        browser = "Safari"
        for name, full in [("chrome", "Google Chrome"), ("firefox", "Firefox"),
                           ("brave", "Brave Browser"), ("arc", "Arc")]:
            if name in low:
                browser = full
                break
        try:
            r = subprocess.run(["open", "-a", browser, url], capture_output=True, timeout=5)
            if r.returncode == 0:
                return f"Opened {url} in {browser}."
            return None  # let a later-matched skill / the agent try instead
        except Exception:
            return None

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
        # Try open -a as fallback — but only claim success if it actually
        # worked. Previously this returned "Opening {app_name}." even when
        # BOTH the activate and the open -a failed (e.g. a garbled phrase
        # like "Echodec Time Magazine In Safari." title-cased into a fake
        # app name) — a false-positive success message with nothing open.
        r2 = subprocess.run(["open", "-a", app_name], capture_output=True, timeout=5)
        if r2.returncode == 0:
            return f"Opening {app_name}."
        return None  # let a later-matched skill / the agent try instead
    except Exception:
        return None
