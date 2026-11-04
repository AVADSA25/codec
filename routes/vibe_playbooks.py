"""CODEC Vibe playbooks and the review run (P-071, docs/VIBE-PLAYBOOKS-DESIGN.md).

Thin HTTP layer over codec_vibe_playbooks and codec_vibe_agent.review_run, behind the dashboard's normal login.
Approving and removing an approval answer 403 to anything not from this Mac. No route writes playbook text: the
approval stores a hash. VIBE_AGENT_ENABLED=false or VIBE_PROJECTS_ENABLED=false answers 404, and so does
vibe_agent.playbooks=false.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import codec_vibe_agent as va
import codec_vibe_playbooks as vpb
import codec_vibe_projects as vp

router = APIRouter()

_BAD_BODY = JSONResponse({"error": "Send a JSON object"}, status_code=400)


def _off() -> bool:
    return not vp.enabled() or not va.enabled() or not va.settings().get("playbooks")


async def _call(fn, *args, **kwargs):
    if _off():
        return JSONResponse({"error": "Playbooks are switched off"}, status_code=404)
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except (vpb.PlaybookError, vp.VibeError) as e:
        return JSONResponse({"error": e.message}, status_code=e.status)


async def _json(request: Request):
    try:
        body = await request.json()
    except Exception:
        return None
    return body if isinstance(body, dict) else None


def _listed() -> dict:
    return {"playbooks": [pb.public() for pb in vpb.scan()]}


async def _mac_only(request: Request):
    from routes._shared import _is_remote_request
    if _is_remote_request(request):
        return JSONResponse({"error": "Playbooks are approved only at this Mac."}, status_code=403)
    return None


@router.get("/api/vibe/playbooks")
async def playbooks():
    return await _call(_listed)


@router.get("/api/vibe/playbooks/{source}/{name}")
async def playbook(source: str, name: str):
    return await _call(vpb.read_text, source, name)


@router.post("/api/vibe/playbooks/approve")
async def approve(request: Request):
    no = await _mac_only(request)
    if no is not None:
        return no
    body = await _json(request)
    if body is None:
        return _BAD_BODY
    return await _call(vpb.approve, body.get("source"), body.get("name"), body.get("sha256"))


@router.post("/api/vibe/playbooks/revoke")
async def revoke(request: Request):
    no = await _mac_only(request)
    if no is not None:
        return no
    body = await _json(request)
    if body is None:
        return _BAD_BODY
    return await _call(vpb.revoke, body.get("source"), body.get("name"))


@router.post("/api/vibe/projects/{pid}/agent/review")
async def review(pid: str, request: Request):
    """A read-only review run over the files changed since a version. Answers from anywhere, like /run: the run has
    only reading tools and commands are never offered to it."""
    body = await _json(request)
    if body is None:
        return _BAD_BODY
    return await _call(va.review_run, pid, body.get("checkpoint"), playbook=body.get("playbook") or "review",
                       focus=body.get("focus"), model=body.get("model") or "local",
                       confirm_cloud=body.get("confirm_cloud") is True, cloud_id=body.get("cloud_id"),
                       temporary=body.get("temporary") is True)
