"""CODEC qchat API routes — chat conversation storage.

D1 / SR-42: extracted from codec_dashboard.py. The qchat subsystem
stores Deep Chat conversation history in ~/.codec/qchat.db. The
endpoints handle session CRUD + a substring search across messages.

UI phase 2 P2.2 (docs/P2.2-DESIGN.md): chats can be renamed, pinned,
archived and exported; the list pages; a save can mark the discarded tail
of a chat (regenerate / edit) as superseded instead of leaving it to come
back on reload. The schema changes are additive and follow a one-time
backup copy of the database; no row or column is deleted.

P2.7 (docs/P2.7-DESIGN.md): thumbs up / down on a reply, with an optional
reason, in a qchat_feedback table (the reply text is not copied, only its
hash and length) plus a metadata-only chat_feedback audit event that the
shift report and the self-improvement run count.

DB setup (QCHAT_DB, _qchat_conn singleton, qchat_db helper) lives here
too — it was only ever referenced by these endpoints. WAL + busy_timeout
+ auto-migration applied on first connect.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

log = logging.getLogger("codec.qchat")

router = APIRouter()


# ── Chat conversation storage ─────────────────────────────────────────────
QCHAT_DB = os.path.expanduser("~/.codec/qchat.db")

_qchat_conn = None

# P2.2 columns: added once, after a backup copy of the database.
_P22_COLUMNS = (("qchat_sessions", "pinned", "INTEGER DEFAULT 0"),
                ("qchat_sessions", "archived", "INTEGER DEFAULT 0"),
                ("qchat_messages", "superseded_at", "TEXT"))


def _columns(conn, table: str) -> set:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _backup_once(conn, tag: str = "p2.2") -> None:
    """Copy the database (SQLite online backup) to qchat.db.bak-<tag>, owner-only,
    before an additive migration. An empty database needs no copy."""
    dest = QCHAT_DB + ".bak-" + tag
    if os.path.exists(dest):
        return
    rows = sum(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
               for t in ("qchat_sessions", "qchat_messages"))
    if not rows:
        return
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    copy = sqlite3.connect(dest)
    try:
        conn.backup(copy)
    finally:
        copy.close()
    os.chmod(dest, 0o600)
    log.info("qchat.db backed up to %s before the %s migration", dest, tag.upper())


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
        # P2.2: pinned / archived chats and superseded messages (additive, backup first).
        missing = [(t, c, d) for t, c, d in _P22_COLUMNS if c not in _columns(_qchat_conn, t)]
        if missing:
            _qchat_conn.commit()
            _backup_once(_qchat_conn)
            for t, c, d in missing:
                _qchat_conn.execute(f"ALTER TABLE {t} ADD COLUMN {c} {d}")
        _qchat_conn.execute("CREATE INDEX IF NOT EXISTS idx_qchat_messages_session ON qchat_messages(session_id, id)")
        # P2.7: reply feedback (a new table; additive, backup first).
        has_feedback = _qchat_conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='qchat_feedback'").fetchone()
        if not has_feedback:
            _qchat_conn.commit()
            _backup_once(_qchat_conn, "p2.7")
            _qchat_conn.execute('''CREATE TABLE IF NOT EXISTS qchat_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, msg_hash TEXT,
                rating INTEGER, reason TEXT, model TEXT, skill TEXT, chars INTEGER,
                created_at TEXT, updated_at TEXT, UNIQUE(session_id, msg_hash))''')
        _qchat_conn.commit()
    return _qchat_conn


@router.get("/api/qchat/sessions")
async def qchat_sessions(user_id: str = None, offset: int = 0, limit: int = 30, archived: int = 0):
    """Newest first, pinned chats on top; archived chats only with archived=1.
    offset / limit page the list (limit is capped at 100)."""
    conn = qchat_db()
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    where, args = ["COALESCE(archived, 0) = ?"], [1 if archived else 0]
    if user_id is not None:
        where.append("user_id = ?")
        args.append(user_id)
    rows = conn.execute(
        "SELECT id, title, updated_at, created_at, COALESCE(pinned, 0), COALESCE(archived, 0) "
        f"FROM qchat_sessions WHERE {' AND '.join(where)} "
        "ORDER BY COALESCE(pinned, 0) DESC, updated_at DESC LIMIT ? OFFSET ?",
        (*args, limit, offset)).fetchall()
    return [{"id": r[0], "title": r[1], "updated_at": r[2], "created_at": r[3],
             "pinned": bool(r[4]), "archived": bool(r[5])} for r in rows]


def _current_messages(conn, sid: str) -> list:
    return conn.execute("SELECT role, content, timestamp FROM qchat_messages "
                        "WHERE session_id=? AND superseded_at IS NULL ORDER BY id ASC", (sid,)).fetchall()


@router.get("/api/qchat/session/{sid}")
async def qchat_session(sid: str):
    rows = _current_messages(qchat_db(), sid)
    return [{"role": r[0], "content": r[1], "timestamp": r[2]} for r in rows]


@router.patch("/api/qchat/session/{sid}")
async def qchat_update(sid: str, request: Request):
    """Rename, pin or archive a chat: any of {"title", "pinned", "archived"}."""
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        return JSONResponse({"error": "JSON object expected"}, status_code=400)
    sets, args = [], []
    if "title" in body:
        title = str(body.get("title") or "").strip()[:60]
        if not title:
            return JSONResponse({"error": "The title cannot be empty"}, status_code=400)
        sets.append("title = ?")
        args.append(title)
    for key in ("pinned", "archived"):
        if key in body:
            sets.append(f"{key} = ?")
            args.append(1 if body.get(key) else 0)
    if not sets:
        return JSONResponse({"error": "Nothing to change"}, status_code=400)
    conn = qchat_db()
    if not conn.execute("SELECT 1 FROM qchat_sessions WHERE id=?", (sid,)).fetchone():
        return JSONResponse({"error": "Chat not found"}, status_code=404)
    conn.execute(f"UPDATE qchat_sessions SET {', '.join(sets)} WHERE id=?", (*args, sid))
    conn.commit()
    row = conn.execute("SELECT title, COALESCE(pinned, 0), COALESCE(archived, 0) FROM qchat_sessions WHERE id=?",
                       (sid,)).fetchone()
    return {"ok": True, "id": sid, "title": row[0], "pinned": bool(row[1]), "archived": bool(row[2])}


def _chat_for_export(sid: str):
    conn = qchat_db()
    s = conn.execute("SELECT title, created_at, updated_at FROM qchat_sessions WHERE id=?", (sid,)).fetchone()
    if not s:
        return None
    return {"id": sid, "title": s[0] or "Chat", "created_at": s[1], "updated_at": s[2],
            "messages": [{"role": r[0], "content": r[1], "timestamp": r[2]} for r in _current_messages(conn, sid)]}


def _export_markdown(chat: dict) -> str:
    def when(ts):
        try:
            return datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            return ""
    out = [f"# {chat['title']}", ""]
    if chat.get("created_at"):
        out += [f"_Started {when(chat['created_at'])}, exported from CODEC {datetime.now().strftime('%Y-%m-%d %H:%M')}._", ""]
    for m in chat["messages"]:
        who = "You" if m["role"] == "user" else "CODEC"
        stamp = when(m.get("timestamp"))
        out += [f"**{who}**" + (f" · {stamp}" if stamp else ""), "", (m.get("content") or "").rstrip(), ""]
    return "\n".join(out).rstrip() + "\n"


def _export_filename(title: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", title or "").strip("-").lower()[:50]
    return slug or "chat"


@router.get("/api/qchat/session/{sid}/export")
async def qchat_export(sid: str, format: str = "md"):
    """Download a chat (current messages only) as Markdown or JSON."""
    chat = _chat_for_export(sid)
    if chat is None:
        return JSONResponse({"error": "Chat not found"}, status_code=404)
    if format == "json":
        body, media, ext = json.dumps(chat, ensure_ascii=False, indent=2), "application/json", "json"
    elif format == "md":
        body, media, ext = _export_markdown(chat), "text/markdown; charset=utf-8", "md"
    else:
        return JSONResponse({"error": "format must be md or json"}, status_code=400)
    name = _export_filename(chat["title"])
    return Response(body, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{name}.{ext}"'})


@router.post("/api/qchat/session/{sid}/gdoc")
async def qchat_gdoc(sid: str):
    """Save a chat to a new Google Doc through the google_docs skill. Runs only
    on an explicit click; the doc lands in the owner's own Google Drive."""
    chat = _chat_for_export(sid)
    if chat is None:
        return JSONResponse({"error": "Chat not found"}, status_code=404)
    try:
        import codec_license
        if not codec_license.feature_allowed("skill_exec"):
            return JSONResponse({"error": "Skills need an active CODEC license"}, status_code=403)
    except Exception:
        pass
    import codec_dispatch
    mod = codec_dispatch.registry.load("google_docs")
    if mod is None or not hasattr(mod, "create_doc"):
        return JSONResponse({"error": "The Google Docs skill is not available"}, status_code=503)
    try:
        url = await asyncio.to_thread(mod.create_doc, chat["title"], _export_markdown(chat))
    except Exception as e:
        log.warning("qchat gdoc export failed: %s", e)
        return JSONResponse({"error": f"Google Docs: {e}"[:200]}, status_code=502)
    try:
        from codec_audit import log_event
        log_event("qchat_export_gdoc", "codec-dashboard", "chat saved to a Google Doc",
                  extra={"messages": len(chat["messages"])}, outcome="ok", level="info")
    except Exception:
        pass
    return {"ok": True, "url": url}


@router.post("/api/qchat/save")
async def qchat_save(request: Request):
    body = await request.json()
    sid = body.get("session_id", "")
    title = body.get("title", "New Chat")
    messages = body.get("messages") or []
    user_id = body.get("user_id", "default")
    discard_from = body.get("discard_from")
    now = datetime.now().isoformat()
    conn = qchat_db()
    # A renamed chat keeps its name, a pinned or archived one keeps its flags: the
    # upsert only sets the title when the chat is new.
    if conn.execute("SELECT 1 FROM qchat_sessions WHERE id=?", (sid,)).fetchone():
        conn.execute("UPDATE qchat_sessions SET updated_at=? WHERE id=?", (now, sid))
    else:
        conn.execute("INSERT INTO qchat_sessions (id, title, created_at, updated_at, user_id) VALUES (?, ?, ?, ?, ?)",
                     (sid, (title or "New Chat")[:60], now, now, user_id))
    # Replace-or-mark (P2.2): regenerate and edit drop the tail of the chat on the
    # page; mark those saved messages superseded instead of leaving them to come
    # back on reload. Nothing is deleted.
    if discard_from is not None:
        try:
            start = max(0, int(discard_from))
        except (TypeError, ValueError):
            return JSONResponse({"error": "discard_from must be a number"}, status_code=400)
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM qchat_messages WHERE session_id=? AND superseded_at IS NULL "
            "ORDER BY id LIMIT -1 OFFSET ?", (sid, start))]
        conn.executemany("UPDATE qchat_messages SET superseded_at=? WHERE id=?", [(now, i) for i in ids])
    for m in messages:
        conn.execute("INSERT INTO qchat_messages (session_id, role, content, timestamp, user_id) VALUES (?, ?, ?, ?, ?)",
            (sid, m.get("role", "user"), m.get("content", ""), now, user_id))
    conn.commit()
    rows = conn.execute("SELECT COUNT(*) FROM qchat_messages WHERE session_id=? AND superseded_at IS NULL",
                        (sid,)).fetchone()[0]
    return {"ok": True, "rows": rows}


# P2.7: thumbs up / down on a reply. The reasons are the three the page offers.
FEEDBACK_REASONS = ("wrong", "too_long", "no_data")
_RATINGS = {"up": 1, "down": -1, "none": 0}


@router.post("/api/qchat/feedback")
async def qchat_feedback(request: Request):
    """Rate one reply: {session_id, content, rating: up|down|none, reason, model, skill}.
    One row per reply (chat + SHA-256 of its text), updated on a change of mind and
    never deleted; the reply text itself is not stored."""
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        return JSONResponse({"error": "JSON object expected"}, status_code=400)
    sid = str(body.get("session_id") or "")
    content = str(body.get("content") or "")
    rating = _RATINGS.get(str(body.get("rating") or ""))
    if rating is None:
        return JSONResponse({"error": "rating must be up, down or none"}, status_code=400)
    if not sid or not content:
        return JSONResponse({"error": "session_id and content are required"}, status_code=400)
    reason = str(body.get("reason") or "")
    if reason and reason not in FEEDBACK_REASONS:
        return JSONResponse({"error": "unknown reason"}, status_code=400)
    if rating != -1:
        reason = ""
    model = str(body.get("model") or "")[:80]
    skill = str(body.get("skill") or "")[:64]
    conn = qchat_db()
    if not conn.execute("SELECT 1 FROM qchat_sessions WHERE id=?", (sid,)).fetchone():
        return JSONResponse({"error": "Chat not found"}, status_code=404)
    msg_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO qchat_feedback (session_id, msg_hash, rating, reason, model, skill, chars, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(session_id, msg_hash) DO UPDATE SET "
        "rating=excluded.rating, reason=excluded.reason, model=excluded.model, skill=excluded.skill, "
        "updated_at=excluded.updated_at",
        (sid, msg_hash, rating, reason, model, skill, len(content), now, now))
    conn.commit()
    try:
        from codec_audit import log_event
        log_event("chat_feedback", "codec-dashboard", "reply rated",
                  extra={"rating": str(body.get("rating")), "reason": reason, "model": model,
                         "reply": msg_hash[:16],
                         "skill": skill, "chars": len(content)},
                  outcome="ok", level="info")
    except Exception:
        pass
    return {"ok": True, "rating": str(body.get("rating")), "reason": reason}


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
           WHERE m.content LIKE ? AND m.superseded_at IS NULL
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
