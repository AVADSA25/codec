"""CODEC Dashboard -- Agent/crew routes (deep research, agent crews, custom agents)."""
import os, json, re, threading, asyncio, uuid
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from routes._shared import (
    _research_jobs, _agent_jobs, _AGENTS_DIR,
)

router = APIRouter()


@router.post("/api/deep_research")
async def deep_research_start(request: Request):
    """Start deep research job -- returns job_id immediately (avoids proxy timeouts)"""
    body = await request.json()
    topic = body.get("topic", "")
    if not topic or len(topic) < 5:
        return JSONResponse({"error": "Topic too short"}, status_code=400)

    job_id = str(uuid.uuid4())[:8]
    _research_jobs[job_id] = {"status": "running", "topic": topic, "started": datetime.now().isoformat()}

    async def _run_async():
        try:
            from codec_agents import run_crew
            result = await run_crew("deep_research", topic=topic)
            _research_jobs[job_id].update(result)
        except Exception as e:
            import traceback; traceback.print_exc()
            _research_jobs[job_id]["status"] = "error"
            _research_jobs[job_id]["error"] = str(e)

    asyncio.create_task(_run_async())
    return {"job_id": job_id, "status": "running", "topic": topic}


@router.get("/api/deep_research/{job_id}")
async def deep_research_status(job_id: str):
    """Poll research job status"""
    job = _research_jobs.get(job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    return job


@router.get("/api/agents/crews")
async def list_agent_crews():
    """List available agent crews."""
    from codec_agents import list_crews
    return {"crews": list_crews()}


@router.post("/api/agents/run")
async def run_agent_crew(request: Request):
    """Start an agent crew in background -- returns job_id immediately to avoid proxy timeouts."""
    body = await request.json()
    crew_name = body.pop("crew", "")
    if not crew_name:
        return JSONResponse({"error": "Missing 'crew' field"}, status_code=400)

    job_id = str(uuid.uuid4())[:8]
    _agent_jobs[job_id] = {
        "status": "running",
        "crew": crew_name,
        "progress": [],
        "started": datetime.now().isoformat(),
    }

    def _run():
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        progress_log = _agent_jobs[job_id]["progress"]

        def on_progress(update):
            progress_log.append(update)
            print(f"[Agents] {update}")

        try:
            if crew_name == "custom":
                from codec_agents import run_custom_agent
                result = loop.run_until_complete(run_custom_agent(
                    name           = body.get("agent_name", "Custom"),
                    role           = body.get("role", ""),
                    tools          = body.get("tools", []),
                    max_iterations = int(body.get("max_iterations", 8)),
                    task           = body.get("task", ""),
                    callback       = on_progress,
                ))
            else:
                from codec_agents import run_crew
                result = loop.run_until_complete(run_crew(crew_name, callback=on_progress, **body))
            _agent_jobs[job_id].update(result)
            _agent_jobs[job_id]["status"] = result.get("status", "complete")
            _agent_jobs[job_id]["progress"] = progress_log
        except Exception as e:
            import traceback; traceback.print_exc()
            _agent_jobs[job_id]["status"] = "error"
            _agent_jobs[job_id]["error"] = str(e)
        finally:
            loop.close()

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id, "status": "running", "crew": crew_name}


@router.get("/api/agents/status/{job_id}")
async def agent_job_status(job_id: str):
    """Poll agent job status. Returns full result when status != 'running'."""
    job = _agent_jobs.get(job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    return job


@router.get("/api/agents/tools")
async def list_agent_tools():
    """Return all available tool names + descriptions for the custom agent builder."""
    from codec_agents import get_all_tools
    tools = get_all_tools()
    return {"tools": [{"name": t.name, "description": t.description} for t in tools]}


@router.post("/api/agents/custom/save")
async def save_custom_agent(request: Request):
    """Save a custom agent definition to ~/.codec/agents/"""
    try:
        body = await request.json()
        name = (body.get("name") or "").strip()
        if not name:
            return JSONResponse({"error": "Name required"}, status_code=400)
        safe_id = re.sub(r"[^\w\-]", "_", name.lower())
        path = os.path.join(_AGENTS_DIR, safe_id + ".json")
        with open(path, "w") as f:
            json.dump({**body, "id": safe_id}, f, indent=2)
        return {"saved": True, "id": safe_id, "path": path}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/agents/custom/list")
async def list_custom_agents():
    """List saved custom agent definitions."""
    agents = []
    for f in sorted(os.listdir(_AGENTS_DIR)):
        if f.endswith(".json"):
            try:
                with open(os.path.join(_AGENTS_DIR, f)) as fh:
                    agents.append(json.load(fh))
            except Exception:
                pass
    return {"agents": agents}


@router.post("/api/agents/custom/delete")
async def delete_custom_agent(request: Request):
    """Delete a saved custom agent definition."""
    try:
        body = await request.json()
        agent_id = (body.get("id") or "").strip()
        if not agent_id:
            return JSONResponse({"error": "Agent ID required"}, status_code=400)
        safe_id = re.sub(r"[^\w\-]", "_", agent_id)
        path = os.path.join(_AGENTS_DIR, safe_id + ".json")
        if os.path.exists(path):
            os.remove(path)
            return {"deleted": True, "id": safe_id}
        return JSONResponse({"error": "Agent not found"}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Phase 1 Step 3: AskUserQuestion reply path ──────────────────────────────
# Per docs/PHASE1-STEP3-DESIGN.md §1.5. PWA + voice both POST here.
# §1.7 strict-consent gate enforced inside codec_ask_user.submit_answer —
# rejected answers return HTTP 200 with {"ok": False, "rejected": True,
# "reason": "ambiguous_consent", "remaining_attempts": N} so the panel
# can re-prompt without an error toast.
@router.post("/api/agents/answer/{pending_question_id}")
async def submit_ask_user_answer(pending_question_id: str, request: Request):
    """Submit a user answer to a pending AskUserQuestion."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    answer = (body.get("answer") or "").strip()
    answered_via = (body.get("answered_via") or "pwa").strip().lower()
    if answered_via not in ("pwa", "voice"):
        answered_via = "pwa"
    try:
        from codec_ask_user import submit_answer
    except ImportError:
        return JSONResponse({"error": "ask_user_not_available"}, status_code=503)
    result = submit_answer(
        pending_question_id, answer, answered_via=answered_via)
    if result.get("ok"):
        return result
    err = result.get("error")
    if err == "not_found":
        return JSONResponse(result, status_code=404)
    if err in ("already_answered", "already_timed_out"):
        return JSONResponse(result, status_code=409)
    if result.get("rejected"):
        # 200 OK — the panel re-prompts with remaining_attempts.
        return result
    return JSONResponse(result, status_code=400)


@router.get("/api/agents/pending_questions")
async def list_pending_questions():
    """List currently-pending AskUserQuestion records. Used by the dashboard
    to render the inline answer panel; voice loop also polls this when
    deciding whether to switch into single-question listen mode."""
    try:
        from codec_ask_user import _load_pending_questions
        data = _load_pending_questions()
        # Filter to status="pending" only — answered/timed_out are history.
        pending = [r for r in data.get("pending_questions", [])
                   if r.get("status") == "pending"]
        return {"pending_questions": pending, "count": len(pending)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Phase 3 Step 8 — Agent Plan + Permission Contract endpoints ───────────────
import logging as _logging
from typing import Any as _Any, Dict as _Dict, List as _List, Optional as _Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field

import codec_agent_plan as _cap

_log = _logging.getLogger("routes.agents.plan")


class CreateAgentBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)
    description: str = Field(..., min_length=1)
    notification_channels: _Optional[_List[str]] = Field(default=None)


class RejectBody(BaseModel):
    reason: str = Field(default="", max_length=500)


class ReviseBody(BaseModel):
    edited_plan: _Dict[str, _Any] = Field(...)


class GlobalGrantBody(BaseModel):
    kind: str = Field(...)
    value: str = Field(..., min_length=1)


@router.post("/api/agents")
def create_agent(body: CreateAgentBody):
    try:
        agent_id = _cap.create_agent(
            title=body.title,
            description=body.description,
            notification_channels=body.notification_channels,
        )
    except _cap.DescriptionTooVagueError as e:
        raise HTTPException(status_code=400, detail=f"description too vague: {e}")
    except _cap.PlanValidationError as e:
        raise HTTPException(status_code=400, detail=f"plan invalid: {e}")
    except _cap.QwenUnavailableError as e:
        raise HTTPException(status_code=503, detail=f"Qwen-3.6 unavailable: {e}")

    manifest = _cap.load_manifest(agent_id)
    return {
        "agent_id": agent_id,
        "status": manifest.get("status", "unknown"),
        "project_dir": manifest.get("project_dir"),  # Phase 3.5: human-browseable folder
    }


@router.get("/api/agents")
def list_agents():
    """List all agents (any status). Returns a thin manifest summary."""
    out: _List[_Dict[str, _Any]] = []
    if not _cap._AGENTS_DIR.exists():
        return {"agents": []}
    for d in sorted(_cap._AGENTS_DIR.iterdir()):
        if not d.is_dir():
            continue
        m = _cap.load_manifest(d.name)
        if m:
            out.append({
                "agent_id": m.get("agent_id", d.name),
                "title":    m.get("title", "(untitled)"),
                "status":   m.get("status", "unknown"),
                "created_at": m.get("created_at"),
                "updated_at": m.get("updated_at"),
            })
    return {"agents": out}


@router.get("/api/agents/{agent_id}")
def get_agent(agent_id: str):
    manifest = _cap.load_manifest(agent_id)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"agent {agent_id} not found")
    plan = _cap.load_plan(agent_id)
    state = _cap.load_state(agent_id)
    grants = _cap.load_grants(agent_id) or None
    return {
        "manifest": manifest,
        "plan": plan.to_dict() if plan else None,
        "state": state,
        "grants": grants,
    }


@router.post("/api/agents/{agent_id}/approve")
def approve_agent(agent_id: str):
    manifest = _cap.load_manifest(agent_id)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"agent {agent_id} not found")
    try:
        grants = _cap.approve_plan(agent_id)
    except _cap.InvalidStatusTransition as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"agent_id": agent_id, "status": "approved", "grants": grants}


@router.post("/api/agents/{agent_id}/reject")
def reject_agent(agent_id: str, body: RejectBody):
    manifest = _cap.load_manifest(agent_id)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"agent {agent_id} not found")
    try:
        _cap.reject_plan(agent_id, reason=body.reason)
    except _cap.InvalidStatusTransition as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"agent_id": agent_id, "status": "rejected"}


@router.post("/api/agents/{agent_id}/revise")
def revise_agent(agent_id: str, body: ReviseBody):
    manifest = _cap.load_manifest(agent_id)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"agent {agent_id} not found")
    try:
        plan = _cap.revise_plan(agent_id, body.edited_plan)
    except _cap.PlanValidationError as e:
        raise HTTPException(status_code=400, detail=f"plan invalid: {e}")
    except _cap.InvalidStatusTransition as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"agent_id": agent_id, "status": "awaiting_approval",
            "plan": plan.to_dict()}


@router.get("/api/agent_global_grants")
def get_global_grants():
    return _cap.load_global_grants()


@router.post("/api/agent_global_grants")
def add_global_grant(body: GlobalGrantBody):
    try:
        _cap.add_global_grant(body.kind, body.value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    import secrets as _secrets
    cid = _secrets.token_hex(6)
    _cap._audit(_cap.AGENT_GLOBAL_GRANT_ADDED, "codec-agent-plan",
               f"grant added: {body.kind}={body.value}",
               correlation_id=cid,
               extra={"kind": body.kind, "value": body.value})
    return _cap.load_global_grants()


@router.delete("/api/agent_global_grants")
def delete_global_grant(body: GlobalGrantBody):
    try:
        _cap.remove_global_grant(body.kind, body.value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    import secrets as _secrets
    cid = _secrets.token_hex(6)
    _cap._audit(_cap.AGENT_GLOBAL_GRANT_REMOVED, "codec-agent-plan",
               f"grant removed: {body.kind}={body.value}",
               correlation_id=cid,
               extra={"kind": body.kind, "value": body.value})
    return _cap.load_global_grants()


class GrantBody(BaseModel):
    kind: str = Field(...)
    value: str = Field(..., min_length=1)


@router.post("/api/agents/{agent_id}/abort")
def abort_agent(agent_id: str):
    manifest = _cap.load_manifest(agent_id)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"agent {agent_id} not found")
    try:
        _cap.set_status(agent_id, "aborted", reason="user_aborted")
    except _cap.InvalidStatusTransition as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"agent_id": agent_id, "status": "aborted"}


@router.post("/api/agents/{agent_id}/pause")
def pause_agent(agent_id: str):
    manifest = _cap.load_manifest(agent_id)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"agent {agent_id} not found")
    try:
        _cap.set_status(agent_id, "paused", reason="user_paused")
    except _cap.InvalidStatusTransition as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"agent_id": agent_id, "status": "paused"}


@router.post("/api/agents/{agent_id}/resume")
def resume_agent(agent_id: str):
    manifest = _cap.load_manifest(agent_id)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"agent {agent_id} not found")
    try:
        _cap.set_status(agent_id, "running")
    except _cap.InvalidStatusTransition as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"agent_id": agent_id, "status": "running"}


@router.post("/api/agents/{agent_id}/grant")
def grant_permission(agent_id: str, body: GrantBody):
    """Grant a missing permission to a blocked agent. Adds the
    item to per-agent grants.json (NOT global). If status is
    blocked_on_permission, transitions back to running."""
    manifest = _cap.load_manifest(agent_id)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"agent {agent_id} not found")

    valid_kinds = {"skills", "read_paths", "write_paths", "network_domains"}
    if body.kind not in valid_kinds:
        raise HTTPException(status_code=400,
                             detail=f"invalid kind: {body.kind}; expected one of {sorted(valid_kinds)}")

    grants = _cap.load_grants(agent_id)
    if not grants:
        raise HTTPException(status_code=409, detail="agent has no grants yet (not approved?)")

    grants[body.kind] = sorted(set(grants.get(body.kind, []) + [body.value]))
    _cap.save_grants(agent_id, grants)

    # If blocked, unblock
    if manifest.get("status") == "blocked_on_permission":
        try:
            _cap.set_status(agent_id, "running")
        except _cap.InvalidStatusTransition:
            pass  # ignore; just leave as-is

    return {"agent_id": agent_id, "grants": grants,
            "status": _cap.load_manifest(agent_id).get("status")}


# ── Phase 3 Step 9 review fix I2 — extend step_budget for paused agents ────
class ExtendBudgetBody(BaseModel):
    additional_steps: int = Field(..., ge=1, le=100)


@router.post("/api/agents/{agent_id}/extend_budget")
def extend_budget(agent_id: str, body: ExtendBudgetBody):
    """Bump the current checkpoint's step_budget for an agent paused on
    step_budget_exhausted. Writes step_budget_overrides[checkpoint_id]
    in state.json (mutable; does NOT modify plan.json so plan_hash
    tamper check stays intact). Transitions paused → running so the
    daemon respawns the thread on its next tick.

    409 if status != paused or status_reason != step_budget_exhausted.
    Body: {"additional_steps": int} where 1 <= int <= 100.
    """
    manifest = _cap.load_manifest(agent_id)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"agent {agent_id} not found")

    status = manifest.get("status", "")
    reason = manifest.get("status_reason", "")
    if status != "paused" or reason != "step_budget_exhausted":
        raise HTTPException(
            status_code=409,
            detail=f"agent must be paused with reason=step_budget_exhausted "
                   f"(currently status={status!r}, reason={reason!r})",
        )

    plan = _cap.load_plan(agent_id)
    if plan is None:
        raise HTTPException(status_code=409, detail="agent has no plan")
    state = _cap.load_state(agent_id)
    current_idx = int(state.get("current_checkpoint", 0))
    if current_idx >= len(plan.checkpoints):
        raise HTTPException(status_code=409, detail="agent has no current checkpoint")

    cp = plan.checkpoints[current_idx]
    overrides = state.get("step_budget_overrides", {}) or {}
    base = int(overrides.get(cp.id, cp.step_budget))
    new_budget = base + int(body.additional_steps)
    overrides[cp.id] = new_budget
    state["step_budget_overrides"] = overrides
    _cap.save_state(agent_id, state)

    try:
        _cap.set_status(agent_id, "running")
    except _cap.InvalidStatusTransition as e:
        raise HTTPException(status_code=409, detail=str(e))

    return {
        "agent_id": agent_id,
        "checkpoint_id": cp.id,
        "previous_budget": base,
        "new_budget": new_budget,
        "additional_steps": int(body.additional_steps),
        "status": "running",
    }


# ── Phase 3 Step 10 — messaging endpoints ──────────────────────────────────


class UserReplyBody(BaseModel):
    body: str = Field(..., min_length=1, max_length=5000)


class SilenceBody(BaseModel):
    silenced: bool = Field(...)


@router.get("/api/agents/{agent_id}/messages")
def get_messages(agent_id: str):
    """Return all entries from messages.jsonl as a list (newest last)."""
    manifest = _cap.load_manifest(agent_id)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"agent {agent_id} not found")

    msg_path = _cap._AGENTS_DIR / agent_id / "messages.jsonl"
    if not msg_path.exists():
        return {"messages": []}

    out = []
    with open(msg_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return {"messages": out}


@router.get("/api/agents/{agent_id}/artifacts")
def get_artifacts(agent_id: str):
    """List files created in the agent's project_dir."""
    manifest = _cap.load_manifest(agent_id)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"agent {agent_id} not found")
    project_dir = manifest.get("project_dir", "")
    if not project_dir or not os.path.isdir(project_dir):
        return {"project_dir": project_dir, "files": []}
    files = []
    try:
        for fname in sorted(os.listdir(project_dir)):
            fpath = os.path.join(project_dir, fname)
            if os.path.isfile(fpath):
                size = os.path.getsize(fpath)
                files.append({"name": fname, "path": fpath, "size": size})
    except Exception:
        pass
    return {"project_dir": project_dir, "files": files}


@router.post("/api/agents/{agent_id}/open-folder")
def open_folder(agent_id: str):
    """Open the agent's project_dir in macOS Finder."""
    import subprocess
    manifest = _cap.load_manifest(agent_id)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"agent {agent_id} not found")
    project_dir = manifest.get("project_dir", "")
    if not project_dir or not os.path.isdir(project_dir):
        return JSONResponse({"error": "project_dir not found"}, status_code=404)
    try:
        subprocess.Popen(["open", project_dir])
        return {"ok": True, "opened": project_dir}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/agents/{agent_id}/messages")
def post_message_endpoint(agent_id: str, body: UserReplyBody):
    """User → agent reply. Writes type=user_reply to messages.jsonl.
    Daemon picks up next tick."""
    manifest = _cap.load_manifest(agent_id)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"agent {agent_id} not found")

    import codec_agent_messaging as cam
    record = cam.post_user_reply(agent_id=agent_id, body=body.body)
    return {"agent_id": agent_id, "ok": True, "ts": record["ts"]}


@router.post("/api/agents/{agent_id}/silence")
def silence_endpoint(agent_id: str, body: SilenceBody):
    """Toggle silence for an agent. Silenced = post_message writes timeline
    but skips notifications.json (no banner spam)."""
    manifest = _cap.load_manifest(agent_id)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"agent {agent_id} not found")

    import codec_agent_messaging as cam
    cam.set_silenced(agent_id, body.silenced)
    return {"agent_id": agent_id, "silenced": cam.is_silenced(agent_id)}


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3.5 — Proactive Intelligence Overlay endpoints
# ─────────────────────────────────────────────────────────────────────────────

class ProactiveAckBody(BaseModel):
    pattern_id: str = Field(...)


class ProactiveDismissBody(BaseModel):
    pattern_id: str = Field(...)
    scope: str = Field("today")  # "today" | "forever"


@router.get("/api/proactive/patterns")
def list_proactive_patterns():
    """List registered proactive-suggestion patterns + their state.
    For PWA settings panel."""
    try:
        import codec_proactive as cp
        return {"patterns": cp.list_patterns(), "enabled": cp.is_enabled()}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/api/proactive/acknowledge")
def acknowledge_proactive(body: ProactiveAckBody):
    """User clicked Acknowledge on a proactive suggestion."""
    try:
        import codec_proactive as cp
        cp.acknowledge(body.pattern_id)
        return {"pattern_id": body.pattern_id, "acknowledged": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/proactive/dismiss")
def dismiss_proactive(body: ProactiveDismissBody):
    """User dismissed a proactive suggestion. scope ∈ {today, forever}."""
    if body.scope not in ("today", "forever"):
        raise HTTPException(status_code=400,
                             detail=f"invalid scope {body.scope!r}; expected 'today' or 'forever'")
    try:
        import codec_proactive as cp
        cp.dismiss(body.pattern_id, scope=body.scope)
        return {"pattern_id": body.pattern_id, "dismissed_scope": body.scope}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
