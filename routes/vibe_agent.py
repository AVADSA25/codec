"""CODEC Vibe coding agent routes (docs/VIBE-AGENT-DESIGN.md, step 3a).

Thin HTTP layer over codec_vibe_agent. A run is a background thread; the page starts it, then
polls GET .../agent/{run}?since=N (optionally waiting a few seconds for news). Everything sits
behind the dashboard's normal login. Nothing here runs a program. VIBE_AGENT_ENABLED=false or
VIBE_PROJECTS_ENABLED=false answers 404.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import codec_vibe_agent as va
import codec_vibe_projects as vp

router = APIRouter()

_BAD_BODY = JSONResponse({"error": "Send a JSON object"}, status_code=400)


async def _call(fn, *args, **kwargs):
    if not vp.enabled() or not va.enabled():
        return JSONResponse({"error": "The agent is switched off"}, status_code=404)
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


@router.get("/api/vibe/agent/models")
async def models():
    return await _call(va.models_info)


@router.post("/api/vibe/projects/{pid}/agent/run")
async def run(pid: str, request: Request):
    body = await _json(request)
    if body is None:
        return _BAD_BODY
    from routes._shared import _is_remote_request
    return await _call(va.start_run, pid, body.get("goal"), model=body.get("model") or "local",
                       confirm_cloud=body.get("confirm_cloud") is True, history=body.get("history"),
                       can_run=not _is_remote_request(request))


@router.get("/api/vibe/projects/{pid}/agent/runs")
async def runs(pid: str):
    return await _call(va.list_runs, pid)


async def _mac_only(request: Request):
    from routes._shared import _is_remote_request
    if _is_remote_request(request):
        return JSONResponse({"error": "Commands are approved only at this Mac."}, status_code=403)
    return None


@router.get("/api/vibe/projects/{pid}/agent/commands")
async def commands(pid: str, request: Request):
    no = await _mac_only(request)
    if no is not None:
        return no
    return await _call(va.commands, pid)


@router.get("/api/vibe/projects/{pid}/agent/{run_id}")
async def get_run(pid: str, run_id: str, since: int = 0, wait: float = 0.0):
    return await _call(va.get_run, pid, run_id, since, wait)


@router.post("/api/vibe/projects/{pid}/agent/{run_id}/stop")
async def stop(pid: str, run_id: str):
    return await _call(va.stop_run, pid, run_id)


@router.post("/api/vibe/projects/{pid}/agent/{run_id}/approve")
async def approve(pid: str, run_id: str, request: Request):
    no = await _mac_only(request)
    if no is not None:
        return no
    body = await _json(request)
    if body is None:
        return _BAD_BODY
    return await _call(va.approve_run, pid, run_id, body.get("id"), body.get("decision"), body.get("network") is True)


@router.post("/api/vibe/projects/{pid}/agent/commands/forget")
async def forget(pid: str, request: Request):
    no = await _mac_only(request)
    if no is not None:
        return no
    body = await _json(request)
    if body is None:
        return _BAD_BODY
    return await _call(va.forget, pid, body.get("command"))


@router.post("/api/vibe/projects/{pid}/agent/{run_id}/answer")
async def answer(pid: str, run_id: str, request: Request):
    body = await _json(request)
    if body is None:
        return _BAD_BODY
    text = body.get("text") if isinstance(body.get("text"), str) and body.get("text").strip() else body.get("choice")
    return await _call(va.answer_run, pid, run_id, body.get("id"), text)
