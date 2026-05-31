"""CODEC CDP (Chrome DevTools Protocol) status API.

F7 / SR-56: extracted from codec_dashboard.py. Single read-only endpoint
that probes localhost:9222 (Chrome with --remote-debugging-port) and
returns the tab summary. Used by the Pilot status pill in the dashboard.
"""
from __future__ import annotations

import httpx as _httpx
from fastapi import APIRouter

router = APIRouter()


@router.get("/api/cdp/status")
async def cdp_status():
    """Check if Chrome is running with CDP enabled."""
    try:
        r = _httpx.get("http://localhost:9222/json", timeout=2)
        tabs = r.json()
        page_tabs = [t for t in tabs if t.get("type") == "page"]
        return {
            "connected": True,
            "total_tabs": len(tabs),
            "page_tabs": len(page_tabs),
            "tabs": [{"title": t.get("title", "")[:60], "url": t.get("url", "")[:80]}
                     for t in page_tabs[:5]]
        }
    except Exception:
        return {"connected": False, "total_tabs": 0, "page_tabs": 0, "tabs": []}
