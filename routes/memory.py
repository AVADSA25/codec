"""CODEC Dashboard -- Memory routes (search, recent, sessions, rebuild)."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from codec_memory import CodecMemory as _CM, _sanitize_fts_query

router = APIRouter()

_memory = _CM()


@router.get("/api/memory/search")
async def memory_search(q: str = "", limit: int = 10):
    """Full-text search over all conversations (FTS5 BM25 ranked)."""
    limit = min(limit, 500)
    sanitized = _sanitize_fts_query(q)
    if not sanitized:
        return JSONResponse({"error": "Query required"}, status_code=400)
    return _memory.search(sanitized, limit=limit)


@router.get("/api/memory/recent")
async def memory_recent(days: int = 7, limit: int = 50):
    """Return messages from the past N days."""
    limit = min(limit, 500)
    return _memory.search_recent(days=days, limit=limit)


@router.get("/api/memory/sessions")
async def memory_sessions(limit: int = 20):
    """Return distinct sessions with message count and preview."""
    limit = min(limit, 500)
    return _memory.get_sessions(limit=limit)


@router.post("/api/memory/rebuild")
async def memory_rebuild():
    """Rebuild FTS index from scratch (use after bulk imports)."""
    n = _memory.rebuild_fts()
    return {"indexed": n}
