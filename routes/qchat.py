"""CODEC qchat API routes — chat conversation storage.

D1 / SR-42: extracted from codec_dashboard.py. The qchat subsystem
stores Deep Chat conversation history in ~/.codec/qchat.db. The
endpoints handle session CRUD + a substring search across messages.

DB setup (QCHAT_DB, _qchat_conn singleton, qchat_db helper) lives here
too — it was only ever referenced by these endpoints. WAL + busy_timeout
+ auto-migration applied on first connect.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime

from fastapi import APIRouter, Request

router = APIRouter()


# ── Chat conversation storage ─────────────────────────────────────────────
QCHAT_DB = os.path.expanduser("~/.codec/qchat.db")

_qchat_conn = None


def qchat_db():
    """Lazy-initialised SQLite connection with WAL + busy_timeout +
    auto-migration of the user_id column. Singleton per process."""
    global _qchat_conn
    if _qchat_conn is None:
        _qchat_conn = sqlite3.connect(QCHAT_DB, check_same_thread=False)
        _qchat_conn.execute("PRAGMA journal_mode=WAL")
        _qchat_conn.execute("PRAGMA busy_timeout=5000")
        _qchat_conn.execute('''CREATE TABLE IF NOT EXISTS qchat_sessions (
            id TEXT PRIMARY KEY, title TEXT, created_at TEXT, updated_at TEXT,
            user_id TEXT DEFAULT 'default')''')
        _qchat_conn.execute('''CREATE TABLE IF NOT EXISTS qchat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,
            content TEXT, timestamp TEXT, user_id TEXT DEFAULT 'default')''')
        # Migrate existing tables: add user_id if missing
        for table in ("qchat_sessions", "qchat_messages"):
            try:
                _qchat_conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id TEXT DEFAULT 'default'")
            except sqlite3.OperationalError:
                pass
        _qchat_conn.execute("CREATE INDEX IF NOT EXISTS idx_qchat_sessions_user ON qchat_sessions(user_id)")
        _qchat_conn.execute("CREATE INDEX IF NOT EXISTS idx_qchat_messages_user ON qchat_messages(user_id)")
        _qchat_conn.commit()
    return _qchat_conn


@router.get("/api/qchat/sessions")
async def qchat_sessions(user_id: str = None):
    conn = qchat_db()
    if user_id is not None:
        rows = conn.execute("SELECT id, title, updated_at FROM qchat_sessions WHERE user_id=? ORDER BY updated_at DESC LIMIT 30", (user_id,)).fetchall()
    else:
        rows = conn.execute("SELECT id, title, updated_at FROM qchat_sessions ORDER BY updated_at DESC LIMIT 30").fetchall()
    return [{"id": r[0], "title": r[1], "updated_at": r[2]} for r in rows]


@router.get("/api/qchat/session/{sid}")
async def qchat_session(sid: str):
    conn = qchat_db()
    rows = conn.execute("SELECT role, content, timestamp FROM qchat_messages WHERE session_id=? ORDER BY id ASC", (sid,)).fetchall()
    return [{"role": r[0], "content": r[1], "timestamp": r[2]} for r in rows]


@router.post("/api/qchat/save")
async def qchat_save(request: Request):
    body = await request.json()
    sid = body.get("session_id", "")
    title = body.get("title", "New Chat")
    messages = body.get("messages", [])
    user_id = body.get("user_id", "default")
    now = datetime.now().isoformat()
    conn = qchat_db()
    conn.execute("INSERT OR REPLACE INTO qchat_sessions (id, title, created_at, updated_at, user_id) VALUES (?, ?, COALESCE((SELECT created_at FROM qchat_sessions WHERE id=?), ?), ?, ?)",
        (sid, title[:60], sid, now, now, user_id))
    for m in messages:
        conn.execute("INSERT INTO qchat_messages (session_id, role, content, timestamp, user_id) VALUES (?, ?, ?, ?, ?)",
            (sid, m.get("role", "user"), m.get("content", ""), now, user_id))
    conn.commit()
    return {"ok": True}


@router.delete("/api/qchat/session/{sid}")
async def qchat_delete(sid: str):
    conn = qchat_db()
    conn.execute("DELETE FROM qchat_messages WHERE session_id=?", (sid,))
    conn.execute("DELETE FROM qchat_sessions WHERE id=?", (sid,))
    conn.commit()
    return {"ok": True}


@router.get("/api/qchat/search")
async def qchat_search(q: str = "", limit: int = 20):
    """Search chat history by keyword across all sessions."""
    if not q or len(q.strip()) < 2:
        return []
    conn = qchat_db()
    keyword = f"%{q.strip()}%"
    rows = conn.execute(
        """SELECT m.session_id, s.title, m.content, m.role, m.timestamp
           FROM qchat_messages m
           LEFT JOIN qchat_sessions s ON m.session_id = s.id
           WHERE m.content LIKE ?
           ORDER BY m.timestamp DESC LIMIT ?""",
        (keyword, min(limit, 50))
    ).fetchall()
    results = []
    seen_sessions = set()
    for r in rows:
        sid = r[0]
        if sid not in seen_sessions:
            seen_sessions.add(sid)
            # Snippet: find keyword position and extract surrounding text
            content = r[2] or ""
            idx = content.lower().find(q.strip().lower())
            start = max(0, idx - 40)
            snippet = ("..." if start > 0 else "") + content[start:start + 120] + ("..." if len(content) > start + 120 else "")
            results.append({
                "session_id": sid,
                "title": r[1] or "Untitled",
                "snippet": snippet,
                "role": r[3],
                "timestamp": r[4]
            })
    return results
