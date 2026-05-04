"""CODEC Skill: Save a note/fact to CODEC memory (searchable via FTS5)"""
SKILL_NAME = "memory_save"
SKILL_DESCRIPTION = "Save a fact or note to CODEC's searchable memory (FTS5)"
SKILL_TRIGGERS = [
    "remember that", "save to memory", "memorize", "note to memory",
    "store in memory", "remember this", "save note",
    "log that", "record that",
]
SKILL_MCP_EXPOSE = True

import os, sys, re
from datetime import datetime

_REPO = os.path.expanduser("~/codec-repo")
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

_VERBS = (
    "remember that", "save to memory", "store in memory",
    "note to memory", "remember this", "save note",
    "record that", "log that", "memorize",
)


def _extract(task: str) -> str:
    t = task.strip()
    low = t.lower()
    for v in sorted(_VERBS, key=len, reverse=True):
        m = re.match(r'^\s*' + re.escape(v) + r'\b', low)
        if m:
            t = t[m.end():].strip()
            break
    t = re.sub(r'^\s*[:,\-]+\s*', '', t).strip()
    return t.strip('"\'').strip()


def run(task, app="", ctx=""):
    content = _extract(task)
    if not content or len(content) < 2:
        return "What should I remember? (e.g. 'remember that MF prefers espresso after 3pm')"
    try:
        from codec_memory import CodecMemory
        mem = CodecMemory()
        session_id = f"claude-mcp-{datetime.now().strftime('%Y%m%d')}"
        row_id = mem.save(session_id, role="fact", content=content, user_id="default")
        # Also try temporal-fact path if available
        try:
            from codec_memory_upgrade import store_fact
            # Heuristic: if content looks like "X = Y" or "X is Y", store as fact
            m = re.match(r'^(.+?)\s*(?:=|is|are|equals)\s*(.+)$', content, re.IGNORECASE)
            if m:
                store_fact(m.group(1).strip(), m.group(2).strip(), source="mcp")
        except Exception:
            pass
        return f"✅ Saved to memory (id={row_id}): {content[:100]}"
    except Exception as e:
        return f"Memory save failed: {type(e).__name__}: {e}"
