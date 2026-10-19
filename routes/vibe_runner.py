"""CODEC Vibe: show or run a project on this Mac, routes (docs/VIBE-PROJECTS-DESIGN.md, step 2b).

Everything here starts or reads a program, so it answers only a person at this Mac: a request
that came through the tunnel or from another address gets 403 (the same rule as /api/run_code).
VIBE_PROJECTS_ENABLED=false answers 404.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import codec_vibe_projects as vp
import codec_vibe_runner as vr

router = APIRouter()


async def _local(request: Request, fn, *args):
    if not vp.enabled():
        return JSONResponse({"error": "Vibe projects are switched off"}, status_code=404)
    from routes._shared import _is_remote_request
    if _is_remote_request(request):
        return JSONResponse({"error": "Running a project works only on this Mac."}, status_code=403)
    try:
        return await asyncio.to_thread(fn, *args)
    except vp.VibeError as e:
        return JSONResponse({"error": e.message}, status_code=e.status)


@router.post("/api/vibe/projects/{pid}/serve")
async def serve(pid: str, request: Request):
    return await _local(request, vr.start_serve, pid)


@router.post("/api/vibe/projects/{pid}/run")
async def run(pid: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        return JSONResponse({"error": "Send a JSON object"}, status_code=400)
    return await _local(request, vr.start_run, pid, body.get("command"), body.get("port"))


@router.get("/api/vibe/projects/{pid}/live")
async def live(pid: str, request: Request, since: int = 0):
    return await _local(request, vr.status, pid, since)


@router.post("/api/vibe/projects/{pid}/stop")
async def stop(pid: str, request: Request):
    return await _local(request, vr.stop, pid)
