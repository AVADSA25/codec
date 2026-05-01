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
