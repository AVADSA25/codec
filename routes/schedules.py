"""CODEC Schedules API routes — cron-style scheduled crew runs.

D3 / SR-44: extracted from codec_dashboard.py. Schedule CRUD + manual
run-now + history. The /run endpoint dispatches a background thread
that executes the crew, generates a markdown report, saves it to a
Google Doc, and posts a notification.

All shared state (notifications, audit, config path) comes from
routes/_shared. codec_jsonstore handles the atomic write of
schedules.json (re-audit medium fix).
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from routes._shared import (
    log, DASHBOARD_DIR, CONFIG_PATH,
    _notif_lock, _load_notifications, _write_notifications,
    _append_schedule_run_log,
)

router = APIRouter()


@router.get("/api/schedules")
async def list_schedules_api():
    """List all scheduled agent runs."""
    try:
        from codec_scheduler import load_schedules
        return {"schedules": load_schedules()}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/schedules")
async def add_schedule_api(request: Request):
    """Add a new scheduled agent run."""
    body = await request.json()
    required = ["crew"]
    for field in required:
        if field not in body:
            return JSONResponse({"error": f"Missing field: {field}"}, status_code=400)
    try:
        from codec_scheduler import add_schedule
        s = add_schedule(
            body["crew"],
            topic=body.get("topic", ""),
            cron_hour=body.get("hour", 8),
            cron_minute=body.get("minute", 0),
            days=body.get("days"),
        )
        return {"schedule": s}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.delete("/api/schedules/{sched_id}")
async def delete_schedule_api(sched_id: str):
    """Remove a schedule by ID."""
    try:
        from codec_scheduler import remove_schedule
        removed = remove_schedule(sched_id)
        return {"removed": removed, "id": sched_id}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.put("/api/schedules/{sched_id}")
async def update_schedule(sched_id: str, request: Request):
    """Update an existing schedule by ID."""
    body = await request.json()
    sched_path = os.path.join(os.path.expanduser("~"), ".codec", "schedules.json")
    schedules = []
    try:
        with open(sched_path) as f:
            schedules = json.load(f)
    except Exception:
        pass
    for s in schedules:
        if s.get("id") == sched_id:
            s.update({k: v for k, v in body.items() if k != "id"})
            # re-audit medium: atomic write (was truncate-then-write, racing
            # the scheduler's concurrent read of schedules.json).
            import codec_jsonstore
            codec_jsonstore.atomic_write_json(sched_path, schedules)
            return {"schedule": s}
    return JSONResponse({"error": "Not found"}, status_code=404)


@router.post("/api/schedules/{sched_id}/run")
async def run_schedule_now(sched_id: str):
    """Manually trigger a scheduled task — actually executes it in a background thread."""
    sched_path = os.path.join(os.path.expanduser("~"), ".codec", "schedules.json")
    schedules = []
    try:
        with open(sched_path) as f:
            schedules = json.load(f)
    except Exception:
        return JSONResponse({"error": "No schedules found"}, status_code=404)

    schedule = None
    for s in schedules:
        if s.get("id") == sched_id:
            schedule = s
            break
    if not schedule:
        return JSONResponse({"error": "Not found"}, status_code=404)

    # Pre-create a notification id so we can return it immediately
    notif_id = f"notif_{uuid.uuid4().hex[:10]}"
    crew = schedule.get("crew", "general")
    topic = schedule.get("topic", "")
    title = schedule.get("label", topic[:60] or "Scheduled Task")

    def _execute_task():
        """Background thread: run the task, generate report, save to Google Doc."""
        try:
            config = {}
            try:
                with open(CONFIG_PATH) as f:
                    config = json.load(f)
            except Exception:
                pass
            base_url = config.get("llm_base_url", "http://localhost:8083/v1")
            model = config.get("llm_model", "mlx-community/Qwen3.6-35B-A3B-4bit")
            # PR-2B (D-15 partial): keychain-aware live read.
            from codec_config import get_llm_api_key as _kc_get_llm
            api_key = _kc_get_llm()
            kwargs = config.get("llm_kwargs", {})

            # ── Step 1: If it's a skill-based task, run the skill directly ──
            skill_output = None
            if "ai news digest" in topic.lower() or "news digest" in topic.lower():
                try:
                    import importlib.util
                    spec = importlib.util.spec_from_file_location("ai_news", os.path.join(DASHBOARD_DIR, "skills", "ai_news_digest.py"))
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    skill_output = mod.run()
                except Exception as e:
                    log.warning(f"Skill execution failed, falling back to LLM: {e}")

            # ── Step 2: Build prompt for LLM report ──
            crew_instructions = {
                "research": "You are a senior research analyst at CODEC. Produce a structured, professional report with markdown formatting: use ## headings, bullet points, and a summary section.",
                "security": "You are a CODEC security analyst. Produce a structured security assessment report with markdown formatting: use ## headings for each area, risk levels, and recommendations.",
                "writer": "You are a professional content writer at CODEC. Write a well-structured article with markdown formatting: ## headings, clear sections, and a conclusion.",
                "analyst": "You are a CODEC data analyst. Produce a structured analysis report with markdown formatting: ## headings, key metrics, insights, and action items.",
            }
            system_msg = crew_instructions.get(crew, "You are CODEC, an AI assistant. Produce a structured, professional report with markdown formatting: ## headings, bullet points, key findings, and a summary.")

            if skill_output:
                prompt = f"Here is raw data collected for the task '{title}':\n\n{skill_output}\n\nProduce a well-structured, professional report based on this data. Use ## headings, highlight the most important items, add brief analysis, and end with key takeaways."
            elif crew == "custom" and topic:
                prompt = f"Execute this task and produce a detailed, structured report:\n\n{topic}"
            else:
                prompt = f"Task: {topic}\n\nProduce a detailed, structured report."

            # A-12 (PR-3E-dashboard): canonical codec_llm.call. raise_on_error=True
            # preserves the original raise-on-failure (was r.json() KeyError) so the
            # outer handler still sees a failure instead of writing an empty report.
            # kwargs passed unfiltered (matches the original payload.update(kwargs),
            # which lets kwargs override enable_thinking).
            import codec_llm
            answer = codec_llm.call(
                [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
                base_url=base_url, model=model, api_key=api_key,
                max_tokens=6000, temperature=0.7, enable_thinking=True,
                extra_kwargs=kwargs, timeout=300, raise_on_error=True,
            )
            answer = re.sub(r'<think>[\s\S]*?</think>', '', answer).strip()

            # ── Step 3: Save to Google Doc ──
            doc_url = None
            try:
                from codec_gdocs import create_google_doc
                doc_title = f"CODEC Report — {title} — {datetime.now().strftime('%b %d, %Y')}"
                doc_url = create_google_doc(doc_title, answer)
                log.info(f"Report saved to Google Doc: {doc_url}")
            except Exception as e:
                log.warning(f"Google Doc creation failed (report still saved locally): {e}")

            # ── Step 4: Save success notification with doc link ──
            body_text = answer[:2000]
            if doc_url:
                body_text = f"📄 [View Full Report]({doc_url})\n\n{body_text}"
            with _notif_lock:
                notifications = _load_notifications()
                for n in notifications:
                    if n["id"] == notif_id:
                        n["body"] = body_text
                        n["status"] = "success"
                        if doc_url:
                            n["doc_url"] = doc_url
                        break
                _write_notifications(notifications)
            _append_schedule_run_log(sched_id, title, "success", (doc_url or answer[:200]))
            log.info(f"Schedule {sched_id} executed successfully")

        except Exception as e:
            error_msg = f"Task execution failed: {str(e)}"
            log.error(f"Schedule {sched_id} failed: {e}")
            with _notif_lock:
                notifications = _load_notifications()
                for n in notifications:
                    if n["id"] == notif_id:
                        n["body"] = error_msg
                        n["status"] = "error"
                        break
                _write_notifications(notifications)
            _append_schedule_run_log(sched_id, title, "error", error_msg)

    # Save a pending notification immediately
    pending_notif = {
        "id": notif_id,
        "type": "task_report",
        "title": title,
        "body": "Task is running...",
        "status": "running",
        "created": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "read": False,
        "schedule_id": sched_id
    }
    with _notif_lock:
        notifications = _load_notifications()
        notifications.insert(0, pending_notif)
        _write_notifications(notifications)

    # Launch background execution
    thread = threading.Thread(target=_execute_task, daemon=True)
    thread.start()

    return {"status": "running", "notification_id": notif_id, "id": sched_id, "crew": crew}


@router.get("/api/schedules/history")
async def schedule_history():
    """Return last 50 schedule run log entries."""
    log_path = os.path.join(os.path.expanduser("~"), ".codec", "schedule_runs.log")
    entries = []
    try:
        with open(log_path) as f:
            for line in f.readlines()[-50:]:
                entries.append({"line": line.strip()})
    except Exception:
        pass
    return entries
