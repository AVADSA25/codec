"""
CODEC Pilot — Phase 3: HTTP Runner
=====================================

FastAPI server on port 8094 exposing the Pilot browser over HTTP.
Consumed by the Phase-4 agent loop and accessible from the Cloudflare
tunnel at pilot.lucyvpa.com.

Endpoints
---------
GET  /health                  liveness probe
GET  /screenshot              current JPEG frame (binary)
GET  /snapshot                indexed-DOM snapshot (JSON + text)
POST /navigate                navigate to URL
POST /click/{index}           click element by [N] index
POST /type/{index}            type text into element by [N] index
POST /run                     start a screencast-recorded run
GET  /run/{run_id}/status     run status + latest snapshot
GET  /run/{run_id}/manifest   screencast frame manifest

Start
-----
    python3 -m pilot.pilot_runner          # headed
    HEADLESS=1 python3 -m pilot.pilot_runner
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .config import PILOT_API_PORT, PILOT_API_HOST, MJPEG_MAX_CONSECUTIVE_FAILURES
from .pilot_chrome import PilotChrome, pilot_session
from .snapshot import take_snapshot, render_for_llm
from .screencast import Screencast
from .hitl import HitlController
from .pilot_agent import AgentRun, AgentStep
from .trace import save_trace, load_trace, list_traces
from .replay import Replayer
from .compiler import compile_skill, compile_to_pending
from .skill_review import (
    save_pending, list_pending, list_active,
    get_pending, approve_pending, reject_pending, slugify,
)

# ─── State ────────────────────────────────────────────────────────────────────

_pilot: Optional[PilotChrome] = None
_runs: dict[str, dict[str, Any]] = {}            # run_id → run state
_hitl: dict[str, HitlController] = {}            # run_id → HitlController
_bg_tasks: set[asyncio.Task] = set()             # keep refs to prevent GC
_lock = asyncio.Lock()
# P-9: the run_id currently driving the single shared browser (None = idle). Only
# one autonomous run at a time — otherwise two runs interleave navigate/click on
# the same page and corrupt each other.
_executing: Optional[str] = None
_MAX_RUNS = 50  # P-14: bound the in-memory _runs dict


def _assert_run_slot_free(run_id: str) -> None:
    """P-9: refuse to start an autonomous run while another is executing on the
    shared browser. Same run_id (a restart) is allowed."""
    if _executing is not None and _executing != run_id and _executing in _runs:
        raise HTTPException(
            409, f"run {_executing} is already executing on the shared browser; "
                 f"wait for it to finish")


def _evict_old_runs(cap: int = _MAX_RUNS) -> None:
    """P-14: drop the oldest runs (by started_at) so _runs can't grow unbounded."""
    if len(_runs) <= cap:
        return
    ordered = sorted(_runs.items(), key=lambda kv: kv[1].get("started_at", 0))
    for rid, _ in ordered[: len(_runs) - cap]:
        _runs.pop(rid, None)

# Manual-record state: when set, navigate/click/type endpoints append
# their actions to this AgentRun. Only one record session at a time.
_recording: Optional[AgentRun] = None


# ─── App lifecycle ────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pilot
    headless = os.environ.get("HEADLESS", "1") == "1"
    _pilot = PilotChrome(headless=headless)  # P-8: randomized CDP port (was fixed CDP_PORT)
    await _pilot.start()
    yield
    await _pilot.stop()
    _pilot = None


app = FastAPI(title="CODEC Pilot Runner", version="3.0.0", lifespan=lifespan)


# ── PP-1 (audit P-1): shared-token auth on every route ────────────────────────
# The :8094 control plane was unauthenticated (it could drive the logged-in
# browser + compile/approve skills → RCE) and reachable via the public Cloudflare
# tunnel. Every request must now present `x-pilot-token` matching a secret shared
# with the parent skill via ~/.codec/pilot_token (0600, auto-bootstrapped). The
# token check covers the tunnel too (loopback bind alone does not, since
# cloudflared connects from localhost).
def _pilot_token_path() -> Path:
    return Path(os.path.expanduser("~/.codec/pilot_token"))


def _load_or_create_pilot_token() -> str:
    p = _pilot_token_path()
    try:
        if p.exists():
            tok = p.read_text().strip()
            if tok:
                return tok
        p.parent.mkdir(parents=True, exist_ok=True)
        tok = secrets.token_urlsafe(32)
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(tok)
        return tok
    except Exception:
        # Fail CLOSED: empty token → no caller can match → all requests 401.
        return ""


_PILOT_TOKEN = _load_or_create_pilot_token()


@app.middleware("http")
async def _require_token(request, call_next):
    presented = request.headers.get("x-pilot-token", "")
    if not _PILOT_TOKEN or not presented or not hmac.compare_digest(presented, _PILOT_TOKEN):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)


# CORS: localhost only (was "*"). Browser pages can't script cross-origin reads of
# the API; the parent skill is server-side (no CORS) and sends the token directly.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _require_pilot() -> PilotChrome:
    if _pilot is None:
        raise HTTPException(503, "Pilot browser not ready")
    return _pilot


# ─── Request / response models ────────────────────────────────────────────────

class NavigateRequest(BaseModel):
    url: str
    wait_until: str = "domcontentloaded"


class TypeRequest(BaseModel):
    text: str


class RunRequest(BaseModel):
    task: str           # natural-language task description (used in Phase 4)
    fps: float = 2.0    # screencast frame rate
    tag: str = ""       # optional human-readable label


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    pilot = _require_pilot()
    return {
        "status": "ok",
        "url": pilot.page.url if pilot._page else None,
        "cdp_port": pilot.cdp_port,
    }


@app.get("/screenshot")
async def screenshot():
    """Return current viewport as JPEG bytes."""
    pilot = _require_pilot()
    data = await pilot.screenshot(quality=80)
    return Response(content=data, media_type="image/jpeg")


async def _mjpeg_frames(get_pilot, *, max_consecutive_failures=None, sleep_s: float = 0.25):
    """Yield MJPEG multipart frames from the live browser viewport.

    PP-12 (audit P-14): a dead browser used to spin this loop forever — every
    `screenshot()` raised, the bare `except` swallowed it, and the stream never closed.
    Now consecutive screenshot failures are bounded: after `max_consecutive_failures`
    in a row the generator returns, closing the stream so the client reconnects against
    a healthy state. A successful frame resets the counter, so transient blips don't
    tear down a working feed. A `None` pilot (stream opened before a run starts) is a
    benign wait, not a failure — it doesn't count toward the bound.
    """
    boundary = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
    limit = (max_consecutive_failures if max_consecutive_failures is not None
             else MJPEG_MAX_CONSECUTIVE_FAILURES)
    failures = 0
    while True:
        p = get_pilot()
        if p is None:
            await asyncio.sleep(sleep_s)
            continue
        try:
            data = await p.screenshot(quality=70)
            failures = 0
            yield boundary + data + b"\r\n"
        except Exception:
            failures += 1
            if failures >= limit:
                return  # close the stream — client reconnects against a healthy state
        await asyncio.sleep(sleep_s)


@app.get("/screenshot/stream")
async def screenshot_stream():
    """MJPEG stream of the browser viewport (~4 fps). Use as <img src=…> for a live feed."""
    return StreamingResponse(
        _mjpeg_frames(lambda: _pilot),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/screenshot/base64")
async def screenshot_b64():
    """Return current viewport as base64-encoded JPEG (for JSON consumers)."""
    pilot = _require_pilot()
    data = await pilot.screenshot(quality=80)
    return {"image": base64.b64encode(data).decode()}


@app.get("/snapshot")
async def snapshot():
    """Return current indexed-DOM snapshot as JSON + rendered text."""
    pilot = _require_pilot()
    snap = await take_snapshot(pilot.page)
    return {
        "url": snap.url,
        "title": snap.title,
        "viewport": snap.viewport,
        "element_count": len(snap),
        "took_ms": snap.took_ms,
        "rendered": render_for_llm(snap),
        "elements": [
            {
                "index": el.index,
                "role":  el.role,
                "name":  el.name,
                "xpath": el.xpath,
                "css_sel": el.css_sel,
                "bbox":  el.bbox,
                "attrs": el.attrs,
            }
            for el in snap.elements
        ],
    }


def _record_step(action: dict, snap_text: str, el=None, result: str = "") -> None:
    """If a manual-record session is active, append this action to its trace."""
    if _recording is None:
        return
    step_idx = len(_recording.steps) + 1
    step = AgentStep(
        step=step_idx,
        action=action,
        snapshot_before=snap_text,
        result=result,
        target_xpath=el.xpath  if el else None,
        target_css=el.css_sel  if el else None,
        target_name=el.name    if el else None,
        target_role=el.role    if el else None,
    )
    _recording.steps.append(step)


def _normalize_url(url: str) -> str:
    """Prepend https:// when the caller sends a bare domain (F8, 2026-07-03).

    The Pilot UI normalizes before sending, but API callers (pilot skill,
    MCP, curl) hitting /navigate with "example.com" got a 500 from the
    browser driver. Scheme-relative (//host) and explicit schemes pass
    through untouched; about:/data: etc. are left alone."""
    u = (url or "").strip()
    if not u:
        return u
    if "://" in u or u.startswith(("about:", "data:", "chrome:", "//")):
        return u
    return "https://" + u


@app.post("/navigate")
async def navigate(req: NavigateRequest):
    pilot = _require_pilot()
    req.url = _normalize_url(req.url)
    await pilot.navigate(req.url, wait_until=req.wait_until)
    snap = await take_snapshot(pilot.page)
    _record_step(
        {"action": "navigate", "url": req.url},
        render_for_llm(snap),
        result=f"navigated to {req.url}",
    )
    return {
        "url": pilot.page.url,
        "title": await pilot.page.title(),
        "element_count": len(snap),
        "recording": _recording.run_id if _recording else None,
    }


@app.post("/click/{index}")
async def click_element(index: int):
    """Click the element with the given [N] snapshot index."""
    pilot = _require_pilot()
    snap = await take_snapshot(pilot.page)
    matches = [el for el in snap.elements if el.index == index]
    if not matches:
        raise HTTPException(404, f"Element [{index}] not found in current snapshot")
    el = matches[0]
    await pilot.click_xpath(el.xpath)
    _record_step(
        {"action": "click", "index": index},
        render_for_llm(snap), el=el,
        result=f"clicked [{index}] {el.role} '{el.name}'",
    )
    return {"clicked": str(el), "xpath": el.xpath,
            "recording": _recording.run_id if _recording else None}


@app.post("/type/{index}")
async def type_element(index: int, req: TypeRequest):
    """Type text into the element with the given [N] snapshot index."""
    pilot = _require_pilot()
    snap = await take_snapshot(pilot.page)
    matches = [el for el in snap.elements if el.index == index]
    if not matches:
        raise HTTPException(404, f"Element [{index}] not found in current snapshot")
    el = matches[0]
    await pilot.type_xpath(el.xpath, req.text)
    _record_step(
        {"action": "type", "index": index, "text": req.text},
        render_for_llm(snap), el=el,
        result=f"typed into [{index}] {el.role} '{el.name}'",
    )
    return {"typed_into": str(el), "text": req.text,
            "recording": _recording.run_id if _recording else None}


@app.post("/run")
async def start_run(req: RunRequest):
    """
    Start a screencast-recorded run.  Returns run_id immediately.
    Phase-4 agent loop will drive the actual browser actions and
    PUT /run/{run_id}/action to record each step.
    """
    pilot = _require_pilot()
    run_id = uuid.uuid4().hex[:12]
    snap = await take_snapshot(pilot.page)

    _runs[run_id] = {
        "run_id": run_id,
        "task": req.task,
        "tag": req.tag,
        "status": "running",
        "started_at": time.time(),
        "fps": req.fps,
        "steps": [],
        "latest_snapshot": render_for_llm(snap),
        "error": None,
    }
    _evict_old_runs()  # P-14: bound in-memory run history

    return {"run_id": run_id, "status": "running"}


@app.get("/run/{run_id}/status")
async def run_status(run_id: str):
    if run_id not in _runs:
        raise HTTPException(404, f"Run {run_id} not found")
    return _runs[run_id]


@app.post("/run/{run_id}/step")
async def record_step(run_id: str, step: dict):
    """Record a step taken by the Phase-4 agent loop."""
    if run_id not in _runs:
        raise HTTPException(404, f"Run {run_id} not found")
    run = _runs[run_id]
    step["ts"] = time.time()
    step["step_index"] = len(run["steps"])
    run["steps"].append(step)
    # Refresh snapshot after step
    pilot = _require_pilot()
    snap = await take_snapshot(pilot.page)
    run["latest_snapshot"] = render_for_llm(snap)
    return {"step_index": step["step_index"], "element_count": len(snap)}


@app.post("/run/{run_id}/complete")
async def complete_run(run_id: str, result: dict = {}):
    """Mark a run as complete."""
    if run_id not in _runs:
        raise HTTPException(404, f"Run {run_id} not found")
    run = _runs[run_id]
    run["status"] = result.get("status", "done")
    run["ended_at"] = time.time()
    run["error"] = result.get("error")
    return run


@app.get("/runs")
async def list_runs():
    """List all recorded runs (most recent first)."""
    runs = sorted(_runs.values(), key=lambda r: r["started_at"], reverse=True)
    return {"runs": runs[:50]}


# ─── Background agent execution ───────────────────────────────────────────────

@app.post("/run/{run_id}/start")
async def start_agent_execution(run_id: str, body: dict = {}):
    """
    Kick off PilotAgent.execute() as a background asyncio task.
    The run must already exist (created via POST /run).
    Results are stored back into _runs[run_id] when complete.
    """
    if run_id not in _runs:
        raise HTTPException(404, f"Run {run_id} not found — call POST /run first")
    _assert_run_slot_free(run_id)  # P-9: one autonomous run on the shared browser
    pilot = _require_pilot()
    run_data = _runs[run_id]
    from .audit import audit  # P-12: forensic trail
    audit("run_started", run_id=run_id, task=str(run_data.get("task", ""))[:120])

    step_budget = body.get("step_budget", 20)
    use_stub    = body.get("use_stub", False)

    # Create HITL controller (gives pause/resume/inject for free)
    ctrl = HitlController(
        pilot,
        task=run_data["task"],
        run_id=run_id,
        step_budget=step_budget,
        use_stub=use_stub,
    )
    _hitl[run_id] = ctrl

    async def _bg_run():
        global _executing
        try:
            agent_run = await ctrl.execute()
            _runs[run_id].update({
                "status":          agent_run.status,
                "result":          agent_run.result,
                "error":           agent_run.error,
                "ended_at":        agent_run.ended_at,
                "steps":           agent_run.to_dict()["steps"],
                "latest_snapshot": render_for_llm(
                    await take_snapshot(pilot.page)
                ),
            })
            # Save trace to disk
            from .trace import save_trace
            save_trace(agent_run)
        except Exception as exc:
            _runs[run_id].update({"status": "error", "error": str(exc)})
        finally:
            _executing = None  # P-9: free the shared-browser slot

    global _executing
    _executing = run_id  # P-9: claim the shared-browser slot
    task = asyncio.create_task(_bg_run())
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)

    return {"run_id": run_id, "status": "executing", "step_budget": step_budget}


# ─── HITL endpoints ───────────────────────────────────────────────────────────

@app.get("/hitl/{run_id}/status")
async def hitl_status(run_id: str):
    if run_id not in _hitl:
        raise HTTPException(404, f"No HITL controller for run {run_id}")
    return _hitl[run_id].status()


@app.post("/hitl/{run_id}/pause")
async def hitl_pause(run_id: str, body: dict = {}):
    if run_id not in _hitl:
        raise HTTPException(404, f"No HITL controller for run {run_id}")
    await _hitl[run_id].pause(body.get("reason", "user request"))
    return _hitl[run_id].status()


@app.post("/hitl/{run_id}/resume")
async def hitl_resume(run_id: str):
    if run_id not in _hitl:
        raise HTTPException(404, f"No HITL controller for run {run_id}")
    await _hitl[run_id].resume()
    return _hitl[run_id].status()


@app.post("/hitl/{run_id}/inject")
async def hitl_inject(run_id: str, action: dict):
    if run_id not in _hitl:
        raise HTTPException(404, f"No HITL controller for run {run_id}")
    await _hitl[run_id].inject(action)
    return {"injected": action, "queue_size": _hitl[run_id]._inject_queue.qsize() + 1}


@app.post("/hitl/{run_id}/takeover")
async def hitl_takeover(run_id: str):
    if run_id not in _hitl:
        raise HTTPException(404, f"No HITL controller for run {run_id}")
    snap_text = await _hitl[run_id].takeover()
    return {"human_in_control": True, "snapshot": snap_text}


@app.post("/hitl/{run_id}/handback")
async def hitl_handback(run_id: str):
    if run_id not in _hitl:
        raise HTTPException(404, f"No HITL controller for run {run_id}")
    await _hitl[run_id].handback()
    return _hitl[run_id].status()


# ─── Manual record mode ──────────────────────────────────────────────────────
#
# Pattern: user clicks "Record" → /record/start creates an empty AgentRun and
# marks the runner as "recording".  All subsequent /navigate /click /type
# calls append into that run's trace via _record_step().  /record/stop saves
# the trace and (optionally) auto-compiles into a pending skill.

class RecordStartRequest(BaseModel):
    task: str = ""        # human-readable description (defaults to "Manual recording")
    tag:  str = ""


@app.post("/record/start")
async def record_start(req: RecordStartRequest):
    """Start a manual-record session. Only one at a time."""
    global _recording
    if _recording is not None:
        raise HTTPException(409, f"Already recording: {_recording.run_id}")

    run_id = uuid.uuid4().hex[:12]
    task   = req.task.strip() or "Manual recording"
    _recording = AgentRun(task=task, run_id=run_id, status="recording")
    _recording.started_at = time.time()

    # Mirror into _runs so the dashboard's Recent Runs picks it up.
    _runs[run_id] = {
        "run_id":     run_id,
        "task":       task,
        "tag":        req.tag or "manual",
        "status":     "recording",
        "started_at": _recording.started_at,
        "fps":        0.0,
        "steps":      [],
        "latest_snapshot": "",
        "error":      None,
        "manual":     True,
    }
    return {"run_id": run_id, "status": "recording", "task": task}


@app.get("/record/status")
async def record_status():
    """Return current recording state."""
    if _recording is None:
        return {"recording": False}
    return {
        "recording": True,
        "run_id":    _recording.run_id,
        "task":      _recording.task,
        "step_count": len(_recording.steps),
        "started_at": _recording.started_at,
    }


class RecordStopRequest(BaseModel):
    compile_skill: bool = True    # auto-generate pending skill after stop
    result:        str  = ""      # optional human-supplied result text


@app.post("/record/stop")
async def record_stop(req: RecordStopRequest):
    """Finalise the recording: save trace, optionally compile to pending skill."""
    global _recording
    if _recording is None:
        raise HTTPException(404, "No active recording")

    run = _recording
    run.status   = "done"
    run.ended_at = time.time()
    run.result   = req.result or f"Manual recording with {len(run.steps)} steps"

    # Save trace
    try:
        trace_path = save_trace(run)
    except Exception as exc:
        _recording = None
        raise HTTPException(500, f"Failed to save trace: {exc}")

    # Mirror into _runs dict
    if run.run_id in _runs:
        _runs[run.run_id].update({
            "status":   "done",
            "result":   run.result,
            "ended_at": run.ended_at,
            "steps":    run.to_dict()["steps"],
        })

    # Auto-compile to pending skill
    pending_path = None
    if req.compile_skill and run.steps:
        try:
            pending_path = compile_to_pending(run)
        except Exception as exc:
            pending_path = None

    _recording = None
    return {
        "run_id":       run.run_id,
        "trace_path":   str(trace_path),
        "step_count":   len(run.steps),
        "pending_skill": str(pending_path) if pending_path else None,
    }


# ─── Replay endpoints ────────────────────────────────────────────────────────

class ReplayRequest(BaseModel):
    allow_llm_rescue: bool = True


@app.post("/run/{run_id}/replay")
async def replay_run(run_id: str, req: ReplayRequest = ReplayRequest()):
    """Replay a saved trace through the Replayer fallback ladder."""
    pilot = _require_pilot()
    try:
        run = load_trace(run_id)
    except FileNotFoundError:
        raise HTTPException(404, f"No trace for run {run_id}")

    replayer = Replayer(pilot, allow_llm_rescue=req.allow_llm_rescue)
    result   = await replayer.replay(run)
    return result.to_dict()


# ─── Skill compile + review endpoints ────────────────────────────────────────

@app.post("/run/{run_id}/compile")
async def compile_run(run_id: str):
    """Compile a saved trace into a pending skill awaiting user approval."""
    try:
        run = load_trace(run_id)
    except FileNotFoundError:
        raise HTTPException(404, f"No trace for run {run_id}")

    try:
        path = compile_to_pending(run)
    except Exception as exc:
        raise HTTPException(500, f"Compile failed: {exc}")

    return {
        "run_id":       run_id,
        "pending_path": str(path),
        "filename":     path.name,
        "slug":         path.stem.replace("pilot_", "", 1),
    }


@app.get("/skills/pending")
async def skills_pending():
    return {"pending": list_pending()}


@app.get("/skills/active")
async def skills_active():
    return {"active": list_active()}


@app.get("/skills/pending/{slug}")
async def skills_pending_get(slug: str):
    rec = get_pending(slug)
    if not rec:
        raise HTTPException(404, f"No pending skill: {slug}")
    return rec


@app.post("/skills/pending/{slug}/approve")
async def skills_pending_approve(slug: str):
    try:
        path = approve_pending(slug)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    return {"approved": True, "path": str(path), "filename": path.name}


@app.post("/skills/pending/{slug}/reject")
async def skills_pending_reject(slug: str):
    deleted = reject_pending(slug)
    if not deleted:
        raise HTTPException(404, f"No pending skill matches '{slug}'")
    return {"rejected": True, "slug": slug}


# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "pilot.pilot_runner:app",
        host=PILOT_API_HOST,  # PP-1: loopback by default (was 0.0.0.0)
        port=PILOT_API_PORT,
        reload=False,
        log_level="info",
    )
