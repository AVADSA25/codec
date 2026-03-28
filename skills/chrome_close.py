"""Chrome Close — close tabs, windows, or Chrome via AppleScript"""
import subprocess

SKILL_NAME = "chrome_close"
SKILL_DESCRIPTION = "Close Chrome tabs, windows, or quit Chrome entirely"
SKILL_TRIGGERS = [
    "close tab", "close chrome", "close this tab", "close window",
    "close all tabs", "close browser", "quit chrome", "exit chrome"
]

def run(task, app="", ctx=""):
    try:
        task_lower = task.lower()

        if "quit" in task_lower or "exit chrome" in task_lower:
            script = 'tell application "Google Chrome" to quit'
            subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5)
            return "Chrome has been quit."

        if "all tabs" in task_lower or "close all" in task_lower:
            script = '''
tell application "Google Chrome"
    set tabCount to count of tabs of front window
    repeat (tabCount - 1) times
        close active tab of front window
    end repeat
end tell
'''
            subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=10)
            return "Closed all Chrome tabs except one."

        if "window" in task_lower:
            script = 'tell application "Google Chrome" to close front window'
            subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5)
            return "Closed the Chrome window."

        # Default: close current tab
        script = 'tell application "Google Chrome" to close active tab of front window'
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return "Closed the current Chrome tab."
        else:
            return f"Chrome error: {r.stderr.strip()}"
    except Exception as e:
        return f"Chrome close error: {e}"
