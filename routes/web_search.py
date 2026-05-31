"""CODEC web_search API route — standalone search for the chat UI.

F6 / SR-55: extracted from codec_dashboard.py. One small endpoint
proxying to codec_search.search() with a formatted result block.
"""
from __future__ import annotations

import os
import sys

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.post("/api/web_search")
async def web_search_endpoint(request: Request):
    """Standalone web search endpoint for the chat UI."""
    body = await request.json()
    query = body.get("query", "").strip()
    if not query:
        return JSONResponse({"error": "query required"}, status_code=400)
    try:
        repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if repo_dir not in sys.path:
            sys.path.insert(0, repo_dir)
        from codec_search import search, format_results
        results = search(query, max_results=8)
        return {"results": results, "formatted": format_results(results, max_snippets=8)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
