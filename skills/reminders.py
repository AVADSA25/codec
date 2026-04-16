"""CODEC Skill: Apple Reminders (add + list)"""
SKILL_NAME = "reminders"
SKILL_DESCRIPTION = "Add or list reminders via Apple Reminders app"
SKILL_MCP_EXPOSE = True
SKILL_TRIGGERS = [
    # add
    "add reminder", "set reminder", "remind me to", "add to reminders",
    "new reminder", "create reminder", "reminder to",
    # list / read
    "list reminders", "show reminders", "my reminders", "read reminders",
    "what reminders", "current reminders", "list current reminders",
    "outstanding reminders", "pending reminders",
]
import subprocess, re


def _is_read_intent(low: str) -> bool:
    READ_KEYS = ("list", "show", "my reminders", "read reminders", "what reminders",
                 "current reminders", "outstanding", "pending")
    # Only treat as read if no "add/create/set/remind me to" write verb present
    WRITE_KEYS = ("add reminder", "set reminder", "remind me to", "add to reminders",
                  "new reminder", "create reminder", "reminder to")
    if any(w in low for w in WRITE_KEYS):
        return False
    return any(k in low for k in READ_KEYS)


def _list_reminders(limit: int = 20) -> str:
    """Return a formatted string of incomplete reminders via AppleScript."""
    script = '''
    tell application "Reminders"
        set output to ""
        set theLists to (name of lists)
        repeat with listName in theLists
            set theList to list listName
            set openRems to (name of reminders of theList whose completed is false)
            repeat with r in openRems
                set output to output & listName & ": " & r & linefeed
            end repeat
        end repeat
        return output
    end tell
    '''
    try:
        r = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=15)
        out = (r.stdout or "").strip()
        if not out:
            return "No open reminders."
        lines = [l for l in out.splitlines() if l.strip()][:limit]
        return "Open reminders:\n" + "\n".join(f"  • {l}" for l in lines)
    except subprocess.TimeoutExpired:
        return "Reminders timed out (Apple Reminders took >15s to respond)."
    except Exception as e:
        return f"Failed to list reminders: {e}"


def run(task, app="", ctx=""):
    low = (task or "").lower()

    # ── Read intent takes precedence ──
    if _is_read_intent(low):
        return _list_reminders()

    # ── Write intent (original behavior) ──
    reminder = task
    for remove in ["add reminder", "set reminder", "remind me to", "add to reminders",
                   "new reminder", "create reminder", "reminder to", "please", "can you"]:
        reminder = re.sub(r'\b' + re.escape(remove) + r'\b', '', reminder, flags=re.IGNORECASE)
    reminder = reminder.strip().strip(":").strip()
    if not reminder or len(reminder) < 3:
        return None
    safe = reminder.replace('"', '\\"').replace("'", "")
    try:
        subprocess.run(["osascript", "-e",
            f'tell application "Reminders" to make new reminder in default list with properties {{name:"{safe}"}}'],
            capture_output=True, text=True, timeout=10)
        return f"Reminder added: {reminder}"
    except Exception as e:
        return f"Failed to add reminder: {e}"
