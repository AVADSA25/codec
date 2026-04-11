"""CODEC Skill: Apple Reminders"""
SKILL_NAME = "reminders"
SKILL_DESCRIPTION = "Add reminders via Apple Reminders app"
SKILL_TRIGGERS = ["add reminder", "set reminder", "remind me to", "add to reminders",
                   "new reminder", "create reminder", "reminder to"]
import subprocess

def run(task, app="", ctx=""):
    task.lower()
    reminder = task
    for remove in ["add reminder", "set reminder", "remind me to", "add to reminders",
                    "new reminder", "create reminder", "reminder to", "please", "can you"]:
        reminder = reminder.lower().replace(remove, "")
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
