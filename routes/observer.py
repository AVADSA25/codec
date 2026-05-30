"""CODEC Observer API routes.

C5 / SR-40: extracted from codec_dashboard.py. Single endpoint exposing
the live observer ring buffer for debugging. Auth-gated (by the
dashboard's existing /api/* middleware) AND debug-flag gated. Emits an
`observer_buffer_inspected` audit event so privileged reads stay visible
in the audit log. NOT linked from the main UI — operator-only.

Return shape redacts the raw entries (which contain window titles, OCR
text, clipboard content) and exposes only metadata + a rendered summary.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/api/observer/buffer")
async def observer_buffer(request: Request, debug: int = 0):
    """Return the current ring buffer state. Q5.6 design: debug-only,
    auth-gated, audit-emitting."""
    if int(debug) != 1:
        return {"error": "set ?debug=1 to read live observer buffer"}
    try:
        from codec_observer import get_global_buffer
        from codec_audit import OBSERVER_BUFFER_INSPECTED, log_event as _le
        buf = get_global_buffer()
        snap = buf.snapshot()
        try:
            client_ip = request.client.host if request.client else "unknown"
        except Exception:
            client_ip = "unknown"
        try:
            _le(
                OBSERVER_BUFFER_INSPECTED, "codec-dashboard",
                "observer buffer inspected via /api/observer/buffer",
                extra={
                    "client_ip": client_ip,
                    "buffer_entries_returned": len(snap),
                },
                outcome="ok", level="info",
            )
        except Exception:
            pass
        # Return only the metadata + a redacted summary, NOT the raw entries
        # (raw entries contain titles + OCR text + clipboard content).
        return {
            "buffer_depth": len(snap),
            "summary": buf.render_summary(),
            "oldest_ts": snap[0].get("ts") if snap else None,
            "newest_ts": snap[-1].get("ts") if snap else None,
        }
    except Exception as e:
        return {"error": f"observer not available: {e}"}
