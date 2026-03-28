"""Chrome Tabs — list and switch between Chrome tabs via AppleScript"""
import subprocess

SKILL_NAME = "chrome_tabs"
SKILL_DESCRIPTION = "List all open Chrome tabs and switch between them"
SKILL_TRIGGERS = [
    "my tabs", "chrome tabs", "list tabs", "show tabs", "switch to tab",
    "switch tab", "open tabs", "which tabs", "how many tabs", "tab list"
]

def run(task, app="", ctx=""):
    try:
        task_lower = task.lower()

        # Check if user wants to switch to a specific tab
        switch_to = ""
        for phrase in ["switch to tab", "switch to", "go to tab", "open tab"]:
            if phrase in task_lower:
                switch_to = task_lower.split(phrase)[-1].strip()
                break

        if switch_to:
            script = f'''
tell application "Google Chrome"
    set targetQuery to "{switch_to}"
    repeat with w in windows
        set tabIndex to 1
        repeat with t in tabs of w
            if (title of t) contains targetQuery or (URL of t) contains targetQuery then
                set active tab index of w to tabIndex
                set index of w to 1
                activate
                return "Switched to: " & title of t
            end if
            set tabIndex to tabIndex + 1
        end repeat
    end repeat
    return "No tab found matching: " & targetQuery
end tell
'''
            r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=10)
            return r.stdout.strip() if r.returncode == 0 else f"Error: {r.stderr.strip()}"

        # List all tabs
        script = '''
tell application "Google Chrome"
    set output to ""
    set winNum to 1
    repeat with w in windows
        set tabNum to 1
        repeat with t in tabs of w
            set isActive to ""
            if tabNum = (active tab index of w) and winNum = 1 then set isActive to " \u2190 active"
            set output to output & tabNum & ". " & title of t & isActive & "\n"
            set tabNum to tabNum + 1
        end repeat
        set winNum to winNum + 1
    end repeat
    return output
end tell
'''
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=10)

        if r.returncode == 0 and r.stdout.strip():
            tabs = r.stdout.strip()
            tab_count = tabs.count("\n") + 1
            return f"\U0001f4cb **Chrome Tabs** ({tab_count} open):\n\n{tabs}\n\n_Say 'switch to [name]' to jump to a tab._"
        else:
            return "No Chrome tabs found. Is Chrome running?"
    except Exception as e:
        return f"Chrome tabs error: {e}"
