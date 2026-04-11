"""Pre-built browser automation routines via CDP"""
import sys, os
sys.path.insert(0, os.path.expanduser("~/codec-repo"))

SKILL_NAME = "chrome_automate"
SKILL_TRIGGERS = ["morning tabs", "open work tabs", "research mode", "close everything",
                  "open my tabs", "browser routine", "work mode", "focus mode tabs"]
SKILL_DESCRIPTION = "Run pre-built browser automation routines: morning tabs, work mode, research mode"

WORK_TABS = [
    "https://mail.google.com",
    "https://calendar.google.com",
    "https://drive.google.com",
    "https://slack.com",
]

RESEARCH_TABS = [
    "https://scholar.google.com",
    "https://www.semanticscholar.org",
    "https://news.ycombinator.com",
]

def run(task: str, context: str = "") -> str:
    import subprocess

    task_lower = task.lower()

    if "morning" in task_lower or "work tab" in task_lower or "work mode" in task_lower:
        urls = WORK_TABS
        label = "morning work"
    elif "research" in task_lower:
        urls = RESEARCH_TABS
        label = "research"
    elif "close" in task_lower or "focus" in task_lower:
        # Close all tabs via AppleScript
        subprocess.run(["osascript", "-e", 'tell application "Google Chrome" to close every tab of every window'],
                      capture_output=True)
        return "Closed all Chrome tabs"
    else:
        return "Try: 'morning tabs', 'work mode', 'research mode', or 'close everything'"

    # Open tabs via AppleScript (doesn't require CDP)
    for url in urls:
        subprocess.run(
            ["osascript", "-e", f'tell application "Google Chrome" to open location "{url}"'],
            capture_output=True
        )

    return f"Opened {len(urls)} {label} tabs: {', '.join(u.split('/')[2] for u in urls)}"
