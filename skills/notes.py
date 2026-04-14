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
    # Extract content — prefer explicit markers, fall back to prefix stripping
    import re as _re
    note = task
    # 1) "with content X", "saying X", "that says X", ': X', '"X"' — capture X
    for pat in [r'with\s+content\s+["\']?(.+?)["\']?$',
                r'saying\s+["\']?(.+?)["\']?$',
                r'that\s+says\s+["\']?(.+?)["\']?$',
                r':\s*["\']?(.+?)["\']?$',
                r'["\'](.+?)["\']']:
        m = _re.search(pat, task, _re.IGNORECASE)
        if m:
            note = m.group(1).strip()
            break
    else:
        # 2) Prefix-strip fallback (word-boundary to avoid chopping 'note' out of 'notebook' etc.)
        low = task.lower()
        for remove in ["take a note of","take a note","save a note","save a new note",
                       "new note","add a note","make a note","write a note",
                       "note that","remember that","jot down","note this","make note",
                       "please","can you"]:
            pat = r'\b' + _re.escape(remove) + r'\b'
            low = _re.sub(pat, '', low, count=1)
        note = low.strip(" :,-").strip()
    if not note or len(note) < 1:
        return None
    safe = note.replace('"', '\\"').replace("'", "")
    try:
        subprocess.run(["osascript", "-e",
            f'tell application "Notes" to make new note at folder "Notes" with properties {{body:"{safe}"}}'],
            capture_output=True, text=True, timeout=10)
        return f"Saved to Apple Notes: {note}"
    except Exception:
        return "Failed to save note."
