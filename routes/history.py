"""CODEC history + conversations API routes.

F2 / SR-51: extracted from codec_dashboard.py. Two read-only endpoints
that browse the sessions / conversations tables for the dashboard UI.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from routes._shared import get_db

router = APIRouter()


@router.get("/api/history")
async def history(limit: int = 50):
    """Get recent task history."""
    limit = min(limit, 500)
    try:
        c = get_db()
        rows = c.execute(
            "SELECT id, timestamp, task, app, response FROM sessions ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [{"id": r[0], "timestamp": r[1], "task": r[2], "app": r[3], "response": r[4]} for r in rows]
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/conversations")
async def conversations(limit: int = 100, source: str = ""):
    """Get recent conversations. source=flash filters to Flash Chat only."""
    limit = min(limit, 500)
    try:
        c = get_db()
        if source == "flash":
            rows = c.execute(
                "SELECT id, session_id, timestamp, role, content FROM conversations WHERE session_id LIKE 'flash-%' ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT id, session_id, timestamp, role, content FROM conversations ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [{"id": r[0], "session_id": r[1], "timestamp": r[2], "role": r[3], "content": r[4]} for r in rows]
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
