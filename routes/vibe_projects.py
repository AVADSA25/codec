"""CODEC Vibe project routes: files, checkpoints, undo (docs/VIBE-PROJECTS-DESIGN.md).

Thin HTTP layer over codec_vibe_projects. The disk work runs off the event loop.
All routes sit behind the dashboard's normal login; nothing here runs code.
VIBE_PROJECTS_ENABLED=false answers 404 so the page falls back to single-file Vibe.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import codec_vibe_projects as vp

router = APIRouter()


async def _run(fn, *args, **kwargs):
    if not vp.enabled():
        return JSONResponse({"error": "Vibe projects are switched off"}, status_code=404)
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except vp.VibeError as e:
        return JSONResponse({"error": e.message}, status_code=e.status)


async def _json(request: Request):
    try:
        body = await request.json()
    except Exception:
        return None
    return body if isinstance(body, dict) else None


_BAD_BODY = JSONResponse({"error": "Send a JSON object"}, status_code=400)


@router.post("/api/vibe/projects")
async def create_project(request: Request):
    body = await _json(request)
    if body is None:
        return _BAD_BODY
    return await _run(vp.create_project, body.get("title", ""), body.get("files"))


@router.get("/api/vibe/projects")
async def list_projects(limit: int = 100):
    return await _run(vp.list_projects, limit)


@router.get("/api/vibe/projects/{pid}")
async def project_info(pid: str):
    return await _run(vp.project_info, pid)


@router.delete("/api/vibe/projects/{pid}")
async def delete_project(pid: str):
    return await _run(vp.trash_project, pid)


@router.get("/api/vibe/projects/{pid}/file")
async def read_file(pid: str, path: str = ""):
    return await _run(vp.read_file, pid, path)


@router.put("/api/vibe/projects/{pid}/file")
async def write_file(pid: str, request: Request):
    body = await _json(request)
    if body is None:
        return _BAD_BODY
    return await _run(vp.write_file, pid, body.get("path"), body.get("content"))


@router.post("/api/vibe/projects/{pid}/checkpoint")
async def checkpoint(pid: str, request: Request):
    body = await _json(request) or {}
    rec = await _run(vp.checkpoint_now, pid, str(body.get("label") or "Saved version")[:200])
    if isinstance(rec, JSONResponse):
        return rec
    return {"checkpoint": rec}


@router.post("/api/vibe/projects/{pid}/apply")
async def apply(pid: str, request: Request):
    body = await _json(request)
    if body is None:
        return _BAD_BODY
    return await _run(vp.apply_blocks, pid, body.get("blocks"), str(body.get("label") or "AI change")[:200])


@router.get("/api/vibe/projects/{pid}/checkpoints")
async def checkpoints(pid: str, limit: int = 100):
    return await _run(vp.list_checkpoints, pid, limit)


@router.get("/api/vibe/projects/{pid}/diff")
async def diff(pid: str, request: Request):
    q = request.query_params
    return await _run(vp.diff, pid, q.get("from", ""), q.get("to", "working"), q.get("path") or None)


@router.post("/api/vibe/projects/{pid}/restore")
async def restore(pid: str, request: Request):
    body = await _json(request)
    if body is None:
        return _BAD_BODY
    return await _run(vp.restore, pid, body.get("checkpoint"))


@router.post("/api/vibe/projects/{pid}/undo")
async def undo(pid: str):
    return await _run(vp.undo_last_ai, pid)
