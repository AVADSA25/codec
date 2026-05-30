"""CODEC Vibe Code API routes — IDE session storage.

D2 / SR-43: extracted from codec_dashboard.py. Vibe is CODEC's browser
IDE; these endpoints persist code editor sessions + AI chat history to
~/.codec/vibe.db. Same db lifecycle pattern as routes/qchat.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime

from fastapi import APIRouter, Request

router = APIRouter()


# ── Vibe Code session storage ─────────────────────────────────────────────
VIBE_DB = os.path.expanduser("~/.codec/vibe.db")

_vibe_conn = None


def vibe_db():
    """Lazy-initialised SQLite connection. Singleton per process."""
    global _vibe_conn
    if _vibe_conn is None:
        _vibe_conn = sqlite3.connect(VIBE_DB, check_same_thread=False)
        _vibe_conn.execute("PRAGMA journal_mode=WAL")
        _vibe_conn.execute("PRAGMA busy_timeout=5000")
        _vibe_conn.execute('''CREATE TABLE IF NOT EXISTS vibe_sessions (
            id TEXT PRIMARY KEY, title TEXT, language TEXT, code TEXT, created_at TEXT, updated_at TEXT,
            user_id TEXT DEFAULT 'default')''')
        _vibe_conn.execute('''CREATE TABLE IF NOT EXISTS vibe_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,
            content TEXT, timestamp TEXT, user_id TEXT DEFAULT 'default')''')
        # Migrate existing tables: add user_id if missing
        for table in ("vibe_sessions", "vibe_messages"):
            try:
                _vibe_conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id TEXT DEFAULT 'default'")
            except sqlite3.OperationalError:
                pass
        _vibe_conn.execute("CREATE INDEX IF NOT EXISTS idx_vibe_sessions_user ON vibe_sessions(user_id)")
        _vibe_conn.execute("CREATE INDEX IF NOT EXISTS idx_vibe_messages_user ON vibe_messages(user_id)")
        _vibe_conn.commit()
    return _vibe_conn


@router.get("/api/vibe/sessions")
async def vibe_sessions(user_id: str = None):
    conn = vibe_db()
    if user_id is not None:
        rows = conn.execute("SELECT id, title, language, updated_at FROM vibe_sessions WHERE user_id=? ORDER BY updated_at DESC LIMIT 30", (user_id,)).fetchall()
    else:
        rows = conn.execute("SELECT id, title, language, updated_at FROM vibe_sessions ORDER BY updated_at DESC LIMIT 30").fetchall()
    return [{"id": r[0], "title": r[1], "language": r[2], "updated_at": r[3]} for r in rows]


@router.get("/api/vibe/session/{sid}")
async def vibe_session(sid: str):
    conn = vibe_db()
    session = conn.execute("SELECT id, title, language, code FROM vibe_sessions WHERE id=?", (sid,)).fetchone()
    msgs = conn.execute("SELECT role, content, timestamp FROM vibe_messages WHERE session_id=? ORDER BY id ASC", (sid,)).fetchall()
    return {
        "session": {"id": session[0], "title": session[1], "language": session[2], "code": session[3]} if session else None,
        "messages": [{"role": r[0], "content": r[1], "timestamp": r[2]} for r in msgs]
    }


@router.post("/api/vibe/save")
async def vibe_save(request: Request):
    body = await request.json()
    sid = body.get("session_id", "")
    title = body.get("title", "Untitled")
    language = body.get("language", "python")
    code = body.get("code", "")
    messages = body.get("messages", [])
    user_id = body.get("user_id", "default")
    now = datetime.now().isoformat()
    full_sync = body.get("full_sync", False)
    conn = vibe_db()
    conn.execute("INSERT OR REPLACE INTO vibe_sessions (id, title, language, code, created_at, updated_at, user_id) VALUES (?, ?, ?, ?, COALESCE((SELECT created_at FROM vibe_sessions WHERE id=?), ?), ?, ?)",
        (sid, title[:60], language, code, sid, now, now, user_id))
    if full_sync and messages:
        conn.execute("DELETE FROM vibe_messages WHERE session_id=?", (sid,))
    for m in messages:
        conn.execute("INSERT INTO vibe_messages (session_id, role, content, timestamp, user_id) VALUES (?, ?, ?, ?, ?)",
            (sid, m.get("role", "user"), m.get("content", ""), now, user_id))
    conn.commit()
    return {"ok": True}


@router.delete("/api/vibe/session/{sid}")
async def vibe_delete(sid: str):
    conn = vibe_db()
    conn.execute("DELETE FROM vibe_messages WHERE session_id=?", (sid,))
    conn.execute("DELETE FROM vibe_sessions WHERE id=?", (sid,))
    conn.commit()
    return {"ok": True}
