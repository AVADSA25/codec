"""CODEC Skill: Voice Notes via Apple Notes"""
SKILL_NAME = "notes"
SKILL_DESCRIPTION = "Save and recall notes via Apple Notes app"
SKILL_TRIGGERS = ["take a note", "save a note", "note that", "remember that",
                   "my notes", "show notes", "read notes", "what did i note", "list notes",
                   "new note", "add a note", "make a note", "write a note", "jot down",
                   "note this", "make note"]
import subprocess

def run(task, app="", ctx=""):
    low = task.lower()
    if any(k in low for k in ["my notes","show notes","read notes","what did i note","list notes"]):
        subprocess.run(["open", "-a", "Notes"], timeout=5)
        return "Opening Apple Notes."
    note = task
    for remove in ["take a note","save a note","note that","remember that",
                    "new note","add a note","make a note","write a note","jot down",
                    "note this","make note","please","can you","note"]:
        note = note.lower().replace(remove, "")
    note = note.strip().strip(":").strip()
    if not note or len(note) < 3: return None
    safe = note.replace('"', '\\"').replace("'", "")
    try:
        subprocess.run(["osascript", "-e",
            f'tell application "Notes" to make new note at folder "Notes" with properties {{body:"{safe}"}}'],
            capture_output=True, text=True, timeout=10)
        return f"Saved to Apple Notes: {note}"
    except Exception:
        return "Failed to save note."
