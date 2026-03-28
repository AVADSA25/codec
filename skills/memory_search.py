"""CODEC Skill: Memory Search — search past conversations with full-text search"""
SKILL_NAME = "memory_search"
SKILL_TRIGGERS = [
    # search
    "search my memory", "search memory", "search conversations",
    "find in memory", "look in memory", "check memory",
    "what did i say about", "what did we talk about",
    "when did i mention", "find when i said",
    "do i have notes on", "recall", "remember when",
    # browse
    "show recent conversations", "show my recent chats",
    "what have we discussed", "conversation history",
    "show memory", "memory history", "past conversations",
]
SKILL_DESCRIPTION = "Search past CODEC conversations using full-text search"

import os, sys, json

_CODEC_REPO = os.path.expanduser("~/codec-dashboard")
if _CODEC_REPO not in sys.path:
    sys.path.insert(0, _CODEC_REPO)


def _get_memory():
    from codec_memory import CodecMemory
    return CodecMemory()


def run(task: str, app: str = "", ctx: str = "") -> str:
    low = task.lower()

    try:
        mem = _get_memory()

        # ── Recent conversations ──
        if any(w in low for w in ["recent", "history", "discussed", "past conversations", "recent chats"]):
            results = mem.search_recent(days=7, limit=20)
            if not results:
                return "No conversations found in the past 7 days."
            lines = [f"Recent conversations (last 7 days) — {len(results)} messages:"]
            seen = set()
            for r in results:
                sid = r["session_id"][:8]
                ts  = r["timestamp"][:16].replace("T", " ")
                role = r["role"].upper()
                snippet = r["content"][:120].replace("\n", " ")
                key = f"{r['timestamp'][:13]}_{r['role']}"
                if key not in seen:
                    seen.add(key)
                    lines.append(f"  [{ts}] {role}: {snippet}")
            return "\n".join(lines[:25])

        # ── Full-text search ──
        # Extract search query: everything after trigger keywords
        query = task
        for strip in [
            "search my memory for", "search memory for", "search conversations for",
            "find in memory", "look in memory", "check memory for",
            "what did i say about", "what did we talk about about",
            "what did we talk about", "when did i mention", "find when i said",
            "do i have notes on", "recall", "remember when",
            "search my memory", "search memory", "search conversations",
            "show memory", "memory history",
        ]:
            if strip in low:
                idx = low.index(strip) + len(strip)
                query = task[idx:].strip(" ?.,")
                break

        if not query or len(query.strip()) < 2:
            return "What should I search for in memory? Try: 'search memory for project X'"

        results = mem.search(query, limit=8)
        if not results:
            return f"Nothing found in memory for '{query}'."

        lines = [f"Memory search: '{query}' — {len(results)} result(s):"]
        for r in results:
            ts      = r["timestamp"][:16].replace("T", " ")
            role    = r["role"].upper()
            snippet = r["content"][:200].replace("\n", " ")
            lines.append(f"  [{ts}] {role}: {snippet}")

        return "\n".join(lines)

    except Exception as e:
        return f"Memory search error: {e}"
