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


MAX_OFFSET = 20000  # Settings > Audit pages 200 at a time (P2.15); this bounds the scan


@router.get("/api/audit/stream")
async def audit_stream(
    categories: str = "",
    level: str = "",
    search: str = "",
    since: str = "",
    until: str = "",
    limit: int = 200,
    offset: int = 0,
):
    """Query audit events with filters, newest first. `offset` skips that many
    matches (P2.15 paging); `has_more` says whether older matches remain."""
    from codec_audit import read_events
    cats = [c.strip() for c in categories.split(",") if c.strip()] or None
    limit = max(1, min(limit, 1000))
    offset = max(0, min(offset, MAX_OFFSET))
    events = read_events(
        categories=cats,
        level=level or None,
        search=search or None,
        since=since or None,
        until=until or None,
        limit=offset + limit + 1,
    )
    page = events[offset:offset + limit]
    return {"events": page, "has_more": len(events) > offset + limit}


@router.get("/api/audit/verify")
async def audit_verify():
    """Check today's audit log against its HMAC signatures (read-only, P2.15).
    Counts only: the home folder in an error reads as ~."""
    from starlette.concurrency import run_in_threadpool

    from codec_audit import verify_audit_log
    r = await run_in_threadpool(verify_audit_log)
    out = {k: r.get(k) for k in ("total_lines", "signed_lines", "unsigned_lines", "broken_lines",
                                 "first_broken_line_no", "integrity_ok", "error")}
    if out["error"]:
        out["error"] = str(out["error"]).replace(os.path.expanduser("~"), "~")
    return out


@router.get("/api/audit/stats")
async def audit_stats():
    """Get audit event statistics for the last 24 hours."""
    from codec_audit import get_stats
    return get_stats(hours=24)
