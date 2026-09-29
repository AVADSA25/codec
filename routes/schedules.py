"""CODEC Schedules API routes — scheduled crews, skills and prompts.

D3 / SR-44: extracted from codec_dashboard.py. P3.3 (docs/P3.3-DESIGN.md): a
job is a crew, a non-destructive skill or a free prompt, timed in plain
language; create and update check every field (codec_scheduler.clean_job_input);
"Run now" uses the same runner as a timed fire (codec_scheduler.run_scheduled),
which records the full output in the history and delivers it.
"""
from __future__ import annotations

import threading
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import codec_scheduler

router = APIRouter()


async def _json(request: Request):
    try:
        body = await request.json()
    except Exception:
        return None
    return body if isinstance(body, dict) else None


@router.get("/api/schedules")
async def list_schedules_api():
    """All jobs, each with its kind, plain-language reading and next run."""
    try:
        return {"schedules": [codec_scheduler.job_view(s) for s in codec_scheduler.load_schedules()]}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/schedules")
async def add_schedule_api(request: Request):
    """Create a job: {kind, prompt | skill+task | crew+topic, when, label, deliver,
    speak, only_if_changed, continuity, enabled}. Older crew bodies with
    hour/minute/days still work."""
    body = await _json(request)
    if body is None:
        return JSONResponse({"error": "Send the job as a JSON object."}, status_code=400)
    try:
        job = codec_scheduler.create_job(body)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"schedule": codec_scheduler.job_view(job)}


@router.post("/api/schedules/parse")
async def parse_schedule_when(request: Request):
    """Read a plain-language timing for the live preview."""
    body = await _json(request) or {}
    try:
        when = codec_scheduler.parse_when(body.get("when", ""))
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    # As a job created now: a time already passed today is not "now" but next time.
    nr = codec_scheduler.next_run({"enabled": True, "when": when, "created": datetime.now().isoformat()})
    return {"ok": True, "when": when, "text": codec_scheduler.describe_when(when),
            "next_run": nr.isoformat(timespec="minutes") if nr else None}


@router.get("/api/schedules/skills")
def schedulable_skills_api():
    """The skills a job may run unattended (no destructive or per-call-consent ones)."""
    return {"skills": codec_scheduler.schedulable_skills()}


@router.delete("/api/schedules/{sched_id}")
async def delete_schedule_api(sched_id: str):
    """Remove a schedule by ID."""
    try:
        removed = codec_scheduler.remove_schedule(sched_id)
        return {"removed": removed, "id": sched_id}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.put("/api/schedules/{sched_id}")
async def update_schedule(sched_id: str, request: Request):
    """Update a job with checked fields only (schedules.json is written with
    codec_jsonstore.atomic_write_json under its lock, by codec_scheduler)."""
    body = await _json(request)
    if body is None:
        return JSONResponse({"error": "Send the changes as a JSON object."}, status_code=400)
    try:
        job = codec_scheduler.update_job(sched_id, body)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if job is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return {"schedule": codec_scheduler.job_view(job)}


@router.post("/api/schedules/{sched_id}/run")
async def run_schedule_now(sched_id: str):
    """Run a job now, in the background, through the same runner as a timed fire."""
    job = next((s for s in codec_scheduler.load_schedules() if s.get("id") == sched_id), None)
    if not job:
        return JSONResponse({"error": "Not found"}, status_code=404)

    def _run():
        try:
            codec_scheduler.run_scheduled(job, manual=True)
        except Exception as e:
            codec_scheduler.log.error(f"Run now failed for {sched_id}: {e}")

    threading.Thread(target=_run, name="codec-schedule-run-now", daemon=True).start()
    return {"status": "running", "id": sched_id, "kind": codec_scheduler.job_kind(job)}


@router.get("/api/schedules/history")
async def schedule_history(schedule_id: str = "", limit: int = 100):
    """Runs newest first, with the full output (old log lines are read too)."""
    return codec_scheduler.read_runs(limit=max(1, min(int(limit), 500)), sched_id=schedule_id or None)
