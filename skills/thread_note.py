"""CODEC Daybreak — working-threads capture (docs/DAYBREAK-DESIGN.md).

Every trigger contains the word "thread" on purpose — natural-language
triggers spuriously fire (the shift_report incident); the namespace keeps
this skill explicit-invocation only.
"""
SKILL_NAME = "thread_note"
SKILL_TRIGGERS = [
    "note a thread", "track a thread", "new thread",
    "close thread", "close the thread", "thread done",
    "open threads", "my threads", "list threads",
]
SKILL_DESCRIPTION = ("Track open working threads (working on / waiting on / "
                     "priority / follow up), list them, or close one when done. "
                     "Threads persist and inform CODEC's context everywhere.")
SKILL_MCP_EXPOSE = True

import os
import re
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_REPO, os.path.expanduser("~/codec-repo")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

_CLOSE_RE = re.compile(r"\b(?:close(?:\s+the)?\s+thread|thread\s+done)\b[:,]?\s*(.*)", re.IGNORECASE)
_OPEN_RE = re.compile(r"\b(?:note\s+a\s+thread|track\s+a\s+thread|new\s+thread)\b[:,]?\s*(.*)", re.IGNORECASE)
_LIST_RE = re.compile(r"\b(?:open\s+threads|my\s+threads|list\s+threads)\b", re.IGNORECASE)


def _infer_kind(text):
    low = text.lower()
    if "waiting on" in low or "waiting for" in low:
        return "waiting_on"
    if "priority" in low:
        return "priority"
    if "follow up" in low or "follow-up" in low:
        return "follow_up"
    return "working_on"


def run(task, app="", ctx=""):
    try:
        import codec_daybreak as db
        task = (task or "").strip()

        m = _CLOSE_RE.search(task)
        if m:
            target = m.group(1).strip()
            if not target:
                return "Tell me which thread to close."
            return db.close_thread(target)

        if _LIST_RE.search(task):
            threads = db.get_open_threads()
            if not threads:
                return "No open threads — clean slate."
            lines = ["Open threads:"]
            for t in threads:
                lines.append(f"- {t['kind'].replace('_', ' ')}: {t['text'][:90]} (since {t['since']})")
            return "\n".join(lines)

        m = _OPEN_RE.search(task)
        text = m.group(1).strip() if m else ""
        if not text:
            return ("Usage: 'note a thread <what you're working on / waiting on>', "
                    "'open threads', or 'close thread <match>'.")
        kind = _infer_kind(text)
        db.save_thread(kind, text)
        return f"Tracked ({kind.replace('_', ' ')}): {text[:90]}"
    except Exception as e:
        return f"Thread note error: {e}"
