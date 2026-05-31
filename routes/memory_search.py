"""CODEC cross-source memory search API.

G1 / SR-57: extracted from codec_dashboard.py.

Searches ALL conversation history across four backends, dedupes by
content prefix, and returns the most-recent unique hits:

  1. Voice memory  — FTS5 via CodecMemory + LIKE fallback on
                     `conversations` table in memory.db
  2. Dashboard chat — qchat_messages (qchat.db, via routes.qchat.qchat_db)
  3. Vibe IDE      — vibe_messages (vibe.db, via routes.vibe.vibe_db)
  4. Flash sessions — sessions table (task+response combined)

Body: {"query": "term", "limit": 20, "sources": ["chat", "voice", "flash", "vibe", "all"]}
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from routes._shared import get_db

router = APIRouter()
log = logging.getLogger("codec_dashboard")


@router.post("/api/memory/search")
async def memory_search_endpoint(request: Request):
    """Search ALL conversation history across voice, chat, vibe, and flash sources.

    JSON body: {"query": "search term", "limit": 20, "sources": ["chat", "voice", "flash", "all"]}
    Returns list of: {timestamp, source, role, content, session_id}
    """
    body = await request.json()
    query = (body.get("query") or "").strip()
    if not query or len(query) < 2:
        return JSONResponse({"error": "query must be at least 2 characters"}, status_code=400)

    limit = min(int(body.get("limit", 20)), 100)
    sources = body.get("sources", ["all"])
    if isinstance(sources, str):
        sources = [sources]
    search_all = "all" in sources
    keyword = f"%{query}%"
    results = []

    # 1. Voice memory (FTS5 via CodecMemory + conversations table in memory.db)
    if search_all or "voice" in sources:
        # FTS5 search (ranked by relevance)
        try:
            from codec_memory import CodecMemory
            mem = CodecMemory()
            fts_results = mem.search(query, limit=limit)
            for r in fts_results:
                results.append({
                    "timestamp": r.get("timestamp", ""),
                    "source": "voice",
                    "role": r.get("role", ""),
                    "content": (r.get("content", "") or "")[:500],
                    "session_id": r.get("session_id", ""),
                })
        except Exception as e:
            log.warning(f"Memory search (voice FTS): {e}")

        # Also search conversations table (LIKE fallback for non-FTS matches)
        try:
            c = get_db()
            rows = c.execute(
                "SELECT session_id, timestamp, role, content FROM conversations "
                "WHERE content LIKE ? COLLATE NOCASE ORDER BY id DESC LIMIT ?",
                (keyword, limit)
            ).fetchall()
            for r in rows:
                results.append({
                    "timestamp": r[1] or "",
                    "source": "voice",
                    "role": r[2] or "",
                    "content": (r[3] or "")[:500],
                    "session_id": r[0] or "",
                })
        except Exception as e:
            log.warning(f"Memory search (conversations table): {e}")

    # 2. Dashboard chat (qchat.db)
    if search_all or "chat" in sources:
        try:
            from routes.qchat import qchat_db
            conn = qchat_db()
            rows = conn.execute(
                """SELECT m.session_id, m.timestamp, m.role, m.content, s.title
                   FROM qchat_messages m
                   LEFT JOIN qchat_sessions s ON m.session_id = s.id
                   WHERE m.content LIKE ? COLLATE NOCASE
                   ORDER BY m.id DESC LIMIT ?""",
                (keyword, limit)
            ).fetchall()
            for r in rows:
                results.append({
                    "timestamp": r[1] or "",
                    "source": "chat",
                    "role": r[2] or "",
                    "content": (r[3] or "")[:500],
                    "session_id": r[0] or "",
                })
        except Exception as e:
            log.warning(f"Memory search (qchat): {e}")

    # 3. Vibe IDE (vibe.db)
    if search_all or "vibe" in sources:
        try:
            from routes.vibe import vibe_db
            conn = vibe_db()
            rows = conn.execute(
                """SELECT m.session_id, m.timestamp, m.role, m.content
                   FROM vibe_messages m
                   WHERE m.content LIKE ? COLLATE NOCASE
                   ORDER BY m.id DESC LIMIT ?""",
                (keyword, limit)
            ).fetchall()
            for r in rows:
                results.append({
                    "timestamp": r[1] or "",
                    "source": "vibe",
                    "role": r[2] or "",
                    "content": (r[3] or "")[:500],
                    "session_id": r[0] or "",
                })
        except Exception as e:
            log.warning(f"Memory search (vibe): {e}")

    # 4. Flash / task sessions (sessions table in memory.db)
    if search_all or "flash" in sources:
        try:
            c = get_db()
            rows = c.execute(
                "SELECT id, timestamp, task, app, response FROM sessions "
                "WHERE task LIKE ? COLLATE NOCASE OR response LIKE ? COLLATE NOCASE "
                "ORDER BY id DESC LIMIT ?",
                (keyword, keyword, limit)
            ).fetchall()
            for r in rows:
                # Combine task + response for content
                task_text = r[2] or ""
                resp_text = r[4] or ""
                content = f"[TASK] {task_text}"
                if resp_text:
                    content += f"\n[RESPONSE] {resp_text[:300]}"
                results.append({
                    "timestamp": r[1] or "",
                    "source": "flash",
                    "role": "system",
                    "content": content[:500],
                    "session_id": str(r[0]) if r[0] else "",
                })
        except Exception as e:
            log.warning(f"Memory search (sessions/flash): {e}")

    # Deduplicate by content prefix and sort by timestamp descending
    seen = set()
    unique = []
    for r in sorted(results, key=lambda x: x.get("timestamp", ""), reverse=True):
        key = r["content"][:80]
        if key not in seen:
            seen.add(key)
            unique.append(r)
    unique = unique[:limit]

    log.info(f"Memory search '{query}': {len(unique)} results from {len(results)} raw hits")
    return {"query": query, "count": len(unique), "results": unique}
