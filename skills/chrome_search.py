"""Chrome Search — search Google via AppleScript"""
import subprocess
import urllib.parse

SKILL_NAME = "chrome_search"
SKILL_DESCRIPTION = "Search Google in Chrome for any query"
SKILL_TRIGGERS = [
    "search google", "google search", "search for", "look up", "google for",
    "search the web", "web search", "find online", "search online"
]

def run(task, app="", ctx=""):
    try:
        task_lower = task.lower()

        # Extract search query by stripping trigger phrases (longest first to avoid partial matches)
        query = task
        triggers_sorted = sorted([
            "search google for", "google search for", "search for", "look up",
            "google for", "search the web for", "web search for", "find online",
            "search online for", "search google", "google search", "search the web",
            "web search", "search online"
        ], key=len, reverse=True)

        for trigger in triggers_sorted:
            if trigger in task_lower:
                idx = task_lower.index(trigger) + len(trigger)
                query = task[idx:].strip()
                break

        if not query or len(query) < 2:
            return "What would you like me to search for?"

        encoded = urllib.parse.quote_plus(query)
        url = f"https://www.google.com/search?q={encoded}"

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
            return f"Searching Google for: {query}"
        else:
            return f"Chrome error: {r.stderr.strip()}"
    except Exception as e:
        return f"Chrome search error: {e}"
