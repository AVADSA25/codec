"""CODEC Skill: Voice Notes — save and recall quick notes"""
SKILL_NAME = "notes"
SKILL_DESCRIPTION = "Save and recall quick voice notes"
SKILL_TRIGGERS = ["take a note", "save a note", "note that", "remember that", "remind me",
                   "my notes", "show notes", "read notes", "what did i note", "list notes",
                   "delete note", "clear notes"]

import sqlite3, os
from datetime import datetime

DB = os.path.expanduser("~/.codec/notes.db")

def _init():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, content TEXT)")
    c.commit(); c.close()

def run(task, app="", ctx=""):
    _init()
    low = task.lower()

    # Read/list notes
    if any(k in low for k in ["my notes", "show notes", "read notes", "what did i note", "list notes"]):
        c = sqlite3.connect(DB)
        rows = c.execute("SELECT timestamp, content FROM notes ORDER BY id DESC LIMIT 10").fetchall()
        c.close()
        if not rows:
            return "No notes saved yet."
        lines = []
        for ts, content in rows:
            t = ts[:16].replace("T", " ")
            lines.append(f"[{t}] {content}")
        return "Your notes: " + " | ".join(lines)

    # Clear notes
    if "clear notes" in low or "delete note" in low:
        c = sqlite3.connect(DB)
        c.execute("DELETE FROM notes")
        c.commit(); c.close()
        return "All notes cleared."

    # Save note
    note = task
    for remove in ["take a note", "save a note", "note that", "remember that",
                    "remind me", "please", "can you", "note"]:
        note = note.lower().replace(remove, "")
    note = note.strip().strip(":").strip()
    if not note or len(note) < 3:
        return None  # Decline

    c = sqlite3.connect(DB)
    c.execute("INSERT INTO notes (timestamp, content) VALUES (?, ?)",
        (datetime.now().isoformat(), note))
    c.commit(); c.close()
    return f"Note saved: {note}"
