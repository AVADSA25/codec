"""CODEC Vibe Code API routes — IDE session storage.

D2 / SR-43: extracted from codec_dashboard.py. Vibe is CODEC's browser
IDE; these endpoints persist code editor sessions + AI chat history to
~/.codec/vibe.db. Same db lifecycle pattern as routes/qchat.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

import codec_vibe_projects as vp

router = APIRouter()
log = logging.getLogger("codec.vibe")


# ── Vibe Code session storage ─────────────────────────────────────────────
VIBE_DB = os.path.expanduser("~/.codec/vibe.db")

_vibe_conn = None

# P-077 (docs/VIBE-V4-DESIGN.md): the checkpoint a row produced, and the rewind flag (1 = hidden, never deleted).
_V4_COLUMNS = (("checkpoint_id", "TEXT"), ("archived", "INTEGER NOT NULL DEFAULT 0"))
_CID_RE = re.compile(r"^c\d{4,}$")


def _copy_before_alter(conn: sqlite3.Connection) -> str:
    """vibe.db copied beside itself (0600) with SQLite's backup API, before the first ALTER that adds _V4_COLUMNS."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for n in range(1, 100):
        dest = f"{VIBE_DB}.pre-v4-{stamp}{'' if n == 1 else f'-{n}'}.bak"
        try:
            os.close(os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
            break
        except FileExistsError:
            continue
    else:
        raise OSError("no free name for the vibe.db copy")
    out = sqlite3.connect(dest)
    try:
        conn.backup(out)
    finally:
        out.close()
    os.chmod(dest, 0o600)
    return dest


def vibe_db():
    """Lazy-initialised SQLite connection. Singleton per process."""
    global _vibe_conn
    if _vibe_conn is None:
        conn = sqlite3.connect(VIBE_DB, check_same_thread=False)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute('''CREATE TABLE IF NOT EXISTS vibe_sessions (
                id TEXT PRIMARY KEY, title TEXT, language TEXT, code TEXT, created_at TEXT, updated_at TEXT,
                user_id TEXT DEFAULT 'default')''')
            conn.execute('''CREATE TABLE IF NOT EXISTS vibe_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,
                content TEXT, timestamp TEXT, user_id TEXT DEFAULT 'default',
                checkpoint_id TEXT, archived INTEGER NOT NULL DEFAULT 0)''')
            # Migrate existing tables: add user_id if missing
            for table in ("vibe_sessions", "vibe_messages"):
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id TEXT DEFAULT 'default'")
                except sqlite3.OperationalError:
                    pass
            # A session may belong to a project folder (docs/VIBE-PROJECTS-DESIGN.md); NULL = single-file session.
            try:
                conn.execute("ALTER TABLE vibe_sessions ADD COLUMN project_id TEXT")
            except sqlite3.OperationalError:
                pass
            # P-077: an older table gets the two columns, after a copy of vibe.db when it holds anything. Once the
            # columns are there nothing is copied again; a failed copy alters nothing (the error reaches the route).
            have = {r[1] for r in conn.execute("PRAGMA table_info(vibe_messages)")}
            missing = [(name, kind) for name, kind in _V4_COLUMNS if name not in have]
            if missing:
                conn.commit()
                if conn.execute("SELECT EXISTS(SELECT 1 FROM vibe_messages) OR EXISTS(SELECT 1 FROM vibe_sessions)").fetchone()[0]:
                    _copy_before_alter(conn)
                for name, kind in missing:
                    conn.execute(f"ALTER TABLE vibe_messages ADD COLUMN {name} {kind}")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_vibe_sessions_user ON vibe_sessions(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_vibe_messages_user ON vibe_messages(user_id)")
            conn.commit()
        except Exception:
            conn.close()
            raise
        _vibe_conn = conn
    return _vibe_conn


@router.get("/api/vibe/sessions")
async def vibe_sessions(user_id: str = None):
    conn = vibe_db()
    _maybe_sweep()
    if user_id is not None:
        rows = conn.execute("SELECT id, title, language, updated_at, project_id FROM vibe_sessions WHERE user_id=? ORDER BY updated_at DESC LIMIT 30", (user_id,)).fetchall()
    else:
        rows = conn.execute("SELECT id, title, language, updated_at, project_id FROM vibe_sessions ORDER BY updated_at DESC LIMIT 30").fetchall()
    return [{"id": r[0], "title": r[1], "language": r[2], "updated_at": r[3], "project_id": r[4]} for r in rows]


@router.get("/api/vibe/session/{sid}")
async def vibe_session(sid: str):
    conn = vibe_db()
    session = conn.execute("SELECT id, title, language, code, project_id FROM vibe_sessions WHERE id=?", (sid,)).fetchone()
    # Rows hidden by a rewind (archived) are not shown; they stay in the table (P-077).
    msgs = conn.execute("SELECT role, content, timestamp, checkpoint_id FROM vibe_messages WHERE session_id=? AND archived=0 "
                        "ORDER BY id ASC", (sid,)).fetchall()
    hidden = conn.execute("SELECT COUNT(*) FROM vibe_messages WHERE session_id=? AND archived<>0", (sid,)).fetchone()[0]
    rewind = None
    if session and session[4] and vp.enabled():
        st = await asyncio.to_thread(vp.rewind_state, session[4], sid)
        if _undo_blocked(conn, sid, st) is None:
            rewind = {"checkpoint": st["checkpoint"], "hidden": len(st["rows"])}
    return {
        "session": {"id": session[0], "title": session[1], "language": session[2], "code": session[3],
                    "project_id": session[4]} if session else None,
        "messages": [{"role": r[0], "content": r[1], "timestamp": r[2], "checkpoint_id": r[3]} for r in msgs],
        "hidden": hidden, "rewind": rewind,
    }


@router.post("/api/vibe/save")
async def vibe_save(request: Request):
    body = await request.json()
    if body.get("temporary") is True:       # a private chat (P-077): nothing of it is written
        return {"ok": True, "saved": False}
    sid = body.get("session_id", "")
    title = body.get("title", "Untitled")
    language = body.get("language", "python")
    code = body.get("code", "")
    messages = body.get("messages", [])
    user_id = body.get("user_id", "default")
    now = datetime.now().isoformat()
    full_sync = body.get("full_sync", False)
    # The project this session works in. A page that does not send the field (an older
    # page, a single-file session) keeps whatever the session already had.
    project_id = body.get("project_id")
    if not (isinstance(project_id, str) and re.match(r"^[a-z0-9][a-z0-9-]{2,60}$", project_id)):
        project_id = None
    conn = vibe_db()
    conn.execute("INSERT OR REPLACE INTO vibe_sessions (id, title, language, code, created_at, updated_at, user_id, project_id) VALUES (?, ?, ?, ?, COALESCE((SELECT created_at FROM vibe_sessions WHERE id=?), ?), ?, ?, COALESCE(?, (SELECT project_id FROM vibe_sessions WHERE id=?)))",
        (sid, title[:60], language, code, sid, now, now, user_id, project_id, sid))
    rows = [(m.get("role", "user"), m.get("content", ""), _cid(m.get("checkpoint_id"))) for m in messages]
    if full_sync and messages:
        # The rows the page sent unchanged keep their ids; only the rest of the visible rows is replaced. Rows a
        # rewind hid (archived) are never touched by a save, so Undo rewind finds them where they were (P-077).
        old = conn.execute("SELECT id, role, content, checkpoint_id FROM vibe_messages WHERE session_id=? AND archived=0 "
                           "ORDER BY id ASC", (sid,)).fetchall()
        k = 0
        while k < len(old) and k < len(rows) and tuple(old[k][1:]) == rows[k]:
            k += 1
        conn.executemany("DELETE FROM vibe_messages WHERE id=?", [(r[0],) for r in old[k:]])
        rows = rows[k:]
    for role, content, cid in rows:
        conn.execute("INSERT INTO vibe_messages (session_id, role, content, timestamp, user_id, checkpoint_id) VALUES (?, ?, ?, ?, ?, ?)",
            (sid, role, content, now, user_id, cid))
    conn.commit()
    return {"ok": True}


@router.delete("/api/vibe/session/{sid}")
async def vibe_delete(sid: str):
    """Purge: the session and every row (hidden ones too) go to a trash JSON file first, then the rows go (P-077)."""
    conn = vibe_db()
    held = _held_project(conn, sid)
    if held:
        return held
    try:
        _trash_session(conn, sid, "owner")
    except OSError:
        return JSONResponse({"error": "Could not move the chat to the trash folder; nothing was deleted"}, status_code=500)
    return {"ok": True}


# ── rewind, rename, export, purge and retention (docs/VIBE-V4-DESIGN.md, P-077) ─────────────────

RETENTION_EVERY_SECONDS = 6 * 3600
_last_sweep = 0.0


def _cid(value):
    return value if isinstance(value, str) and _CID_RE.match(value) else None


def _audit(event: str, message: str, extra: dict) -> None:
    """Counts only. Never a title, a message or a session's text."""
    try:
        from codec_audit import log_event
        log_event(event, "codec-vibe", message, extra=extra)
    except Exception:
        pass


def _cfg() -> dict:
    try:
        import codec_config
        with open(codec_config.CONFIG_PATH) as f:
            c = json.load(f)
        return c if isinstance(c, dict) else {}
    except Exception:
        return {}


async def _body(request: Request):
    try:
        body = await request.json()
    except Exception:
        return None
    return body if isinstance(body, dict) else None


def _err(e: vp.VibeError) -> JSONResponse:
    return JSONResponse({"error": e.message}, status_code=e.status)


def _projects_off() -> JSONResponse:
    """VIBE_PROJECTS_ENABLED=false: the same 404 every project route gives (routes/vibe_projects.py)."""
    return JSONResponse({"error": "Vibe projects are switched off"}, status_code=404)


def _project_of(conn, sid: str):
    row = conn.execute("SELECT project_id FROM vibe_sessions WHERE id=?", (sid,)).fetchone()
    return row[0] if row else None


def _held_project(conn, sid: str):
    """A 409 while an agent run holds this session's project, else None."""
    pid = _project_of(conn, sid)
    if pid and vp.agent_run_active(pid):
        return JSONResponse({"error": "The agent is working on this project. Stop it first."}, status_code=409)
    return None


def _undo_blocked(conn, sid: str, st):
    """Why Undo rewind cannot take back the rewind st (vp.rewind_state), or None when it can: the rewind must be the
    project's newest checkpoint and no message may have been added after the rows it hid."""
    if not st:
        return "This chat has no rewind to undo"
    if not st.get("newest"):
        return "The work went on after the rewind. Every version stays in Versions."
    if st["rows"]:
        newest = conn.execute("SELECT MAX(id) FROM vibe_messages WHERE session_id=? AND archived=0", (sid,)).fetchone()[0]
        if newest is not None and newest > min(st["rows"]):
            return "The chat went on after the rewind. The hidden messages stay saved."
    return None


def _trash_session(conn, sid: str, reason: str) -> int:
    """Write the session and all its rows to the trash folder, then delete them. Returns the rows moved."""
    session = conn.execute("SELECT id, title, language, code, created_at, updated_at, user_id, project_id "
                           "FROM vibe_sessions WHERE id=?", (sid,)).fetchone()
    msgs = conn.execute("SELECT id, role, content, timestamp, user_id, checkpoint_id, archived FROM vibe_messages "
                        "WHERE session_id=? ORDER BY id ASC", (sid,)).fetchall()
    if session is None and not msgs:
        return 0
    folder = vp.trash_root() / "sessions"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = re.sub(r"[^A-Za-z0-9_-]", "_", str(sid))[:64] or "session"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for n in range(1, 1000):
        path = folder / f"{name}-{stamp}{'' if n == 1 else f'-{n}'}.json"
        if not path.exists():
            break
    keys = ("id", "title", "language", "code", "created_at", "updated_at", "user_id", "project_id")
    data = {"trashed_at": datetime.now().isoformat(timespec="seconds"), "reason": reason,
            "session": dict(zip(keys, session)) if session else {"id": sid},
            "messages": [dict(zip(("id", "role", "content", "timestamp", "user_id", "checkpoint_id", "archived"), m))
                         for m in msgs]}
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    conn.execute("DELETE FROM vibe_messages WHERE session_id=?", (sid,))     # only once the file is on disk
    conn.execute("DELETE FROM vibe_sessions WHERE id=?", (sid,))
    conn.commit()
    _audit("vibe_session_trash", "Vibe chat moved to the trash", {"messages": len(msgs), "reason": reason})
    return len(msgs)


def retention_days(cfg: dict | None = None) -> int:
    """config.json vibe.retention_days: purge sessions idle longer. 0 (the default) keeps everything."""
    c = cfg if cfg is not None else _cfg()
    v = c.get("vibe") if isinstance(c, dict) else None
    d = v.get("retention_days") if isinstance(v, dict) else 0
    return d if isinstance(d, int) and not isinstance(d, bool) and 0 < d <= 36500 else 0


def retention_sweep(now: datetime | None = None, cfg: dict | None = None) -> dict:
    """Purge (to the trash first) every session not updated for retention_days. Off, it does nothing at all."""
    days = retention_days(cfg)
    if not days:
        return {"ran": False, "days": 0, "sessions": 0, "messages": 0, "skipped": 0}
    conn = vibe_db()
    cutoff = (now or datetime.now()) - timedelta(days=days)
    sessions = messages = skipped = 0
    for sid, updated, pid in conn.execute("SELECT id, updated_at, project_id FROM vibe_sessions").fetchall():
        try:
            when = datetime.fromisoformat(str(updated))
        except ValueError:
            skipped += 1
            continue
        if when.tzinfo is not None:
            when = when.astimezone().replace(tzinfo=None)
        if when >= cutoff:
            continue
        if pid and vp.agent_run_active(pid):
            skipped += 1
            continue
        try:
            messages += _trash_session(conn, sid, "retention")
            sessions += 1
        except OSError:
            skipped += 1
    out = {"ran": True, "days": days, "sessions": sessions, "messages": messages, "skipped": skipped}
    _audit("vibe_retention_sweep", "Vibe chat retention sweep", {k: out[k] for k in ("days", "sessions", "messages", "skipped")})
    return out


def _maybe_sweep() -> None:
    """The sweep runs when the page lists its chats, at most once every RETENTION_EVERY_SECONDS per process."""
    global _last_sweep
    if _last_sweep and time.monotonic() - _last_sweep < RETENTION_EVERY_SECONDS:
        return
    _last_sweep = time.monotonic()
    try:
        retention_sweep()
    except Exception:
        log.warning("vibe retention sweep failed", exc_info=True)


@router.post("/api/vibe/session/{sid}/rewind")
async def vibe_rewind(sid: str, request: Request):
    """Back to the visible row at `index`: its checkpoint is restored, the rows after it are hidden (archived=1)."""
    body = await _body(request)
    if body is None:
        return JSONResponse({"error": "Send a JSON object"}, status_code=400)
    index, cid = body.get("index"), body.get("checkpoint")
    if not isinstance(index, int) or isinstance(index, bool) or index < 0 or _cid(cid) is None:
        return JSONResponse({"error": "Send the message index and its checkpoint"}, status_code=400)
    if not vp.enabled():       # it writes project files: the projects kill switch applies, as on every project route
        return _projects_off()
    conn = vibe_db()
    pid = _project_of(conn, sid)
    if not pid:
        return JSONResponse({"error": "Rewind works in a chat that has a project"}, status_code=400)
    rows = conn.execute("SELECT id, checkpoint_id FROM vibe_messages WHERE session_id=? AND archived=0 ORDER BY id ASC",
                        (sid,)).fetchall()
    if index >= len(rows) or rows[index][1] != cid:
        return JSONResponse({"error": "The chat changed since the page showed it. Reload it and try again."}, status_code=409)
    hide = [r[0] for r in rows[index + 1:]]
    if len(hide) > vp.MAX_REWIND_ROWS:      # the rewind record keeps at most this many ids, and Undo needs all of them
        return JSONResponse({"error": f"A rewind can hide at most {vp.MAX_REWIND_ROWS} later messages"}, status_code=409)
    try:
        out = await asyncio.to_thread(vp.rewind, pid, cid, sid, hide)
    except vp.VibeError as e:
        return _err(e)
    conn.executemany("UPDATE vibe_messages SET archived=1 WHERE id=? AND session_id=?", [(i, sid) for i in hide])
    conn.commit()
    return {**out, "hidden": len(hide)}


@router.post("/api/vibe/session/{sid}/rewind/undo")
async def vibe_rewind_undo(sid: str, request: Request):
    body = await _body(request)
    if body is None:
        return JSONResponse({"error": "Send a JSON object"}, status_code=400)
    if not vp.enabled():
        return _projects_off()
    conn = vibe_db()
    pid = _project_of(conn, sid)
    if not pid:
        return JSONResponse({"error": "This chat has no project"}, status_code=400)
    try:
        st = await asyncio.to_thread(vp.rewind_state, pid, sid)
    except vp.VibeError as e:
        return _err(e)
    if st and st["checkpoint"] != body.get("checkpoint"):
        return JSONResponse({"error": "Only the newest rewind of this chat can be undone"}, status_code=409)
    why = _undo_blocked(conn, sid, st)
    if why:
        return JSONResponse({"error": why}, status_code=409)
    try:
        out = await asyncio.to_thread(vp.undo_rewind, pid, st["checkpoint"], sid)
    except vp.VibeError as e:
        return _err(e)
    conn.executemany("UPDATE vibe_messages SET archived=0 WHERE id=? AND session_id=? AND archived<>0",
                     [(i, sid) for i in out["rows"]])
    conn.commit()
    return {**out, "shown": len(out["rows"])}


@router.post("/api/vibe/session/{sid}/rename")
async def vibe_rename(sid: str, request: Request):
    """The chat's title and, in a project, the project's shown name (its id and folder stay)."""
    body = await _body(request)
    if body is None:
        return JSONResponse({"error": "Send a JSON object"}, status_code=400)
    title = re.sub(r"\s+", " ", body["title"].strip()) if isinstance(body.get("title"), str) else ""
    if not title:
        return JSONResponse({"error": "Write the new name first"}, status_code=400)
    conn = vibe_db()
    if conn.execute("SELECT 1 FROM vibe_sessions WHERE id=?", (sid,)).fetchone() is None:
        return JSONResponse({"error": "No such chat"}, status_code=404)
    pid = _project_of(conn, sid)
    if pid and vp.enabled():        # projects switched off: the chat's own title only, project.json is not written
        try:
            title = (await asyncio.to_thread(vp.rename_project, pid, title))["title"]
        except vp.VibeError as e:
            return _err(e)
        conn.execute("UPDATE vibe_sessions SET title=? WHERE project_id=?", (title[:60], pid))
    else:
        conn.execute("UPDATE vibe_sessions SET title=? WHERE id=?", (title[:60], sid))
    conn.commit()
    return {"ok": True, "title": title}


def conversation_markdown(title: str, project_id, rows, hidden: int, when: datetime) -> str:
    out = [f"# {title or 'Vibe chat'}", "",
           f"Exported from CODEC Vibe on {when.strftime('%Y-%m-%d %H:%M')}." + (f" Project: `{project_id}`." if project_id else "")]
    if hidden:
        out.append(f"{hidden} later {'message was' if hidden == 1 else 'messages were'} hidden by a rewind and "
                   "are not included.")
    for role, content in rows:
        out += ["", "## You" if role == "user" else "## CODEC", "", str(content or "").rstrip()]
    return "\n".join(out).rstrip() + "\n"


@router.get("/api/vibe/session/{sid}/export")
async def vibe_export(sid: str):
    """The visible conversation as a Markdown file to download."""
    conn = vibe_db()
    session = conn.execute("SELECT title, project_id FROM vibe_sessions WHERE id=?", (sid,)).fetchone()
    if session is None:
        return JSONResponse({"error": "No such chat"}, status_code=404)
    rows = conn.execute("SELECT role, content FROM vibe_messages WHERE session_id=? AND archived=0 ORDER BY id ASC",
                        (sid,)).fetchall()
    hidden = conn.execute("SELECT COUNT(*) FROM vibe_messages WHERE session_id=? AND archived<>0", (sid,)).fetchone()[0]
    now = datetime.now()
    name = re.sub(r"[^a-z0-9]+", "-", str(session[0] or "").lower()).strip("-")[:40] or "vibe-chat"
    return Response(conversation_markdown(session[0], session[1], rows, hidden, now), media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}-{now.strftime("%Y%m%d")}.md"'})
