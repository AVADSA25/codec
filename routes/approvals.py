"""CODEC Remote Command Approval API routes.

C1 / SR-36: extracted from codec_dashboard.py. The 4 approval endpoints
gate destructive commands that a session would otherwise auto-deny —
the PWA pops a banner asking the operator to allow or deny.

All state (`_pending_approvals`, `_approval_lock`, `_evict_expired_approvals`)
already lives in routes/_shared. Same extraction pattern as
routes/notifications.py.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from routes._shared import (
    _pending_approvals,
    _approval_lock,
    _evict_expired_approvals,
)

router = APIRouter()
log = logging.getLogger("codec_dashboard")


@router.get("/api/approvals")
async def list_pending_approvals():
    """List all pending command approvals."""
    with _approval_lock:
        # H-6: delete entries older than 120s (any status) so the dict can't grow
        # unbounded — replaces the old mark-expired-but-never-delete behavior.
        # After eviction every remaining entry is ≤120s, so a "pending" entry is
        # genuinely actionable (no per-entry time check needed).
        _evict_expired_approvals()
        pending = [{**a, "id": aid} for aid, a in _pending_approvals.items()
                   if a.get("status") == "pending"]
        return {"approvals": pending}


@router.get("/api/approvals/count")
async def pending_approval_count():
    """Badge count of pending approvals."""
    with _approval_lock:
        # H-6: sweep here too (this is the frequently-polled badge endpoint) so
        # the dict stays bounded regardless of which endpoint the PWA hits.
        _evict_expired_approvals()
        count = sum(1 for a in _pending_approvals.values()
                    if a.get("status") == "pending")
        return {"count": count}


@router.post("/api/approvals/{approval_id}/allow")
async def allow_approval(approval_id: str):
    """Approve a pending command from dashboard/phone."""
    with _approval_lock:
        a = _pending_approvals.get(approval_id)
        if not a:
            return JSONResponse({"error": "Approval not found"}, status_code=404)
        if a["status"] != "pending":
            return JSONResponse({"error": f"Approval already {a['status']}"}, status_code=409)
        a["status"] = "allowed"
        log.info(f"[APPROVAL] Remote ALLOW: {a['command'][:80]}")
        return {"status": "allowed", "command": a["command"][:120]}


@router.post("/api/approvals/{approval_id}/deny")
async def deny_approval(approval_id: str):
    """Deny a pending command from dashboard/phone."""
    with _approval_lock:
        a = _pending_approvals.get(approval_id)
        if not a:
            return JSONResponse({"error": "Approval not found"}, status_code=404)
        if a["status"] != "pending":
            return JSONResponse({"error": f"Approval already {a['status']}"}, status_code=409)
        a["status"] = "denied"
        log.info(f"[APPROVAL] Remote DENY: {a['command'][:80]}")
        return {"status": "denied"}
