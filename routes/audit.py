"""CODEC Audit API routes.

C4 / SR-39: extracted from codec_dashboard.py. Read-only audit log
endpoints — full ~/.codec/audit.log tail, filtered event stream, and
24h stats. All emit no audit events themselves (read-only).
"""
from __future__ import annotations

import os

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from routes._shared import AUDIT_LOG

router = APIRouter()


@router.get("/api/audit")
async def audit(limit: int = 50):
    """Get recent audit log entries."""
    limit = min(limit, 500)
    try:
        if not os.path.exists(AUDIT_LOG):
            return []
        with open(AUDIT_LOG) as f:
            lines = f.readlines()
        return [{"line": line.strip()} for line in lines[-limit:]][::-1]
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/audit/stream")
async def audit_stream(
    categories: str = "",
    level: str = "",
    search: str = "",
    since: str = "",
    until: str = "",
    limit: int = 200
):
    """Query audit events with filters."""
    from codec_audit import read_events
    cats = [c.strip() for c in categories.split(",") if c.strip()] or None
    events = read_events(
        categories=cats,
        level=level or None,
        search=search or None,
        since=since or None,
        until=until or None,
        limit=min(limit, 1000)
    )
    return {"events": events}


@router.get("/api/audit/stats")
async def audit_stats():
    """Get audit event statistics for the last 24 hours."""
    from codec_audit import get_stats
    return get_stats(hours=24)
