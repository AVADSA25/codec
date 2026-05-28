"""CODEC Skill: Voice Notes via Apple Notes"""
SKILL_NAME = "notes"
SKILL_DESCRIPTION = "Save and recall notes via Apple Notes app"
SKILL_MCP_EXPOSE = True
SKILL_TRIGGERS = ["take a note", "save a note", "note that", "remember that",
                   "my notes", "show notes", "read notes", "what did i note", "list notes",
                   "new note", "add a note", "make a note", "write a note", "jot down",
                   "note this", "make note"]
import os
import subprocess


def _is_remote_transport() -> bool:
    """True for claude.ai / MCP HTTP path; False for local dashboard / voice."""
    return os.environ.get("CODEC_MCP_TRANSPORT", "stdio").lower() == "http"


def _list_recent_notes_inline(limit: int = 20) -> str:
    """For the MCP path: return the last N notes' titles + body previews
    as a string so claude.ai has them as context. Uses AppleScript to
    enumerate the Notes app — no DB poking, works without Full Disk Access."""
    script = f'''
tell application "Notes"
    set out to ""
    set noteList to notes
    set lim to {limit}
    set i to 1
    repeat with n in noteList
        if i > lim then exit repeat
        set t to name of n
        set b to body of n
        -- strip HTML tags from body (cheap pass)
        set out to out & "[" & (i as string) & "] " & t & linefeed & (b as text) & linefeed & linefeed
        set i to i + 1
    end repeat
    return out
end tell
'''
    try:
        r = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=15)
        out = (r.stdout or "").strip()
        if not out:
            return "Apple Notes is empty or unreachable."
        # Quick HTML strip — Notes returns body as HTML
        import re as _re
        out = _re.sub(r'<[^>]+>', '', out)
        out = _re.sub(r'\n{3,}', '\n\n', out)
        return f"CODEC NOTES — last {limit} items\n{'=' * 40}\n\n{out}"
    except Exception as e:
        return f"Could not read notes: {e}"


def run(task, app="", ctx=""):
    low = task.lower()
    # Read intent: any read verb + "notes" in the phrase. Guard against write verbs.
    WRITE_VERBS = ("take a note", "save a note", "new note", "add a note",
                   "make a note", "write a note", "note that", "remember that",
                   "jot down", "note this", "make note", "with content",
                   "saying", "that says")
    READ_VERBS = ("list", "show", "read", "my notes", "open notes", "view notes",
                  "what notes", "recent notes", "see notes", "what did i note")
    is_write = any(w in low for w in WRITE_VERBS)
    is_read = "notes" in low and any(v in low for v in READ_VERBS)
    if is_read and not is_write:
        if _is_remote_transport():
            # claude.ai / MCP: read the notes inline (no UI on the user's Mac).
            return _list_recent_notes_inline(limit=20)
        # Local path: open Apple Notes app for the human user.
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
