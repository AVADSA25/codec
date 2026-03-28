"""Chrome Open — open URLs and new tabs via AppleScript"""
import subprocess

SKILL_NAME = "chrome_open"
SKILL_DESCRIPTION = "Open URLs and websites in Google Chrome"
SKILL_TRIGGERS = [
    "open chrome", "open url", "open website", "open page", "go to", "browse to",
    "navigate to", "open tab", "new tab", "open google", "open youtube",
    "open gmail", "open github"
]

# Common shortcuts
SHORTCUTS = {
    "gmail": "https://mail.google.com",
    "calendar": "https://calendar.google.com",
    "drive": "https://drive.google.com",
    "youtube": "https://youtube.com",
    "github": "https://github.com",
    "reddit": "https://reddit.com",
    "twitter": "https://twitter.com",
    "x": "https://x.com",
    "google": "https://google.com",
    "docs": "https://docs.google.com",
    "sheets": "https://sheets.google.com",
    "slack": "https://app.slack.com",
    "notion": "https://notion.so",
    "linkedin": "https://linkedin.com",
    "news": "https://news.google.com",
    "maps": "https://maps.google.com",
}

def run(task, app="", ctx=""):
    try:
        task_lower = task.lower()

        # Extract URL or site name
        url = ""

        # Check for direct URLs
        for word in task.split():
            if word.startswith("http://") or word.startswith("https://"):
                url = word
                break
            if "." in word and len(word) > 4 and " " not in word:
                url = "https://" + word
                break

        # Check shortcuts
        if not url:
            for name, shortcut_url in SHORTCUTS.items():
                if name in task_lower:
                    url = shortcut_url
                    break

        # Just open new tab if no URL found
        if not url:
            script = 'tell application "Google Chrome" to activate'
            subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5)
            script2 = 'tell application "Google Chrome" to make new tab at end of tabs of front window'
            subprocess.run(["osascript", "-e", script2], capture_output=True, text=True, timeout=5)
            return "Opened a new Chrome tab."

        script = f'''
tell application "Google Chrome"
    activate
    if (count of windows) = 0 then
        make new window
        set URL of active tab of front window to "{url}"
    else
        make new tab at end of tabs of front window with properties {{URL:"{url}"}}
    end if
end tell
'''
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            return f"Opened {url} in Chrome."
        else:
            return f"Chrome error: {r.stderr.strip()}"
    except Exception as e:
        return f"Chrome open error: {e}"
