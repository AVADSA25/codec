"""CODEC Skill: Memory Search — search ALL past conversations (voice + chat + vibe)"""
SKILL_NAME = "memory_search"
SKILL_TRIGGERS = [
    # search — "my" and "your" variations
    "search my memory", "search your memory", "search memory", "search conversations",
    "find in memory", "look in memory", "check memory", "check my memory", "check your memory",
    "what did i say about", "what did we talk about", "what did we discuss",
    "when did i mention", "find when i said",
    "do i have notes on", "recall", "remember when",
    "search history for", "search our conversations",
    # browse
    "show recent conversations", "show my recent chats",
    "what have we discussed", "conversation history",
    "show memory", "memory history", "past conversations",
    # natural phrasing
    "do you remember", "have we talked about", "have we discussed",
]
SKILL_DESCRIPTION = "Search ALL past CODEC conversations (voice, chat, vibe) using full-text search"
SKILL_MCP_EXPOSE = True

import os, sys, sqlite3, subprocess, tempfile

_CODEC_REPO = os.path.expanduser("~/codec-repo")
if _CODEC_REPO not in sys.path:
    sys.path.insert(0, _CODEC_REPO)

MEMORY_DB = os.path.expanduser("~/.codec/memory.db")
QCHAT_DB = os.path.expanduser("~/.codec/qchat.db")
VIBE_DB = os.path.expanduser("~/.codec/vibe.db")


def _search_all(query, limit=15):
    """Search across ALL CODEC databases — voice memory, chat, and vibe."""
    results = []

    # 1. Voice/terminal memory (FTS5 indexed)
    try:
        sys.path.insert(0, _CODEC_REPO)
        from codec_memory import CodecMemory
        mem = CodecMemory()
        fts_results = mem.search(query, limit=limit)
        for r in fts_results:
            results.append({
                "source": "VOICE",
                "timestamp": r.get("timestamp", ""),
                "role": r.get("role", ""),
                "content": r.get("content", ""),
            })
    except Exception:
        pass

    # 2. Dashboard chat (qchat.db)
    try:
        if os.path.exists(QCHAT_DB):
            conn = sqlite3.connect(QCHAT_DB)
            conn.execute("PRAGMA busy_timeout=3000")
            q = f"%{query}%"
            rows = conn.execute(
                "SELECT role, content, timestamp FROM qchat_messages "
                "WHERE content LIKE ? COLLATE NOCASE ORDER BY id DESC LIMIT ?",
                (q, limit)
            ).fetchall()
            conn.close()
            for r in rows:
                results.append({
                    "source": "CHAT",
                    "timestamp": r[2] or "",
                    "role": r[0] or "",
                    "content": r[1] or "",
                })
    except Exception:
        pass

    # 3. Vibe IDE messages (vibe.db)
    try:
        if os.path.exists(VIBE_DB):
            conn = sqlite3.connect(VIBE_DB)
            conn.execute("PRAGMA busy_timeout=3000")
            q = f"%{query}%"
            rows = conn.execute(
                "SELECT role, content, timestamp FROM vibe_messages "
                "WHERE content LIKE ? COLLATE NOCASE ORDER BY id DESC LIMIT ?",
                (q, limit)
            ).fetchall()
            conn.close()
            for r in rows:
                results.append({
                    "source": "VIBE",
                    "timestamp": r[2] or "",
                    "role": r[0] or "",
                    "content": r[1] or "",
                })
    except Exception:
        pass

    # Sort by timestamp descending, deduplicate
    seen = set()
    unique = []
    for r in sorted(results, key=lambda x: x.get("timestamp", ""), reverse=True):
        key = r["content"][:80]
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def _show_in_terminal(text):
    """Open a terminal window to display search results."""
    try:
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', prefix='codec_memory_',
                                          delete=False, dir='/tmp')
        tmp.write(text)
        tmp.close()
        subprocess.Popen([
            "osascript", "-e",
            f'tell application "Terminal" to do script "cat {tmp.name} && echo && echo \\"Press Enter to close\\" && read && rm {tmp.name}"'
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def run(task: str, app: str = "", ctx: str = "") -> str:
    low = task.lower()

    try:
        # ── Extract search query ──
        query = task
        for strip in [
            # Long phrases first (greedy match)
            "search your memory for", "search my memory for", "search memory for",
            "search our conversations for", "search conversations for", "search history for",
            "find in memory for", "find in memory", "look in memory for", "look in memory",
            "check memory for", "check my memory for", "check your memory for",
            "what did i say about", "what did we talk about about",
            "what did we talk about", "what did we discuss about", "what did we discuss",
            "have we talked about", "have we discussed",
            "when did i mention", "find when i said",
            "do i have notes on", "do you remember about", "do you remember",
            "recall about", "recall", "remember when",
            # Short phrases last (fallback)
            "search your memory", "search my memory", "search memory", "search conversations",
            "show memory", "memory history", "check memory", "check my memory",
            "check your memory",
            # Strip trailing filler
            "share with me", "tell me about", "show me",
        ]:
            if strip in low:
                idx = low.index(strip) + len(strip)
                query = task[idx:].strip(" ?.,")
                break

        # Secondary cleanup: strip conversational filler from extracted query
        _query_low = query.lower()
        for filler in [
            "share with me", "tell me about", "show me", "give me",
            "i want to know about", "i'd like to know about",
            "the latest", "the most recent", "the last",
            "can you find", "can you search", "do you know",
        ]:
            if _query_low.startswith(filler):
                query = query[len(filler):].strip(" ,.")
                _query_low = query.lower()

        if not query or len(query.strip()) < 2:
            return "What should I search for in memory? Try: 'search memory for project X'"

        results = _search_all(query, limit=20)
        if not results:
            return f"Nothing found in memory for '{query}'."

        # Format for voice (short summary)
        voice_summary = f"Found {len(results)} results for '{query}' across all CODEC history."

        # Format for terminal (full detail)
        lines = [
            f"{'='*60}",
            f"  CODEC MEMORY SEARCH: '{query}'",
            f"  {len(results)} result(s) across voice, chat, and vibe",
            f"{'='*60}",
            "",
        ]
        for r in results[:20]:
            ts = r["timestamp"][:16].replace("T", " ") if r["timestamp"] else "?"
            role = r["role"].upper()
            src = r["source"]
            content = r["content"][:300].replace("\n", "\n    ")
            lines.append(f"[{ts}] [{src}] {role}:")
            lines.append(f"    {content}")
            lines.append("")

        full_text = "\n".join(lines)

        # Show in terminal window
        _show_in_terminal(full_text)

        # Return voice-friendly summary + first 2 results
        top = results[:2]
        detail_lines = [voice_summary, "Opening full results in a terminal window.", ""]
        for r in top:
            role = r["role"].upper()
            snippet = r["content"][:150].replace("\n", " ")
            detail_lines.append(f"{role}: {snippet}")

        return "\n".join(detail_lines)

    except Exception as e:
        return f"Memory search error: {e}"
