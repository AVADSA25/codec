"""CODEC Phase 3 Step 9 — Background Execution + Permission Gate.

PM2-managed daemon `codec-agent-runner` that picks up status=approved
plans (from Step 8), executes their checkpoints autonomously via
Qwen-3.6 ↔ skill loops, enforces the permission manifest, persists
state for resume-after-restart.

Reuses:
  - codec_audit (Step 1) for paired-cid envelope
  - codec_dispatch.run_skill (Step 2 plugin hooks fire automatically)
  - codec_ask_user (Step 3) for outside-manifest grant prompts
  - codec_ask_user.strict_consent (Step 3 §1.7) for destructive ops
  - codec_dashboard._StepBudget (Step 3) for per-checkpoint cap
  - codec_agent_plan (Step 8) for plan/state/manifest/grants R/W

See docs/PHASE3-BLUEPRINT.md §3 for design rationale.
"""
from __future__ import annotations

import fnmatch
import json
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("codec_agent_runner")

# ── Audit event constants (mirror codec_audit) ────────────────────────────────
try:
    from codec_audit import (
        AGENT_STARTED, AGENT_CHECKPOINT_STARTED, AGENT_CHECKPOINT_COMPLETED,
        AGENT_PAUSED, AGENT_RESUMED, AGENT_BLOCKED_ON_PERMISSION,
        AGENT_COMPLETED, AGENT_ABORTED,
    )
except ImportError:
    AGENT_STARTED = "agent_started"
    AGENT_CHECKPOINT_STARTED = "agent_checkpoint_started"
    AGENT_CHECKPOINT_COMPLETED = "agent_checkpoint_completed"
    AGENT_PAUSED = "agent_paused"
    AGENT_RESUMED = "agent_resumed"
    AGENT_BLOCKED_ON_PERMISSION = "agent_blocked_on_permission"
    AGENT_COMPLETED = "agent_completed"
    AGENT_ABORTED = "agent_aborted"


# ── Configurable knobs (overridable for tests) ────────────────────────────────
DAEMON_TICK_SECONDS = 5
DEFAULT_MAX_CONCURRENT = 3
DESTRUCTIVE_CONSENT_TIMEOUT_S = 600  # Step 3 §1.7 default — overnight = block, not abort


# ── Action dataclass ──────────────────────────────────────────────────────────
@dataclass
class Action:
    """One proposed step in a checkpoint loop. Returned by
    Qwen-3.6's next-action driver, evaluated by permission_gate,
    executed via codec_dispatch.run_skill.

    Phase 3.5 review M4: `reads_path` + `read_path` added for symmetric
    read/write gating. `touches_path`/`path` is the write side."""
    skill: str
    task: str
    is_destructive: bool = False
    network_call: bool = False
    network_domain: str = ""
    touches_path: bool = False
    path: str = ""
    reads_path: bool = False        # Phase 3.5 review M4: read enforcement
    read_path: str = ""             # Phase 3.5 review M4
    kind: str = "skill_call"   # "skill_call" | "checkpoint_done"


# ── PermissionViolation ───────────────────────────────────────────────────────
class PermissionViolation(Exception):
    """An Action references something outside the union of per-agent
    grants + global allowlist. Caught by _run_agent and translated
    to status=blocked_on_permission + ask_user notification."""

    def __init__(self, reason: str, needed: str, message: str = ""):
        self.reason = reason
        self.needed = needed
        super().__init__(message or f"{reason}: {needed}")


# ── Permission gate ───────────────────────────────────────────────────────────
def permission_gate(action: Action, agent_grants: Dict[str, Any],
                    global_grants: Dict[str, Any]) -> None:
    """The core Step 9 enforcement. Walks the action's resource use,
    checks the union of per-agent grants and global allowlist. Raises
    PermissionViolation on any gap.

    Note: destructive ops fall through to strict_consent_gate (Step 3
    §1.7) — even if pre-approved by the user. That's the universal
    floor; permission_gate alone is not enough.
    """
    skills = set(agent_grants.get("skills", [])) | set(global_grants.get("skills", []))
    if action.skill not in skills:
        raise PermissionViolation("skill_not_authorized", action.skill)

    # Phase 3.5 review M4: symmetric read/write gating now active.
    # `touches_path` = write; `reads_path` = read. Both checked against
    # respective manifest entries. Note: skill-internal reads (where the
    # skill itself opens files without going through Action) still bypass
    # the runner — that's a fundamental limitation of the dispatch model.
    if action.touches_path:
        write_paths = (set(agent_grants.get("write_paths", [])) |
                       set(global_grants.get("write_paths", [])))
        # fnmatch supports glob patterns the LLM puts in manifest.
        # Expand ~ on both sides so "~/Documents/x" matches "~/Documents/**".
        action_path_abs = os.path.expanduser(action.path)
        ok = any(fnmatch.fnmatch(action_path_abs, os.path.expanduser(p))
                 for p in write_paths)
        if not ok:
            raise PermissionViolation("path_not_authorized", action.path)

    if action.reads_path and action.read_path:
        read_paths = (set(agent_grants.get("read_paths", [])) |
                      set(global_grants.get("read_paths", [])))
        action_read_abs = os.path.expanduser(action.read_path)
        ok = any(fnmatch.fnmatch(action_read_abs, os.path.expanduser(p))
                 for p in read_paths)
        if not ok:
            raise PermissionViolation("read_path_not_authorized", action.read_path)

    if action.network_call:
        domains = (set(agent_grants.get("network_domains", [])) |
                   set(global_grants.get("network_domains", [])))
        if action.network_domain not in domains:
            raise PermissionViolation("domain_not_authorized", action.network_domain)


# ── Qwen-3.6 client (mirrors codec_agent_plan pattern) ────────────────────────
QWEN_URL = "http://127.0.0.1:8090/v1/chat/completions"
QWEN_MODEL = "qwen3.6"
QWEN_TIMEOUT = 60


class QwenUnavailableError(RuntimeError):
    """Qwen-3.6 service down or unreachable."""


_NEXT_ACTION_SYSTEM_PROMPT = """You are CODEC's autonomous agent runtime. \
Given a plan, current checkpoint, and recent action history, decide the SINGLE \
next action to take. Return ONLY a JSON object with one of these shapes:

For a skill call:
{
  "kind": "skill_call",
  "skill": "<skill_name from plan.permission_manifest.skills>",
  "task": "<the natural-language task to pass to that skill>",
  "is_destructive": <bool — true for irreversible ops: file delete, payments, send-on-behalf>,
  "network_call": <bool — true if the skill will make HTTP requests>,
  "network_domain": "<domain if network_call=true, else empty>",
  "touches_path": <bool — true if the skill WRITES to a filesystem path>,
  "path": "<path if touches_path=true, else empty>",
  "reads_path": <bool — true if the skill READS a filesystem path>,
  "read_path": "<path if reads_path=true, else empty>"
}

For checkpoint completion:
{"kind": "checkpoint_done"}

Rules:
- skill MUST be in plan.permission_manifest.skills.
- If you need a skill that's NOT in the manifest, return is_destructive=false and pick the closest available; the runtime will block via permission_gate, escalate to user, and re-call you with the same context.
- read_path is checked against permission_manifest.read_paths; write path against write_paths. They are independent — a single action can both read and write (e.g. read template.md, write output.md).
- Output ONLY the JSON. No prose.
"""


def _qwen_chat(user_prompt: str, system_prompt: str = "",
               max_tokens: int = 2000) -> str:
    """Local Qwen-3.6 OpenAI-compatible call. Same shape as
    codec_agent_plan._qwen_chat — keep them parallel."""
    import requests
    payload = {
        "model": QWEN_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt or ""},
            {"role": "user",   "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    try:
        r = requests.post(QWEN_URL, json=payload, timeout=QWEN_TIMEOUT)
    except requests.exceptions.ConnectionError as e:
        raise QwenUnavailableError(f"qwen3.6 unreachable: {e}")
    except requests.exceptions.Timeout:
        raise QwenUnavailableError("qwen3.6 request timed out")
    if r.status_code != 200:
        raise QwenUnavailableError(f"qwen3.6 returned {r.status_code}")
    try:
        return r.json()["choices"][0]["message"]["content"]
    except (KeyError, json.JSONDecodeError) as e:
        raise QwenUnavailableError(f"qwen3.6 returned malformed response: {e}")


def _qwen_next_action(plan_dict: Dict[str, Any], checkpoint: Dict[str, Any],
                     history: List[Dict[str, Any]],
                     max_history: int = 10) -> Action:
    """Compose the next-action prompt, call Qwen, parse the JSON
    response into an Action. Raises QwenUnavailableError or
    ValueError on bad JSON shape."""
    recent = history[-max_history:] if history else []
    user_prompt = (
        f"Plan goals: {plan_dict.get('goals')}\n\n"
        f"Current checkpoint:\n"
        f"  title: {checkpoint['title']}\n"
        f"  description: {checkpoint['description']}\n"
        f"  expected_output: {checkpoint['expected_output']}\n\n"
        f"Recent action history (last {len(recent)} steps):\n"
        f"{json.dumps(recent, indent=2)}\n\n"
        f"What's the next action? Output JSON now."
    )

    raw = _qwen_chat(user_prompt, _NEXT_ACTION_SYSTEM_PROMPT).strip()
    if raw.startswith("```"):
        # Strip ```json ... ``` fences
        import re as _re
        raw = _re.sub(r"^```(?:json)?\s*", "", raw)
        raw = _re.sub(r"\s*```\s*$", "", raw)

    try:
        d = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"qwen returned non-JSON next-action: {e}; raw={raw[:200]!r}")

    kind = d.get("kind", "skill_call")
    if kind == "checkpoint_done":
        return Action(skill="", task="", kind="checkpoint_done")

    return Action(
        skill=str(d.get("skill", "")),
        task=str(d.get("task", "")),
        is_destructive=bool(d.get("is_destructive", False)),
        network_call=bool(d.get("network_call", False)),
        network_domain=str(d.get("network_domain", "")),
        touches_path=bool(d.get("touches_path", False)),
        path=str(d.get("path", "")),
        reads_path=bool(d.get("reads_path", False)),    # Phase 3.5 review M4
        read_path=str(d.get("read_path", "")),          # Phase 3.5 review M4
        kind="skill_call",
    )


@dataclass
class ConsentResult:
    """Outcome of strict-consent gate for a destructive op."""
    approved: bool = False
    timed_out: bool = False
    user_response: str = ""


def _strict_consent(action: Action, deadline: int = DESTRUCTIVE_CONSENT_TIMEOUT_S) -> ConsentResult:
    """Lazy-imported codec_ask_user.strict_consent_gate wrapper.
    Returns ConsentResult. Reuses Phase 1 Step 3 §1.7 verb-match
    enforcement — generic 'yes' is rejected, two-strike timeout
    in <2s emits ambiguous_consent."""
    try:
        from codec_ask_user import strict_consent_gate
    except Exception as e:
        log.warning("codec_ask_user.strict_consent_gate unavailable: %s", e)
        return ConsentResult(approved=False, timed_out=True,
                              user_response="ask_user_unavailable")

    question = (
        f"⚠️ Destructive op requested by agent: {action.skill}: {action.task[:80]}\n\n"
        f"To approve, type the literal verb: 'delete' / 'send' / 'pay' / 'authorize' "
        f"(matching the operation). Generic 'yes' will be rejected."
    )
    result = strict_consent_gate(question, deadline_seconds=deadline,
                                  source=f"agent_runner")
    return ConsentResult(
        approved=getattr(result, "approved", False),
        timed_out=getattr(result, "timed_out", False),
        user_response=getattr(result, "user_response", ""),
    )


def _enforce_destructive_gate(action: Action,
                              deadline: int = DESTRUCTIVE_CONSENT_TIMEOUT_S) -> ConsentResult:
    """Called by checkpoint executor for any action where
    is_destructive=True. Returns ConsentResult; caller decides
    aborted vs blocked based on `timed_out` flag (Q7)."""
    if not action.is_destructive:
        return ConsentResult(approved=True, timed_out=False)
    return _strict_consent(action, deadline)


class DestructiveOpRejected(Exception):
    """User explicitly rejected a destructive op via strict-consent."""


class StepBudgetExhausted(Exception):
    """Per-checkpoint step budget cap reached without checkpoint_done."""


def _run_skill(skill_name: str, task: str, agent_id: str) -> str:
    """Lazy-imported codec_dispatch.run_skill. Step 1+2 hooks fire
    automatically inside run_skill via run_with_hooks."""
    try:
        from codec_dispatch import run_skill, registry
    except Exception as e:
        raise RuntimeError(f"codec_dispatch unavailable: {e}")
    meta = (registry.get_meta(skill_name) if registry else None) or {}
    skill = {"name": skill_name, "_all_matches": [skill_name], **meta}
    return run_skill(skill, task, app=f"agent:{agent_id}")


def _execute_checkpoint(plan_dict: Dict[str, Any],
                        checkpoint: Dict[str, Any],
                        agent_grants: Dict[str, Any],
                        global_grants: Dict[str, Any],
                        agent_id: str,
                        history: Optional[List[Dict[str, Any]]] = None
                        ) -> List[Dict[str, Any]]:
    """Inner loop: ask Qwen for next action, gate it, execute, append
    to history, repeat until checkpoint_done OR step_budget hit OR
    PermissionViolation OR DestructiveOpRejected raised.

    Returns the final history list. Caller (run_agent) is responsible
    for atomic state save + audit emit on each checkpoint completion.

    Raises:
        PermissionViolation — escalate to status=blocked_on_permission
        DestructiveOpRejected — abort the agent
        StepBudgetExhausted — escalate to status=blocked_on_budget
        QwenUnavailableError — daemon retries
    """
    if history is None:
        history = []
    budget = int(checkpoint.get("step_budget", 30))

    for step in range(budget):
        action = _qwen_next_action(plan_dict, checkpoint, history)

        if action.kind == "checkpoint_done":
            return history

        # Permission gate (raises PermissionViolation if outside manifest)
        permission_gate(action, agent_grants, global_grants)

        # Destructive gate (raises DestructiveOpRejected on user reject)
        if action.is_destructive:
            consent = _enforce_destructive_gate(action)
            if consent.timed_out:
                # Q7: timeout overnight — caller transitions to blocked_on_destructive
                raise StepBudgetExhausted(
                    "destructive_consent_timeout"  # special marker
                )
            if not consent.approved:
                raise DestructiveOpRejected(
                    f"user rejected: {action.skill} {action.task[:80]}"
                )

        # Execute via codec_dispatch.run_skill (Step 1+2 hooks fire)
        try:
            result = _run_skill(action.skill, action.task, agent_id)
        except Exception as e:
            log.warning("[%s] skill %s raised: %s", agent_id, action.skill, e)
            result = f"<skill_error: {e}>"

        history.append({
            "step": len(history),
            "skill": action.skill,
            "task": action.task[:200],
            "result": (result or "")[:500],
            "is_destructive": action.is_destructive,
        })

    raise StepBudgetExhausted(f"step_budget {budget} exhausted in checkpoint {checkpoint.get('id')}")


def _audit(event: str, source: str = "codec-agent-runner",
           message: str = "", correlation_id: str = "",
           outcome: str = "ok", level: str = "info",
           extra: Optional[Dict[str, Any]] = None) -> None:
    """Lazy-imported audit emit. Centralized for monkeypatching in tests."""
    try:
        from codec_audit import audit
    except Exception as e:
        log.debug("codec_audit unavailable for %s: %s", event, e)
        return
    audit(event=event, source=source, message=message,
          correlation_id=correlation_id, outcome=outcome,
          level=level, extra=dict(extra or {}))


def _atomic_set_status(agent_id: str, new_status: str,
                       reason: Optional[str] = None) -> None:
    """Wrapper that catches InvalidStatusTransition and logs.
    Step 9 sometimes needs to bypass strict transitions on crash recovery
    paths — but always within the documented state graph."""
    try:
        from codec_agent_plan import set_status
        set_status(agent_id, new_status, reason=reason)
    except Exception as e:
        log.warning("[%s] set_status %s failed: %s", agent_id, new_status, e)


def _run_agent(agent_id: str, cid: Optional[str] = None) -> None:
    """The main per-agent thread function. Loads plan + grants,
    verifies plan_hash, walks checkpoints via _execute_checkpoint,
    persists state, emits audit events.

    On any unhandled exception: atomic save status=aborted, log,
    emit agent_aborted. Never propagates exceptions to caller (the
    daemon's thread pool depends on this).

    `cid` lets the daemon's crash-recovery path mint a single correlation_id,
    emit AGENT_RESUMED under it, then chain all of this run's emits to the
    same id (Step 1 §1.4 paired-cid contract). When None, generate fresh.
    """
    from codec_agent_plan import (
        load_plan, load_state, load_manifest, load_grants,
        load_global_grants, save_state, save_manifest,
        compute_plan_hash,
    )
    try:
        from codec_agent_messaging import post_message
    except ImportError:
        post_message = lambda **kw: None  # graceful degradation

    if cid is None:
        cid = secrets.token_hex(6)

    try:
        plan = load_plan(agent_id)
        if plan is None:
            log.warning("[%s] plan missing; aborting", agent_id)
            _atomic_set_status(agent_id, "aborted", reason="plan_missing")
            _audit(AGENT_ABORTED, message=f"plan missing for {agent_id}",
                   correlation_id=cid, outcome="error", level="error",
                   extra={"agent_id": agent_id, "reason": "plan_missing"})
            return

        manifest = load_manifest(agent_id)
        stored_hash = manifest.get("plan_hash", "")
        actual_hash = compute_plan_hash(plan)
        # Q13 (review fix I1): if stored_hash is missing/empty, the plan was
        # never properly approved or someone cleared the hash. Either way:
        # ABORT. The "if stored_hash and ..." pattern silently bypasses
        # tamper detection on hash absence — that's an attack vector.
        if not stored_hash:
            log.warning("[%s] plan_hash absent — refusing to run (never approved or hash tampered)",
                        agent_id)
            _atomic_set_status(agent_id, "aborted", reason="plan_hash_missing")
            _audit(AGENT_ABORTED, message="plan_hash missing",
                   correlation_id=cid, outcome="error", level="error",
                   extra={"agent_id": agent_id, "reason": "plan_hash_missing"})
            return
        if stored_hash != actual_hash:
            log.warning("[%s] plan_hash tamper: stored=%s actual=%s",
                        agent_id, stored_hash[:8], actual_hash[:8])
            _atomic_set_status(agent_id, "aborted", reason="plan_tampered")
            _audit(AGENT_ABORTED, message="plan tampered",
                   correlation_id=cid, outcome="error", level="error",
                   extra={"agent_id": agent_id, "reason": "plan_tampered",
                          "stored_hash": stored_hash[:8], "actual_hash": actual_hash[:8]})
            return

        grants = load_grants(agent_id)
        global_grants = load_global_grants()
        state = load_state(agent_id)
        current_idx = int(state.get("current_checkpoint", 0))

        # Transition approved → running (or any prior state → running for resume)
        _atomic_set_status(agent_id, "running")
        _audit(AGENT_STARTED, message=f"agent started {agent_id}",
               correlation_id=cid,
               extra={"agent_id": agent_id,
                      "checkpoint_count": len(plan.checkpoints),
                      "starting_at": current_idx})
        post_message(agent_id=agent_id, type="agent_update",
                     title=f"Agent started: {manifest.get('title', agent_id)}",
                     body=f"Starting plan execution from checkpoint {current_idx + 1} of {len(plan.checkpoints)}.",
                     actions=[
                         {"label": "Pause", "endpoint": f"/api/agents/{agent_id}/pause"},
                         {"label": "Abort", "endpoint": f"/api/agents/{agent_id}/abort"},
                     ],
                     correlation_id=cid)

        # Walk checkpoints
        history: List[Dict[str, Any]] = []
        # Review fix I2: per-checkpoint step_budget overrides applied on resume
        # after /extend_budget endpoint bumps the cap. Keys are checkpoint IDs.
        budget_overrides = state.get("step_budget_overrides", {}) or {}
        for idx, cp in enumerate(plan.checkpoints):
            if idx < current_idx:
                continue  # resume: skip already-completed checkpoints
            effective_budget = int(budget_overrides.get(cp.id, cp.step_budget))
            cp_dict = {
                "id": cp.id, "title": cp.title, "description": cp.description,
                "skills_needed": cp.skills_needed,
                "expected_output": cp.expected_output,
                "step_budget": effective_budget,
            }

            _audit(AGENT_CHECKPOINT_STARTED,
                   message=f"checkpoint {cp.id} started",
                   correlation_id=cid,
                   extra={"agent_id": agent_id, "checkpoint_id": cp.id,
                          "checkpoint_idx": idx})

            try:
                history = _execute_checkpoint(
                    plan_dict=plan.to_dict(), checkpoint=cp_dict,
                    agent_grants=grants, global_grants=global_grants,
                    agent_id=agent_id, history=history,
                )
            except PermissionViolation as pv:
                _atomic_set_status(agent_id, "blocked_on_permission",
                                   reason=f"{pv.reason}:{pv.needed}")
                _audit(AGENT_BLOCKED_ON_PERMISSION,
                       message=f"blocked: {pv.reason}",
                       correlation_id=cid, outcome="warning", level="warning",
                       extra={"agent_id": agent_id, "checkpoint_id": cp.id,
                              "reason": pv.reason, "needed": pv.needed[:200]})
                post_message(agent_id=agent_id, type="agent_blocked",
                             title=f"Blocked: {pv.reason}",
                             body=f"Agent needs additional permission: `{pv.needed}`. Grant or skip?",
                             actions=[
                                 {"label": "Grant", "endpoint": f"/api/agents/{agent_id}/grant",
                                  "body_hint": {"kind": "<infer from reason>", "value": pv.needed}},
                                 {"label": "Abort", "endpoint": f"/api/agents/{agent_id}/abort"},
                             ],
                             correlation_id=cid)
                return
            except DestructiveOpRejected as e:
                _atomic_set_status(agent_id, "aborted",
                                   reason=f"destructive_rejected:{e}")
                _audit(AGENT_ABORTED, message="destructive op rejected",
                       correlation_id=cid, outcome="warning", level="warning",
                       extra={"agent_id": agent_id, "checkpoint_id": cp.id,
                              "reason": "destructive_rejected"})
                post_message(agent_id=agent_id, type="agent_aborted",
                             title="Aborted: destructive op rejected",
                             body=f"User rejected a destructive operation. Plan halted.",
                             actions=[],
                             correlation_id=cid)
                return
            except StepBudgetExhausted as e:
                # Q7: distinguish "destructive_consent_timeout" from real budget hits
                if "destructive_consent_timeout" in str(e):
                    _atomic_set_status(agent_id, "blocked_on_destructive",
                                       reason="destructive_consent_timeout")
                    _audit(AGENT_PAUSED,
                           message="paused on destructive consent timeout",
                           correlation_id=cid, outcome="warning", level="warning",
                           extra={"agent_id": agent_id, "checkpoint_id": cp.id,
                                  "reason": "destructive_consent_timeout"})
                else:
                    # Review fix I2: real budget hit → paused (not blocked_on_permission).
                    # User can resolve via POST /api/agents/{id}/extend_budget which
                    # writes step_budget_overrides[checkpoint_id] to state.json and
                    # transitions status=paused → running. The plan stays immutable
                    # (plan_hash tamper check remains intact); the override lives in
                    # mutable state.json.
                    _atomic_set_status(agent_id, "paused",
                                       reason="step_budget_exhausted")
                    _audit(AGENT_PAUSED,
                           message="paused on step budget exhaustion",
                           correlation_id=cid, outcome="warning", level="warning",
                           extra={"agent_id": agent_id, "checkpoint_id": cp.id,
                                  "reason": "step_budget_exhausted"})
                return

            # Checkpoint complete: atomic state save (resume guarantee)
            save_state(agent_id, {
                "current_checkpoint": idx + 1,
                "history_len": len(history),
                "last_checkpoint_completed_at": _now_iso_local(),
            })
            _audit(AGENT_CHECKPOINT_COMPLETED,
                   message=f"checkpoint {cp.id} completed",
                   correlation_id=cid,
                   extra={"agent_id": agent_id, "checkpoint_id": cp.id,
                          "checkpoint_idx": idx, "steps_used": len(history)})
            post_message(agent_id=agent_id, type="agent_update",
                         title=f"Checkpoint {idx + 1}/{len(plan.checkpoints)}: {cp.title}",
                         body=f"Completed in {len(history)} step(s). Output: {cp.expected_output[:200]}",
                         actions=[
                             {"label": "Pause", "endpoint": f"/api/agents/{agent_id}/pause"},
                             {"label": "Abort", "endpoint": f"/api/agents/{agent_id}/abort"},
                         ],
                         correlation_id=cid)

        # All checkpoints done
        _atomic_set_status(agent_id, "completed")
        _audit(AGENT_COMPLETED, message=f"agent completed {agent_id}",
               correlation_id=cid,
               extra={"agent_id": agent_id, "total_steps": len(history)})
        post_message(agent_id=agent_id, type="agent_done",
                     title=f"Done: {manifest.get('title', agent_id)}",
                     body=f"Plan complete. {len(history)} total steps across {len(plan.checkpoints)} checkpoints.",
                     actions=[
                         {"label": "View artifacts",
                          "endpoint": f"/api/agents/{agent_id}/artifacts"},
                     ],
                     correlation_id=cid)

    except QwenUnavailableError as e:
        # Phase 3.5 review fix C2: dedicated `blocked_on_qwen` status.
        # Distinct from blocked_on_permission — no permission to grant; the
        # LLM service is just down. The daemon auto-resumes on next tick
        # when Qwen comes back online (see _daemon_one_tick blocked_on_qwen
        # branch). Audit emit still uses AGENT_BLOCKED_ON_PERMISSION with
        # reason="qwen_unavailable" since we don't add a new audit constant
        # for this — the status is enough to disambiguate.
        log.warning("[%s] qwen unavailable: %s", agent_id, e)
        _atomic_set_status(agent_id, "blocked_on_qwen",
                           reason=f"qwen_unavailable:{e}")
        _audit(AGENT_BLOCKED_ON_PERMISSION,
               message=f"qwen unavailable: {e}",
               correlation_id=cid, outcome="warning", level="warning",
               extra={"agent_id": agent_id, "reason": "qwen_unavailable",
                      "status": "blocked_on_qwen"})
    except Exception as e:
        log.exception("[%s] unhandled exception in _run_agent", agent_id)
        _atomic_set_status(agent_id, "aborted",
                           reason=f"unhandled:{type(e).__name__}:{str(e)[:100]}")
        _audit(AGENT_ABORTED, message=f"unhandled: {e}",
               correlation_id=cid, outcome="error", level="error",
               extra={"agent_id": agent_id, "reason": "unhandled_exception"})


def _now_iso_local() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Daemon state (module-global) ──────────────────────────────────────────────
MAX_CONCURRENT = int(os.environ.get("AGENT_RUNNER_MAX_CONCURRENT", DEFAULT_MAX_CONCURRENT))
_active_threads: Dict[str, threading.Thread] = {}
_threads_lock = threading.Lock()


def _scan_agents() -> List[Dict[str, Any]]:
    """Walk ~/.codec/agents/*, return manifest dicts. Skips dirs without manifest.json."""
    from codec_agent_plan import _AGENTS_DIR, load_manifest
    out: List[Dict[str, Any]] = []
    if not _AGENTS_DIR.exists():
        return out
    for d in sorted(_AGENTS_DIR.iterdir()):
        if not d.is_dir():
            continue
        m = load_manifest(d.name)
        if m:
            out.append(m)
    return out


def _occupied_slots() -> int:
    """Count active threads + agents in any blocked_* state (Q8 — they
    occupy a slot). Note: completed/aborted/rejected don't occupy.

    Review fix I3: dedupe so an agent counted as `active_thread` is NOT
    also counted as `blocked_*` if its status was just transitioned but
    the thread hasn't been reaped yet."""
    with _threads_lock:
        active_ids = {aid for aid, t in _active_threads.items() if t.is_alive()}
    active_count = len(active_ids)
    blocked_count = 0
    for m in _scan_agents():
        agent_id = m.get("agent_id", "")
        status = m.get("status", "")
        # Skip if already counted as active (avoid double-count during transition window)
        if agent_id in active_ids:
            continue
        if status.startswith("blocked_"):
            blocked_count += 1
    return active_count + blocked_count


def _daemon_one_tick() -> None:
    """Single iteration of the daemon outer loop. Synchronous (unit-testable).
    Production daemon (`run_daemon`) calls this in a `while True` with sleep."""
    if os.environ.get("AGENT_RUNNER_ENABLED", "true").lower() == "false":
        return

    # Reap dead threads
    with _threads_lock:
        dead = [aid for aid, t in _active_threads.items() if not t.is_alive()]
        for aid in dead:
            _active_threads.pop(aid, None)

    agents = _scan_agents()
    occupied = _occupied_slots()

    for m in agents:
        agent_id = m.get("agent_id", "")
        status = m.get("status", "")

        if status == "approved":
            if occupied >= MAX_CONCURRENT:
                continue  # queue: stay approved, picked up next tick
            with _threads_lock:
                if agent_id in _active_threads and _active_threads[agent_id].is_alive():
                    continue  # already running
            t = threading.Thread(target=_run_agent, args=(agent_id,), daemon=True,
                                  name=f"agent-{agent_id}")
            t.start()
            with _threads_lock:
                _active_threads[agent_id] = t
            occupied += 1

        elif status == "running":
            # If no active thread, agent crashed (e.g. PM2 restart). Mark + resume.
            with _threads_lock:
                has_thread = agent_id in _active_threads and _active_threads[agent_id].is_alive()
            if not has_thread and occupied < MAX_CONCURRENT:
                # Mint cid here and propagate into _run_agent so AGENT_RESUMED
                # chains with the agent_started/checkpoint/completed emits that
                # follow (Step 1 §1.4 paired-cid contract; review I4).
                recovery_cid = secrets.token_hex(6)
                _atomic_set_status(agent_id, "crashed_resumed")
                _audit(AGENT_RESUMED,
                       message=f"resumed {agent_id} after crash/restart",
                       correlation_id=recovery_cid,
                       extra={"agent_id": agent_id, "recovery": True})
                # Transition to running and re-spawn
                _atomic_set_status(agent_id, "running")
                t = threading.Thread(target=_run_agent, args=(agent_id,),
                                      kwargs={"cid": recovery_cid}, daemon=True,
                                      name=f"agent-{agent_id}")
                t.start()
                with _threads_lock:
                    _active_threads[agent_id] = t
                occupied += 1

        elif status == "blocked_on_qwen":
            # Phase 3.5 review C2: auto-resume when Qwen returns. We probe
            # Qwen liveness with a tiny request; if the call succeeds, the
            # agent transitions back to running and the daemon respawns it
            # next iteration. No user interaction needed for this block —
            # unlike blocked_on_permission, the user has nothing to grant.
            if occupied >= MAX_CONCURRENT:
                continue
            try:
                # Probe Qwen with a trivial call; if it succeeds, unblock
                _qwen_chat("ping", system_prompt="", max_tokens=1)
                qwen_alive = True
            except QwenUnavailableError:
                qwen_alive = False
            except Exception as e:
                log.debug("[%s] qwen probe error: %s", agent_id, e)
                qwen_alive = False
            if qwen_alive:
                _atomic_set_status(agent_id, "running")
                t = threading.Thread(target=_run_agent, args=(agent_id,), daemon=True,
                                      name=f"agent-{agent_id}")
                t.start()
                with _threads_lock:
                    _active_threads[agent_id] = t
                occupied += 1


def run_daemon() -> None:
    """Production entry point. Blocks forever, ticking every DAEMON_TICK_SECONDS."""
    log.info("codec-agent-runner daemon starting (MAX_CONCURRENT=%d)", MAX_CONCURRENT)
    while True:
        try:
            _daemon_one_tick()
        except Exception as e:
            log.exception("daemon tick raised: %s", e)
        time.sleep(DAEMON_TICK_SECONDS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_daemon()
