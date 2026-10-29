"""CODEC Skill: save a note when asked (docs/NOTES-BY-ASKING-DESIGN.md).

"save a note: buy lemons" (typed in chat, spoken, or sent through a bridge) saves to CODEC Notes (Tasks > Notes).
Apple Notes is used only when the message says "apple notes", and over claude.ai (MCP) as before.
A model-written skill tag cannot save a note: only the owner's own message does."""
SKILL_NAME = "notes"
SKILL_DESCRIPTION = ("Save a note when asked: to CODEC Notes (Tasks > Notes), or to Apple Notes over claude.ai "
                     "or when the message says Apple Notes; shows where the notes are")
SKILL_MCP_EXPOSE = True
SKILL_TRIGGERS = ["take a note", "save a note", "note that", "remember that",
                   "my notes", "show notes", "read notes", "what did i note", "list notes",
                   "new note", "add a note", "make a note", "write a note", "jot down",
                   "note this", "make note", "note to self", "add to notes", "save to notes"]
import os
import re
import subprocess

# Phrases that mean "save this": a message with one of them is a write, never a read.
WRITE_VERBS = ("take a note", "save a note", "new note", "add a note",
               "make a note", "write a note", "note that", "remember that",
               "jot down", "note this", "make note", "with content",
               "saying", "that says", "note to self", "add to my notes", "add to notes",
               "save to my notes", "save to notes", "save this to my notes", "save that to my notes",
               "put in my notes", "put this in my notes", "put that in my notes")
READ_VERBS = ("list", "show", "read", "my notes", "open notes", "view notes",
              "what notes", "recent notes", "see notes", "what did i note")

# The words that start a save request, longest first: what follows them is the note.
_COMMANDS = sorted([
    "save this to my notes", "save that to my notes", "save it to my notes",
    "add this to my notes", "add that to my notes", "add it to my notes",
    "put this in my notes", "put that in my notes", "put it in my notes",
    "save to my notes", "add to my notes", "save to notes", "add to notes", "put in my notes",
    "take a note of", "take a note", "save a new note", "save a note", "add a note",
    "make a note", "write a note", "new note", "jot down", "note that", "note this",
    "note to self", "remember that", "make note",
], key=len, reverse=True)

NO_TEXT = "Tell me what to save, for example: save a note, buy lemons. Or press the bookmark under a reply."
NOT_BY_A_MODEL = "I only save a note when you ask me directly, in your own message."


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
        out = re.sub(r'<[^>]+>', '', out)
        out = re.sub(r'\n{3,}', '\n\n', out)
        return f"CODEC NOTES — last {limit} items\n{'=' * 40}\n\n{out}"
    except Exception as e:
        return f"Could not read notes: {e}"


def _shelf_pointer() -> str:
    """Where the notes are, and how many. Never what is in them."""
    try:
        import codec_notes
        if not codec_notes.enabled():
            return "Notes are switched off."
        total = codec_notes.list_notes(limit=1)["total"]
    except Exception:
        return "Your notes are in Tasks > Notes."
    if not total:
        return "You have no notes yet. They will be in Tasks > Notes."
    return f"Your notes are in Tasks > Notes ({total} {'note' if total == 1 else 'notes'})."


def _note_text(task: str) -> str:
    """What the owner asked to save: the words after the command phrase, in the case he used."""
    t = " ".join(task.split())
    for phrase in _COMMANDS:
        m = re.search(r"\b" + re.escape(phrase) + r"\b", t, re.IGNORECASE)
        if m:
            rest = t[m.end():].strip()
            break
    else:
        return ""
    # "in my notes", "to codec notes": where it goes is not part of the note
    rest = re.sub(r"^(?:(?:in|into|to|on)\s+)?(?:my\s+|the\s+)?(?:codec\s+)?notes?\b", "", rest, count=1, flags=re.IGNORECASE).strip()
    rest = re.sub(r"^(?:[:,\-]\s*|(?:saying|that says|with content|reading)\s+)", "", rest, flags=re.IGNORECASE)
    return rest.strip().strip("\"'").strip()


def _save_to_codec(task: str, app: str):
    if "llm-routed" in (app or "").lower():
        return NOT_BY_A_MODEL
    text = _note_text(task)
    if not text:
        return NO_TEXT
    try:
        import codec_notes
        if not codec_notes.enabled():
            return "Notes are switched off."
        note = codec_notes.create(text, source="chat")
    except Exception as e:
        return getattr(e, "message", None) or "Could not save the note."
    return f"Saved to Notes: {note['title']}"


def _save_to_apple(task: str):
    """The old way, unchanged: a note in the macOS Apple Notes app."""
    note = task
    # 1) "with content X", "saying X", "that says X", ': X', '"X"' — capture X
    for pat in [r'with\s+content\s+["\']?(.+?)["\']?$',
                r'saying\s+["\']?(.+?)["\']?$',
                r'that\s+says\s+["\']?(.+?)["\']?$',
                r':\s*["\']?(.+?)["\']?$',
                r'["\'](.+?)["\']']:
        m = re.search(pat, task, re.IGNORECASE)
        if m:
            note = m.group(1).strip()
            break
    else:
        # 2) Prefix-strip fallback (word-boundary to avoid chopping 'note' out of 'notebook' etc.)
        low = task.lower()
        low = re.sub(r"\b(?:in|to|into|on)\s+(?:the\s+|my\s+)?apple\s+notes?(?:\s+app)?\b", "", low)
        for remove in ["take a note of","take a note","save a note","save a new note",
                       "new note","add a note","make a note","write a note",
                       "note that","remember that","jot down","note this","make note",
                       "please","can you"]:
            pat = r'\b' + re.escape(remove) + r'\b'
            low = re.sub(pat, '', low, count=1)
        note = low.strip(" :,-").strip()
    if not note or len(note) < 1:
        return None
    import codec_applescript
    safe = codec_applescript.escape(note)
    try:
        subprocess.run(["osascript", "-e",
            f'tell application "Notes" to make new note at folder "Notes" with properties {{body:"{safe}"}}'],
            capture_output=True, text=True, timeout=10)
        return f"Saved to Apple Notes: {note}"
    except Exception:
        return "Failed to save note."


def run(task, app="", ctx=""):
    low = task.lower()
    apple = re.search(r"\bapple\s+notes?\b", low) is not None
    is_write = any(w in low for w in WRITE_VERBS)
    is_read = "notes" in low and any(v in low for v in READ_VERBS)
    if is_read and not is_write:
        if _is_remote_transport():
            # claude.ai / MCP: read the notes inline (no UI on the user's Mac).
            return _list_recent_notes_inline(limit=20)
        if apple:
            subprocess.run(["open", "-a", "Notes"], timeout=5)
            return "Opening Apple Notes."
        return _shelf_pointer()
    if apple or _is_remote_transport():
        return _save_to_apple(task)
    return _save_to_codec(task, app)
