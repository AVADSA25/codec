"""CODEC Notifications API routes.

B6-P3 / SR-34: extracted from codec_dashboard.py. The 4 notification
endpoints are well-isolated (no implicit module-level state beyond what
routes/_shared.py already exposes — _notif_lock, _load_notifications,
_write_notifications), so they make the cleanest first extraction
target after the agents / auth / skills route groups that already
moved.

Pattern for future route-group extractions:
  1. Move the @app.<verb> decorators to @router.<verb> in this file.
  2. Re-import any shared state (locks, helpers) from routes/_shared.
  3. Add `app.include_router(notifications_router)` in codec_dashboard.
  4. Tests that hit the URL still work via TestClient — no code change.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from routes._shared import (
    _notif_lock,
    _load_notifications,
    _write_notifications,
)

router = APIRouter()


@router.get("/api/notifications")
async def get_notifications(request: Request):
    """Return all notifications, newest first. Use ?unread=true to filter."""
    notifications = _load_notifications()
    unread_filter = request.query_params.get("unread", "").lower()
    if unread_filter == "true":
        notifications = [n for n in notifications if not n.get("read", False)]
    # Sort newest first by created timestamp
    notifications.sort(key=lambda n: n.get("created", ""), reverse=True)
    return {"notifications": notifications}


@router.get("/api/notifications/count")
async def get_notification_count():
    """Return unread notification count for badge display.
    Only counts completed notifications (success/error), not 'running' ones."""
    notifications = _load_notifications()
    unread = sum(1 for n in notifications
                 if not n.get("read", False)
                 and n.get("status", "success") != "running")
    return {"unread": unread}


@router.post("/api/notifications/{notif_id}/read")
async def mark_notification_read(notif_id: str):
    """Mark a single notification as read."""
    with _notif_lock:
        notifications = _load_notifications()
        for n in notifications:
            if n["id"] == notif_id:
                n["read"] = True
                _write_notifications(notifications)
                return {"status": "ok", "id": notif_id}
    return JSONResponse({"error": "Notification not found"}, status_code=404)


@router.post("/api/notifications/read-all")
async def mark_all_notifications_read():
    """Mark all notifications as read."""
    with _notif_lock:
        notifications = _load_notifications()
        count = 0
        for n in notifications:
            if not n.get("read", False):
                n["read"] = True
                count += 1
        _write_notifications(notifications)
    return {"status": "ok", "marked": count}
