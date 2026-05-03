# Phase 3 Step 9 — Background Execution + Permission Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the runtime layer of Phase 3. `codec-agent-runner` PM2 daemon picks up `status=approved` plans (from Step 8), executes their checkpoints autonomously via Qwen-3.6 ↔ skill loops, enforces the permission manifest, and persists state for resume-after-restart. **No UI yet** — Step 10 picks that up. Step 9 alone is shippable: agents actually run, you just observe via `audit.log` + `notifications.json`.

**Architecture:** New `codec_agent_runner.py` module + new PM2 service `codec-agent-runner` (sibling to `codec-observer`). Daemon outer loop polls `~/.codec/agents/*/state.json` every 5s. Each `approved` agent gets its own thread inside the daemon. Per-checkpoint LLM↔skill loop with permission gate enforcement (skill / write_path / network_domain matrix). Reuses Phase 1 Step 3 step budget + strict-consent + ask_user infrastructure. Every operation goes through `codec_dispatch.run_skill` so Step 2 plugin hooks fire automatically.

**Tech Stack:** Python 3.13 (existing), `threading.Thread` for per-agent execution, PM2 process supervision, Qwen-3.6 local LLM via `http://127.0.0.1:8090/v1/chat/completions` (existing PM2 service `qwen3.6`), pytest with `unittest.mock` for all external dependencies. All file I/O via the atomic tmp+rename pattern (mirror Phase 2 + Step 8). Permission enforcement is in-process Python (no OS-level sandbox — Q14 deferred).

**Reference design doc:** `docs/PHASE3-BLUEPRINT.md` §3 (Step 9) and §8 (resolved Q5–Q8).

**Reference Step 8 (already shipped):** `codec_agent_plan.py`, `routes/agents.py`, `~/.codec/agents/<id>/{plan,state,manifest,grants}.json`, `~/.codec/agent_global_grants.json`.

---

## File Structure

**NEW files:**

| Path | Purpose | Est. LOC |
|---|---|---|
| `codec_agent_runner.py` | Daemon outer loop + per-agent run + permission gate + checkpoint executor + qwen next-action driver | ~700 |
| `tests/test_agent_runner.py` | 31 tests covering all paths | ~900 |

**MODIFIED files:**

| Path | What | Est. LOC |
|---|---|---|
| `codec_audit.py` | Add 8 Phase 3 Step 9 audit event constants + `PHASE3_STEP9_EVENTS` frozenset | +25 |
| `codec_agent_plan.py` | Extend `_VALID_TRANSITIONS` with Step 9 statuses (running, paused, blocked_on_permission, blocked_on_destructive, completed, aborted, crashed_resumed) | +20 |
| `routes/agents.py` | Add `POST /api/agents/{id}/abort`, `/pause`, `/resume`, `/grant` endpoints | +120 |
| `ecosystem.config.js` | Add `codec-agent-runner` PM2 service entry | +15 |
| `codec_heartbeat.py` | Add `codec-agent-runner` to monitored-services list (Q15) | +5 |
| `AGENTS.md` | New §X.Y Phase 3 Step 9 sub-section, §6 audit events table extension, §10 don't-touch list update | +60 |

**Storage created at runtime** (mostly already exists from Step 8; Step 9 just writes more):

```
~/.codec/agents/<agent_id>/
  state.json         (Step 8 created; Step 9 writes more fields)
  events.jsonl       (NEW Step 9: append-only skill-call / error log)
  messages.jsonl     (NEW Step 9: agent→user message log; Step 10 will read)

~/.codec/agent_runner_max_concurrent.lock   (NEW Step 9: in-memory slot tracking)
```

---

## Task 1: Audit event constants for Step 9

**Files:**
- Modify: `codec_audit.py` (add 8 Phase 3 Step 9 constants + frozenset)
- Modify: `tests/test_agent_runner.py` (NEW file — initial test)

- [ ] **Step 1: Create `tests/test_agent_runner.py` with the audit-constants test**

```python
"""Phase 3 Step 9 tests — codec_agent_runner.

31 tests covering: audit constants, state machine, permission gate,
Action dataclass, qwen next-action driver, strict-consent integration,
checkpoint executor, run_agent paths, daemon outer loop, multi-agent
concurrency, resume-after-restart, plan-hash tamper, PWA endpoints.

All tests:
  - Mock Qwen-3.6 via monkeypatch._qwen_next_action
  - Mock codec_dispatch.run_skill (never fire real skills)
  - Mock codec_ask_user.ask + strict_consent_gate
  - Use tmp_path + temp_codec_dir fixture (mirror Step 8)
  - No real notifications, no real audit emits to live log
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def test_step9_audit_constants_present():
    """Phase 3 Step 9 adds 8 named events + 1 frozenset."""
    import codec_audit
    assert codec_audit.AGENT_STARTED == "agent_started"
    assert codec_audit.AGENT_CHECKPOINT_STARTED == "agent_checkpoint_started"
    assert codec_audit.AGENT_CHECKPOINT_COMPLETED == "agent_checkpoint_completed"
    assert codec_audit.AGENT_PAUSED == "agent_paused"
    assert codec_audit.AGENT_RESUMED == "agent_resumed"
    assert codec_audit.AGENT_BLOCKED_ON_PERMISSION == "agent_blocked_on_permission"
    assert codec_audit.AGENT_COMPLETED == "agent_completed"
    assert codec_audit.AGENT_ABORTED == "agent_aborted"
    assert codec_audit.PHASE3_STEP9_EVENTS == frozenset({
        "agent_started", "agent_checkpoint_started", "agent_checkpoint_completed",
        "agent_paused", "agent_resumed", "agent_blocked_on_permission",
        "agent_completed", "agent_aborted",
    })
```

- [ ] **Step 2: Run test, verify it fails**

`python3.13 -m pytest tests/test_agent_runner.py::test_step9_audit_constants_present -v`
Expected: FAIL with `AttributeError: module 'codec_audit' has no attribute 'AGENT_STARTED'`

- [ ] **Step 3: Add constants to `codec_audit.py`**

Find the `PHASE3_STEP8_EVENTS` block. Immediately after that frozenset closes, insert:

```python
# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 Step 9 — Background Execution + Permission Gate
# ─────────────────────────────────────────────────────────────────────────────
AGENT_STARTED                 = "agent_started"
AGENT_CHECKPOINT_STARTED      = "agent_checkpoint_started"
AGENT_CHECKPOINT_COMPLETED    = "agent_checkpoint_completed"
AGENT_PAUSED                  = "agent_paused"
AGENT_RESUMED                 = "agent_resumed"
AGENT_BLOCKED_ON_PERMISSION   = "agent_blocked_on_permission"
AGENT_COMPLETED               = "agent_completed"
AGENT_ABORTED                 = "agent_aborted"

PHASE3_STEP9_EVENTS = frozenset({
    AGENT_STARTED, AGENT_CHECKPOINT_STARTED, AGENT_CHECKPOINT_COMPLETED,
    AGENT_PAUSED, AGENT_RESUMED, AGENT_BLOCKED_ON_PERMISSION,
    AGENT_COMPLETED, AGENT_ABORTED,
})
```

- [ ] **Step 4: Run test, verify it passes**

`python3.13 -m pytest tests/test_agent_runner.py::test_step9_audit_constants_present -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add codec_audit.py tests/test_agent_runner.py
git commit -m "feat(audit): Phase 3 Step 9 event constants"
```

---

## Task 2: Extend state machine for Step 9 statuses

**Files:**
- Modify: `codec_agent_plan.py` (`_VALID_TRANSITIONS` map)
- Modify: `tests/test_agent_runner.py` (append)

Step 8's `_VALID_TRANSITIONS` only covered `draft_pending → awaiting_approval → approved/rejected/revised`. Step 9 introduces runtime statuses: `approved → running`, `running → checkpoint statuses → completed / aborted / blocked_*`, `crashed_resumed → running`.

- [ ] **Step 1: Append the failing test**

```python
def test_step9_state_transitions_extend_valid_map():
    from codec_agent_plan import _VALID_TRANSITIONS
    # approved can now transition to running (Step 9 NEW)
    assert "running" in _VALID_TRANSITIONS["approved"]
    # running can transition to: completed, aborted, paused, blocked_on_permission, blocked_on_destructive, crashed_resumed
    assert {"completed", "aborted", "paused",
            "blocked_on_permission", "blocked_on_destructive",
            "crashed_resumed"} <= _VALID_TRANSITIONS["running"]
    # paused can resume → running
    assert "running" in _VALID_TRANSITIONS["paused"]
    # blocked_on_permission can resume (after grant) → running, OR be aborted
    assert {"running", "aborted"} <= _VALID_TRANSITIONS["blocked_on_permission"]
    # blocked_on_destructive can resume (next morning consent) → running, OR be aborted
    assert {"running", "aborted"} <= _VALID_TRANSITIONS["blocked_on_destructive"]
    # crashed_resumed can re-enter running, or be aborted
    assert {"running", "aborted"} <= _VALID_TRANSITIONS["crashed_resumed"]
    # completed and aborted are terminal
    assert _VALID_TRANSITIONS["completed"] == frozenset()
    assert _VALID_TRANSITIONS["aborted"] == frozenset()
```

- [ ] **Step 2: Run test, verify fail**

`python3.13 -m pytest tests/test_agent_runner.py::test_step9_state_transitions_extend_valid_map -v`
Expected: FAIL — `KeyError: 'running'` or assertion mismatch.

- [ ] **Step 3: Extend `_VALID_TRANSITIONS` in `codec_agent_plan.py`**

Find `_VALID_TRANSITIONS` (search for it). Replace the existing map with:

```python
# Step 8 manages: draft_pending → awaiting_approval → approved/rejected/revised.
# Step 9 adds: approved → running → checkpoint_completed/blocked_*/aborted/completed.
_VALID_TRANSITIONS: Dict[str, frozenset] = {
    "draft_pending":        frozenset({"awaiting_approval", "plan_failed"}),
    "awaiting_approval":    frozenset({"approved", "rejected", "revised"}),
    "revised":              frozenset({"awaiting_approval"}),
    "approved":             frozenset({"rejected", "running"}),  # Step 9: running
    "rejected":             frozenset(),
    "plan_failed":          frozenset({"draft_pending"}),

    # Step 9 runtime states
    "running":              frozenset({"completed", "aborted", "paused",
                                       "blocked_on_permission", "blocked_on_destructive",
                                       "crashed_resumed"}),
    "paused":               frozenset({"running", "aborted"}),
    "blocked_on_permission": frozenset({"running", "aborted"}),
    "blocked_on_destructive": frozenset({"running", "aborted"}),
    "crashed_resumed":      frozenset({"running", "aborted"}),
    "completed":            frozenset(),
    "aborted":              frozenset(),
}
```

- [ ] **Step 4: Run test, verify pass**

`python3.13 -m pytest tests/test_agent_runner.py::test_step9_state_transitions_extend_valid_map -v`
Expected: PASS. Also run Step 8 tests to verify no regression: `python3.13 -m pytest tests/test_agent_plan.py -q` should still show 31/31 pass.

- [ ] **Step 5: Commit**

```bash
git add codec_agent_plan.py tests/test_agent_runner.py
git commit -m "feat(agent_plan): extend state machine with Step 9 runtime statuses"
```

---

## Task 3: PermissionViolation + permission_gate

**Files:**
- Create: `codec_agent_runner.py` (initial skeleton with permission gate)
- Modify: `tests/test_agent_runner.py` (append)

The permission gate is the core Step 9 safety enforcement. It checks every proposed action against the union of per-agent grants and global allowlist.

- [ ] **Step 1: Append 4 failing tests for the permission gate matrix**

```python
# ─────────────────────────────────────────────────────────────────────────────
# Task 3 — PermissionViolation + permission_gate (4 tests)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def basic_grants():
    """Default per-agent grants used in permission gate tests."""
    return {
        "skills": ["weather", "calculator"],
        "read_paths": ["~/Documents/**"],
        "write_paths": ["~/.codec/agents/test/artifacts/**"],
        "network_domains": ["example.com"],
    }


@pytest.fixture
def empty_global_grants():
    return {
        "schema": 1, "version": 0,
        "skills": [], "read_paths": [], "write_paths": [], "network_domains": [],
    }


def test_permission_gate_allows_action_in_manifest(basic_grants, empty_global_grants):
    from codec_agent_runner import permission_gate, Action
    action = Action(skill="weather", task="weather in Paris",
                    is_destructive=False, network_call=False, touches_path=False)
    # No exception means allowed
    permission_gate(action, basic_grants, empty_global_grants)


def test_permission_gate_blocks_skill_not_in_grants(basic_grants, empty_global_grants):
    from codec_agent_runner import permission_gate, Action, PermissionViolation
    action = Action(skill="terminal", task="ls",
                    is_destructive=False, network_call=False, touches_path=False)
    with pytest.raises(PermissionViolation) as exc:
        permission_gate(action, basic_grants, empty_global_grants)
    assert exc.value.reason == "skill_not_authorized"
    assert exc.value.needed == "terminal"


def test_permission_gate_blocks_path_outside_write_paths(basic_grants, empty_global_grants):
    from codec_agent_runner import permission_gate, Action, PermissionViolation
    action = Action(skill="weather", task="x",
                    is_destructive=False, network_call=False,
                    touches_path=True, path="/etc/passwd")
    with pytest.raises(PermissionViolation) as exc:
        permission_gate(action, basic_grants, empty_global_grants)
    assert exc.value.reason == "path_not_authorized"
    assert exc.value.needed == "/etc/passwd"


def test_permission_gate_allows_via_global_allowlist(basic_grants):
    from codec_agent_runner import permission_gate, Action
    global_grants = {
        "schema": 1, "version": 1,
        "skills": ["terminal"],  # not in per-agent grants, but in global
        "read_paths": [], "write_paths": [], "network_domains": [],
    }
    action = Action(skill="terminal", task="ls",
                    is_destructive=False, network_call=False, touches_path=False)
    # Should NOT raise — global allowlist covers it
    permission_gate(action, basic_grants, global_grants)
```

- [ ] **Step 2: Run tests, verify they fail**

`python3.13 -m pytest tests/test_agent_runner.py -k "permission_gate" -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'codec_agent_runner'`

- [ ] **Step 3: Create `codec_agent_runner.py` skeleton with permission gate**

```python
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
    executed via codec_dispatch.run_skill."""
    skill: str
    task: str
    is_destructive: bool = False
    network_call: bool = False
    network_domain: str = ""
    touches_path: bool = False
    path: str = ""
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

    if action.touches_path:
        write_paths = (set(agent_grants.get("write_paths", [])) |
                       set(global_grants.get("write_paths", [])))
        # fnmatch supports glob patterns the LLM puts in manifest
        ok = any(fnmatch.fnmatch(action.path, os.path.expanduser(p))
                 for p in write_paths)
        if not ok:
            raise PermissionViolation("path_not_authorized", action.path)

    if action.network_call:
        domains = (set(agent_grants.get("network_domains", [])) |
                   set(global_grants.get("network_domains", [])))
        if action.network_domain not in domains:
            raise PermissionViolation("domain_not_authorized", action.network_domain)
```

- [ ] **Step 4: Run tests, verify pass**

`python3.13 -m pytest tests/test_agent_runner.py -k "permission_gate" -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add codec_agent_runner.py tests/test_agent_runner.py
git commit -m "feat(agent_runner): Action dataclass + permission_gate enforcement"
```

---

## Task 4: Qwen-3.6 next-action driver

**Files:**
- Modify: `codec_agent_runner.py`
- Modify: `tests/test_agent_runner.py`

The next-action driver calls Qwen-3.6 with the current checkpoint context (plan, current state, recent events) and asks: "what's the next action?". Returns either a `skill_call` Action or `kind="checkpoint_done"` to signal completion.

- [ ] **Step 1: Append 3 failing tests**

```python
# ─────────────────────────────────────────────────────────────────────────────
# Task 4 — Qwen-3.6 next-action driver (3 tests)
# ─────────────────────────────────────────────────────────────────────────────

def test_qwen_next_action_returns_skill_call(monkeypatch):
    import codec_agent_runner as car
    fake_response = json.dumps({
        "kind": "skill_call",
        "skill": "weather",
        "task": "weather in Paris",
        "is_destructive": False,
        "network_call": False,
        "touches_path": False,
    })
    monkeypatch.setattr(car, "_qwen_chat", lambda *a, **k: fake_response)

    action = car._qwen_next_action(
        plan_dict={"goals": ["x"]},
        checkpoint={"id": "cp1", "title": "t", "description": "d", "expected_output": "o"},
        history=[],
    )
    assert action.kind == "skill_call"
    assert action.skill == "weather"
    assert action.task == "weather in Paris"


def test_qwen_next_action_returns_checkpoint_done(monkeypatch):
    import codec_agent_runner as car
    fake_response = json.dumps({"kind": "checkpoint_done"})
    monkeypatch.setattr(car, "_qwen_chat", lambda *a, **k: fake_response)

    action = car._qwen_next_action(
        plan_dict={"goals": ["x"]},
        checkpoint={"id": "cp1", "title": "t", "description": "d", "expected_output": "o"},
        history=[],
    )
    assert action.kind == "checkpoint_done"


def test_qwen_next_action_handles_qwen_unavailable(monkeypatch):
    import codec_agent_runner as car

    def raise_unavailable(*a, **k):
        raise car.QwenUnavailableError("qwen down")
    monkeypatch.setattr(car, "_qwen_chat", raise_unavailable)

    with pytest.raises(car.QwenUnavailableError):
        car._qwen_next_action(
            plan_dict={"goals": ["x"]},
            checkpoint={"id": "cp1", "title": "t", "description": "d", "expected_output": "o"},
            history=[],
        )
```

- [ ] **Step 2: Run tests, verify fail**

`python3.13 -m pytest tests/test_agent_runner.py -k "qwen_next_action" -v`
Expected: FAIL — `_qwen_next_action` not defined.

- [ ] **Step 3: Add Qwen client + driver**

Append to `codec_agent_runner.py`:

```python
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
  "touches_path": <bool — true if the skill writes to a filesystem path>,
  "path": "<path if touches_path=true, else empty>"
}

For checkpoint completion:
{"kind": "checkpoint_done"}

Rules:
- skill MUST be in plan.permission_manifest.skills.
- If you need a skill that's NOT in the manifest, return is_destructive=false and pick the closest available; the runtime will block via permission_gate, escalate to user, and re-call you with the same context.
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
        kind="skill_call",
    )
```

- [ ] **Step 4: Run tests, verify pass**

`python3.13 -m pytest tests/test_agent_runner.py -k "qwen_next_action" -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add codec_agent_runner.py tests/test_agent_runner.py
git commit -m "feat(agent_runner): Qwen-3.6 next-action driver"
```

---

## Task 5: Strict-consent gate integration for destructive ops

**Files:**
- Modify: `codec_agent_runner.py`
- Modify: `tests/test_agent_runner.py`

For destructive Actions (delete, payment, send-on-behalf), even if the manifest pre-approves, we still require Step 3 §1.7 strict-consent (literal verb-match). Overnight timeout = `blocked_on_destructive` (NOT abort, per Q7 from blueprint).

- [ ] **Step 1: Append 2 failing tests**

```python
# ─────────────────────────────────────────────────────────────────────────────
# Task 5 — Strict-consent gate integration (2 tests)
# ─────────────────────────────────────────────────────────────────────────────

def test_destructive_consent_approved_proceeds(monkeypatch):
    import codec_agent_runner as car
    fake_consent = MagicMock()
    fake_consent.approved = True
    fake_consent.timed_out = False
    monkeypatch.setattr(car, "_strict_consent", lambda action, deadline: fake_consent)

    action = car.Action(skill="file_ops", task="delete x",
                        is_destructive=True, network_call=False, touches_path=False)
    result = car._enforce_destructive_gate(action)
    assert result.approved is True
    assert result.timed_out is False


def test_destructive_consent_timeout_overnight(monkeypatch):
    """Per Q7: overnight timeout doesn't abort; agent transitions to
    blocked_on_destructive, queued for morning."""
    import codec_agent_runner as car
    fake_consent = MagicMock()
    fake_consent.approved = False
    fake_consent.timed_out = True
    monkeypatch.setattr(car, "_strict_consent", lambda action, deadline: fake_consent)

    action = car.Action(skill="file_ops", task="delete x",
                        is_destructive=True, network_call=False, touches_path=False)
    result = car._enforce_destructive_gate(action)
    assert result.timed_out is True
    assert result.approved is False
```

- [ ] **Step 2: Run tests, verify fail**

`python3.13 -m pytest tests/test_agent_runner.py -k "destructive_consent" -v`
Expected: FAIL — `_enforce_destructive_gate` not defined.

- [ ] **Step 3: Add strict-consent integration**

Append to `codec_agent_runner.py`:

```python
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
```

- [ ] **Step 4: Run tests, verify pass**

`python3.13 -m pytest tests/test_agent_runner.py -k "destructive_consent" -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add codec_agent_runner.py tests/test_agent_runner.py
git commit -m "feat(agent_runner): strict-consent gate for destructive ops (Q7)"
```

---

## Task 6: _execute_checkpoint inner loop

**Files:**
- Modify: `codec_agent_runner.py`
- Modify: `tests/test_agent_runner.py`

The checkpoint executor is the inner loop: ask Qwen for next action, gate it, execute, log, repeat until checkpoint_done OR step budget exhausted OR exception.

- [ ] **Step 1: Append 4 failing tests**

```python
# ─────────────────────────────────────────────────────────────────────────────
# Task 6 — _execute_checkpoint (4 tests)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_codec_dir(tmp_path, monkeypatch):
    """Mirrors Step 8 fixture; redirects all codec_agent_plan paths to tmp."""
    import codec_agent_plan as cap
    monkeypatch.setattr(cap, "_CODEC_DIR", tmp_path)
    monkeypatch.setattr(cap, "_AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(cap, "_GLOBAL_GRANTS_PATH", tmp_path / "agent_global_grants.json")
    return tmp_path


def test_execute_checkpoint_happy_path(monkeypatch, temp_codec_dir):
    """Two skill calls then checkpoint_done."""
    import codec_agent_runner as car

    actions_to_return = [
        car.Action(skill="weather", task="weather in Paris",
                   is_destructive=False, network_call=False, touches_path=False),
        car.Action(skill="weather", task="weather in Madrid",
                   is_destructive=False, network_call=False, touches_path=False),
        car.Action(skill="", task="", kind="checkpoint_done"),
    ]
    call_idx = {"n": 0}
    def fake_next(plan_dict, checkpoint, history):
        a = actions_to_return[call_idx["n"]]
        call_idx["n"] += 1
        return a
    monkeypatch.setattr(car, "_qwen_next_action", fake_next)

    fake_run_skill = MagicMock(return_value="result_string")
    monkeypatch.setattr(car, "_run_skill", fake_run_skill)

    grants = {"skills": ["weather"], "read_paths": [], "write_paths": [],
              "network_domains": []}
    global_grants = {"schema": 1, "version": 0,
                     "skills": [], "read_paths": [], "write_paths": [], "network_domains": []}
    checkpoint = {"id": "cp1", "title": "t", "description": "d",
                  "expected_output": "o", "step_budget": 10}

    history = car._execute_checkpoint(
        plan_dict={"goals": ["x"]}, checkpoint=checkpoint,
        agent_grants=grants, global_grants=global_grants,
        agent_id="test_agent",
    )
    assert len(history) == 2  # two skill calls (checkpoint_done not in history)
    assert fake_run_skill.call_count == 2


def test_execute_checkpoint_permission_violation_propagates(monkeypatch, temp_codec_dir):
    """Action references unauthorized skill → PermissionViolation."""
    import codec_agent_runner as car

    monkeypatch.setattr(car, "_qwen_next_action", lambda *a, **k:
        car.Action(skill="terminal", task="ls",
                   is_destructive=False, network_call=False, touches_path=False))

    grants = {"skills": ["weather"], "read_paths": [], "write_paths": [],
              "network_domains": []}
    global_grants = {"schema": 1, "version": 0,
                     "skills": [], "read_paths": [], "write_paths": [], "network_domains": []}
    checkpoint = {"id": "cp1", "title": "t", "description": "d",
                  "expected_output": "o", "step_budget": 10}

    with pytest.raises(car.PermissionViolation) as exc:
        car._execute_checkpoint(
            plan_dict={"goals": ["x"]}, checkpoint=checkpoint,
            agent_grants=grants, global_grants=global_grants,
            agent_id="test_agent",
        )
    assert exc.value.reason == "skill_not_authorized"


def test_execute_checkpoint_destructive_rejection_raises(monkeypatch, temp_codec_dir):
    """Strict-consent denied → DestructiveOpRejected."""
    import codec_agent_runner as car

    monkeypatch.setattr(car, "_qwen_next_action", lambda *a, **k:
        car.Action(skill="weather", task="x",
                   is_destructive=True, network_call=False, touches_path=False))

    fake_consent = MagicMock(approved=False, timed_out=False)
    monkeypatch.setattr(car, "_strict_consent", lambda action, deadline: fake_consent)

    grants = {"skills": ["weather"], "read_paths": [], "write_paths": [],
              "network_domains": []}
    global_grants = {"schema": 1, "version": 0,
                     "skills": [], "read_paths": [], "write_paths": [], "network_domains": []}
    checkpoint = {"id": "cp1", "title": "t", "description": "d",
                  "expected_output": "o", "step_budget": 10}

    with pytest.raises(car.DestructiveOpRejected):
        car._execute_checkpoint(
            plan_dict={"goals": ["x"]}, checkpoint=checkpoint,
            agent_grants=grants, global_grants=global_grants,
            agent_id="test_agent",
        )


def test_execute_checkpoint_step_budget_exhausted(monkeypatch, temp_codec_dir):
    """Step budget cap reached → StepBudgetExhausted."""
    import codec_agent_runner as car

    # Always return a skill call (never checkpoint_done)
    monkeypatch.setattr(car, "_qwen_next_action", lambda *a, **k:
        car.Action(skill="weather", task="loop",
                   is_destructive=False, network_call=False, touches_path=False))
    monkeypatch.setattr(car, "_run_skill", MagicMock(return_value="r"))

    grants = {"skills": ["weather"], "read_paths": [], "write_paths": [],
              "network_domains": []}
    global_grants = {"schema": 1, "version": 0,
                     "skills": [], "read_paths": [], "write_paths": [], "network_domains": []}
    checkpoint = {"id": "cp1", "title": "t", "description": "d",
                  "expected_output": "o", "step_budget": 3}  # tiny budget

    with pytest.raises(car.StepBudgetExhausted):
        car._execute_checkpoint(
            plan_dict={"goals": ["x"]}, checkpoint=checkpoint,
            agent_grants=grants, global_grants=global_grants,
            agent_id="test_agent",
        )
```

- [ ] **Step 2: Run tests, verify fail**

`python3.13 -m pytest tests/test_agent_runner.py -k "execute_checkpoint" -v`
Expected: FAIL — `_execute_checkpoint`, `_run_skill`, `DestructiveOpRejected`, `StepBudgetExhausted` not defined.

- [ ] **Step 3: Add the executor**

Append to `codec_agent_runner.py`:

```python
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
```

- [ ] **Step 4: Run tests, verify pass**

`python3.13 -m pytest tests/test_agent_runner.py -k "execute_checkpoint" -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add codec_agent_runner.py tests/test_agent_runner.py
git commit -m "feat(agent_runner): _execute_checkpoint inner loop"
```

---

## Task 7: _run_agent main thread function (5 paths)

**Files:**
- Modify: `codec_agent_runner.py`
- Modify: `tests/test_agent_runner.py`

The agent thread function. Loads the plan + grants, verifies plan_hash hasn't been tampered with, walks each checkpoint via `_execute_checkpoint`, persists state atomically after each checkpoint, posts `agent_*` audit events.

- [ ] **Step 1: Append 5 failing tests**

```python
# ─────────────────────────────────────────────────────────────────────────────
# Task 7 — _run_agent (5 tests)
# ─────────────────────────────────────────────────────────────────────────────

def _setup_approved_agent(temp_codec_dir, monkeypatch, num_checkpoints=2):
    """Helper: create an agent in 'approved' state with N checkpoints."""
    import codec_agent_plan as cap
    cps = []
    for i in range(num_checkpoints):
        cps.append({
            "id": f"cp{i}", "title": f"checkpoint{i}", "description": f"d{i}",
            "skills_needed": ["weather"], "expected_output": "o", "step_budget": 5,
        })
    plan_dict = {
        "schema": 1, "agent_id": "test_agent", "goals": ["g"],
        "checkpoints": cps,
        "permission_manifest": {
            "skills": ["weather"], "read_paths": [], "write_paths": [],
            "network_domains": [], "destructive_ops": [],
        },
        "estimated_duration_minutes": 10, "assumptions": [],
    }
    plan = cap.plan_from_dict(plan_dict)
    cap.save_plan(plan)
    cap.save_grants("test_agent", {
        "schema": 1, "agent_id": "test_agent", "approved_at": "2026-01-01",
        "skills": ["weather"], "read_paths": [], "write_paths": [],
        "network_domains": [], "destructive_ops": [], "auto_approved": {},
    })
    plan_hash = cap.compute_plan_hash(plan)
    cap.save_manifest("test_agent", {
        "agent_id": "test_agent", "title": "x",
        "status": "approved", "plan_hash": plan_hash,
        "created_at": "2026-01-01", "updated_at": "2026-01-01",
    })
    cap.save_state("test_agent", {"current_checkpoint": 0})
    return plan_hash


def test_run_agent_happy_path_completes(monkeypatch, temp_codec_dir):
    """2 checkpoints, each with one skill call → completed."""
    import codec_agent_runner as car
    import codec_agent_plan as cap
    _setup_approved_agent(temp_codec_dir, monkeypatch, num_checkpoints=2)

    actions = [
        car.Action(skill="weather", task="x", kind="skill_call",
                   is_destructive=False, network_call=False, touches_path=False),
        car.Action(skill="", task="", kind="checkpoint_done"),
        car.Action(skill="weather", task="y", kind="skill_call",
                   is_destructive=False, network_call=False, touches_path=False),
        car.Action(skill="", task="", kind="checkpoint_done"),
    ]
    idx = {"n": 0}
    def fake_next(*a, **k):
        a_obj = actions[idx["n"]]
        idx["n"] += 1
        return a_obj
    monkeypatch.setattr(car, "_qwen_next_action", fake_next)
    monkeypatch.setattr(car, "_run_skill", MagicMock(return_value="r"))

    car._run_agent("test_agent")

    m = cap.load_manifest("test_agent")
    assert m["status"] == "completed"


def test_run_agent_blocked_on_permission(monkeypatch, temp_codec_dir):
    """Action outside manifest → status=blocked_on_permission."""
    import codec_agent_runner as car
    import codec_agent_plan as cap
    _setup_approved_agent(temp_codec_dir, monkeypatch, num_checkpoints=1)

    monkeypatch.setattr(car, "_qwen_next_action", lambda *a, **k:
        car.Action(skill="terminal", task="ls", kind="skill_call",
                   is_destructive=False, network_call=False, touches_path=False))
    monkeypatch.setattr(car, "_run_skill", MagicMock())

    car._run_agent("test_agent")

    m = cap.load_manifest("test_agent")
    assert m["status"] == "blocked_on_permission"


def test_run_agent_destructive_rejected_aborts(monkeypatch, temp_codec_dir):
    """User rejects destructive op → status=aborted."""
    import codec_agent_runner as car
    import codec_agent_plan as cap
    _setup_approved_agent(temp_codec_dir, monkeypatch, num_checkpoints=1)

    monkeypatch.setattr(car, "_qwen_next_action", lambda *a, **k:
        car.Action(skill="weather", task="x", kind="skill_call",
                   is_destructive=True, network_call=False, touches_path=False))
    monkeypatch.setattr(car, "_strict_consent", lambda action, deadline:
        car.ConsentResult(approved=False, timed_out=False, user_response="no"))

    car._run_agent("test_agent")

    m = cap.load_manifest("test_agent")
    assert m["status"] == "aborted"


def test_run_agent_plan_hash_tamper_aborts(monkeypatch, temp_codec_dir):
    """plan_hash mismatch (someone edited plan.json) → aborted with reason=plan_tampered."""
    import codec_agent_runner as car
    import codec_agent_plan as cap
    _setup_approved_agent(temp_codec_dir, monkeypatch, num_checkpoints=1)

    # Tamper: rewrite plan.json with different content but keep stored hash
    plan = cap.load_plan("test_agent")
    plan.goals = ["TAMPERED"]
    cap.save_plan(plan)

    car._run_agent("test_agent")

    m = cap.load_manifest("test_agent")
    assert m["status"] == "aborted"
    assert "tamper" in (m.get("status_reason", "") or "").lower()


def test_run_agent_resume_from_checkpoint(monkeypatch, temp_codec_dir):
    """state.current_checkpoint=1 means skip checkpoint 0 on resume."""
    import codec_agent_runner as car
    import codec_agent_plan as cap
    _setup_approved_agent(temp_codec_dir, monkeypatch, num_checkpoints=3)

    cap.save_state("test_agent", {"current_checkpoint": 2})  # already past 0 and 1

    actions = [
        car.Action(skill="weather", task="cp2", kind="skill_call",
                   is_destructive=False, network_call=False, touches_path=False),
        car.Action(skill="", task="", kind="checkpoint_done"),
    ]
    idx = {"n": 0}
    def fake_next(*a, **k):
        a_obj = actions[idx["n"]]
        idx["n"] += 1
        return a_obj
    monkeypatch.setattr(car, "_qwen_next_action", fake_next)
    monkeypatch.setattr(car, "_run_skill", MagicMock(return_value="r"))

    car._run_agent("test_agent")

    m = cap.load_manifest("test_agent")
    assert m["status"] == "completed"
    # Only one checkpoint executed (cp2); cp0 and cp1 skipped via resume
    assert idx["n"] == 2  # one skill_call + one checkpoint_done = 2 next-action calls
```

- [ ] **Step 2: Run tests, verify fail**

`python3.13 -m pytest tests/test_agent_runner.py -k "run_agent" -v`
Expected: FAIL — `_run_agent` not defined.

- [ ] **Step 3: Add `_run_agent`**

Append to `codec_agent_runner.py`:

```python
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


def _run_agent(agent_id: str) -> None:
    """The main per-agent thread function. Loads plan + grants,
    verifies plan_hash, walks checkpoints via _execute_checkpoint,
    persists state, emits audit events.

    On any unhandled exception: atomic save status=aborted, log,
    emit agent_aborted. Never propagates exceptions to caller (the
    daemon's thread pool depends on this)."""
    from codec_agent_plan import (
        load_plan, load_state, load_manifest, load_grants,
        load_global_grants, save_state, save_manifest,
        compute_plan_hash,
    )

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
        if stored_hash and stored_hash != actual_hash:
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

        # Walk checkpoints
        history: List[Dict[str, Any]] = []
        for idx, cp in enumerate(plan.checkpoints):
            if idx < current_idx:
                continue  # resume: skip already-completed checkpoints
            cp_dict = {
                "id": cp.id, "title": cp.title, "description": cp.description,
                "skills_needed": cp.skills_needed,
                "expected_output": cp.expected_output,
                "step_budget": cp.step_budget,
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
                return
            except DestructiveOpRejected as e:
                _atomic_set_status(agent_id, "aborted",
                                   reason=f"destructive_rejected:{e}")
                _audit(AGENT_ABORTED, message="destructive op rejected",
                       correlation_id=cid, outcome="warning", level="warning",
                       extra={"agent_id": agent_id, "checkpoint_id": cp.id,
                              "reason": "destructive_rejected"})
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
                    _atomic_set_status(agent_id, "blocked_on_permission",
                                       reason="step_budget_exhausted")
                    _audit(AGENT_BLOCKED_ON_PERMISSION,
                           message="step budget exhausted",
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

        # All checkpoints done
        _atomic_set_status(agent_id, "completed")
        _audit(AGENT_COMPLETED, message=f"agent completed {agent_id}",
               correlation_id=cid,
               extra={"agent_id": agent_id, "total_steps": len(history)})

    except QwenUnavailableError as e:
        log.warning("[%s] qwen unavailable: %s", agent_id, e)
        _atomic_set_status(agent_id, "blocked_on_permission",
                           reason=f"qwen_unavailable:{e}")
        _audit(AGENT_BLOCKED_ON_PERMISSION,
               message=f"qwen unavailable: {e}",
               correlation_id=cid, outcome="warning", level="warning",
               extra={"agent_id": agent_id, "reason": "qwen_unavailable"})
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
```

- [ ] **Step 4: Run tests, verify pass**

`python3.13 -m pytest tests/test_agent_runner.py -k "run_agent" -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add codec_agent_runner.py tests/test_agent_runner.py
git commit -m "feat(agent_runner): _run_agent main thread (happy/blocked/aborted/tampered/resume)"
```

---

## Task 8: Daemon outer loop + multi-agent concurrency

**Files:**
- Modify: `codec_agent_runner.py`
- Modify: `tests/test_agent_runner.py`

The daemon outer loop scans `~/.codec/agents/*/state.json` every 5s, dispatches threads for `approved` agents (up to `MAX_CONCURRENT=3`), monitors `running` agents for crash recovery.

- [ ] **Step 1: Append 6 failing tests**

```python
# ─────────────────────────────────────────────────────────────────────────────
# Task 8 — Daemon outer loop + multi-agent concurrency (6 tests)
# ─────────────────────────────────────────────────────────────────────────────

def test_daemon_scan_finds_approved_agents(temp_codec_dir):
    """scan_agents() returns agent_ids with status=approved."""
    import codec_agent_runner as car
    import codec_agent_plan as cap

    cap.save_manifest("a1", {"agent_id": "a1", "status": "approved", "title": "x"})
    cap.save_manifest("a2", {"agent_id": "a2", "status": "draft_pending", "title": "y"})
    cap.save_manifest("a3", {"agent_id": "a3", "status": "approved", "title": "z"})

    found = car._scan_agents()
    approved = [a for a in found if a["status"] == "approved"]
    assert {a["agent_id"] for a in approved} == {"a1", "a3"}


def test_daemon_dispatches_thread_for_approved(monkeypatch, temp_codec_dir):
    """Daemon spawns a thread when it finds an approved agent."""
    import codec_agent_runner as car
    import codec_agent_plan as cap

    cap.save_manifest("a1", {"agent_id": "a1", "status": "approved", "title": "x"})

    spawned: List[str] = []
    def fake_run_agent(agent_id):
        spawned.append(agent_id)
        # Simulate completion
        cap.save_manifest(agent_id, {**cap.load_manifest(agent_id),
                                      "status": "completed"})
    monkeypatch.setattr(car, "_run_agent", fake_run_agent)

    car._daemon_one_tick()  # synchronous one-shot for testability

    # Wait briefly for thread completion
    time.sleep(0.5)
    assert "a1" in spawned


def test_daemon_concurrency_cap_3_max(monkeypatch, temp_codec_dir):
    """4 approved agents → only 3 spawn this tick (4th queues)."""
    import codec_agent_runner as car
    import codec_agent_plan as cap

    for i in range(4):
        cap.save_manifest(f"a{i}", {"agent_id": f"a{i}",
                                     "status": "approved", "title": "x"})

    spawned: List[str] = []
    barrier = threading.Event()
    def fake_run_agent(agent_id):
        spawned.append(agent_id)
        barrier.wait(timeout=2)  # block to keep thread "running"
    monkeypatch.setattr(car, "_run_agent", fake_run_agent)
    monkeypatch.setattr(car, "MAX_CONCURRENT", 3)

    car._daemon_one_tick()
    time.sleep(0.3)
    assert len(spawned) == 3
    barrier.set()  # release the threads


def test_daemon_blocked_agent_occupies_slot(monkeypatch, temp_codec_dir):
    """Per Q8: blocked_on_permission counts toward the 3-slot cap."""
    import codec_agent_runner as car
    import codec_agent_plan as cap

    cap.save_manifest("blocked1", {"agent_id": "blocked1",
                                    "status": "blocked_on_permission", "title": "x"})
    cap.save_manifest("blocked2", {"agent_id": "blocked2",
                                    "status": "blocked_on_permission", "title": "y"})
    cap.save_manifest("blocked3", {"agent_id": "blocked3",
                                    "status": "blocked_on_destructive", "title": "z"})
    cap.save_manifest("approved1", {"agent_id": "approved1",
                                     "status": "approved", "title": "new"})

    spawned: List[str] = []
    monkeypatch.setattr(car, "_run_agent", lambda a: spawned.append(a))
    monkeypatch.setattr(car, "MAX_CONCURRENT", 3)

    car._daemon_one_tick()
    time.sleep(0.3)
    # blocked_* count toward the 3-slot cap → no slot for approved1
    assert "approved1" not in spawned


def test_daemon_resumes_after_pm2_restart(monkeypatch, temp_codec_dir):
    """An agent in status=running with no live thread → mark crashed_resumed → restart."""
    import codec_agent_runner as car
    import codec_agent_plan as cap

    cap.save_manifest("crashed", {"agent_id": "crashed",
                                    "status": "running", "title": "x"})

    spawned: List[str] = []
    monkeypatch.setattr(car, "_run_agent", lambda a: spawned.append(a))
    # Mark NO active thread for "crashed" — simulating fresh PM2 boot
    monkeypatch.setattr(car, "_active_threads", {})

    car._daemon_one_tick()
    time.sleep(0.3)

    # Daemon should mark crashed_resumed and restart the thread
    assert "crashed" in spawned
    m = cap.load_manifest("crashed")
    # Status moved through crashed_resumed back to running (or may still be running if thread is fast)
    assert m["status"] in ("crashed_resumed", "running", "completed", "aborted")


def test_daemon_global_kill_switch(monkeypatch, temp_codec_dir):
    """AGENT_RUNNER_ENABLED=false → daemon idles even with approved agents."""
    import codec_agent_runner as car
    import codec_agent_plan as cap

    cap.save_manifest("a1", {"agent_id": "a1", "status": "approved", "title": "x"})

    monkeypatch.setenv("AGENT_RUNNER_ENABLED", "false")
    spawned: List[str] = []
    monkeypatch.setattr(car, "_run_agent", lambda a: spawned.append(a))

    car._daemon_one_tick()
    time.sleep(0.3)
    assert spawned == []
```

- [ ] **Step 2: Run tests, verify fail**

`python3.13 -m pytest tests/test_agent_runner.py -k "daemon" -v`
Expected: FAIL — `_scan_agents`, `_daemon_one_tick`, `_active_threads`, `MAX_CONCURRENT` not defined.

- [ ] **Step 3: Add daemon loop**

Append to `codec_agent_runner.py`:

```python
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
    occupy a slot). Note: completed/aborted/rejected don't occupy."""
    with _threads_lock:
        active_count = sum(1 for t in _active_threads.values() if t.is_alive())
    blocked_count = 0
    for m in _scan_agents():
        if m.get("status", "").startswith("blocked_"):
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
                _atomic_set_status(agent_id, "crashed_resumed")
                _audit(AGENT_RESUMED,
                       message=f"resumed {agent_id} after crash/restart",
                       extra={"agent_id": agent_id, "recovery": True})
                # Transition to running and re-spawn
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
```

- [ ] **Step 4: Run tests, verify pass**

`python3.13 -m pytest tests/test_agent_runner.py -k "daemon" -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add codec_agent_runner.py tests/test_agent_runner.py
git commit -m "feat(agent_runner): daemon outer loop + multi-agent concurrency (3 max)"
```

---

## Task 9: PWA endpoints — abort / pause / resume / grant

**Files:**
- Modify: `routes/agents.py` (add 4 endpoints)
- Modify: `tests/test_agent_runner.py`

These complement Step 8's plan-management endpoints. Step 9 adds runtime controls: abort an agent, pause, resume from blocked, grant a missing permission.

- [ ] **Step 1: Append 4 failing tests**

```python
# ─────────────────────────────────────────────────────────────────────────────
# Task 9 — PWA endpoints (4 tests)
# ─────────────────────────────────────────────────────────────────────────────

def test_post_api_agents_abort(temp_codec_dir):
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    import codec_agent_plan as cap

    cap.save_manifest("a1", {"agent_id": "a1", "status": "running", "title": "x"})

    from routes.agents import router
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    r = client.post("/api/agents/a1/abort")
    assert r.status_code == 200
    m = cap.load_manifest("a1")
    assert m["status"] == "aborted"


def test_post_api_agents_pause_then_resume(temp_codec_dir):
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    import codec_agent_plan as cap

    cap.save_manifest("a1", {"agent_id": "a1", "status": "running", "title": "x"})

    from routes.agents import router
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    r1 = client.post("/api/agents/a1/pause")
    assert r1.status_code == 200
    assert cap.load_manifest("a1")["status"] == "paused"

    r2 = client.post("/api/agents/a1/resume")
    assert r2.status_code == 200
    assert cap.load_manifest("a1")["status"] == "running"


def test_post_api_agents_grant_missing_permission(temp_codec_dir):
    """User grants a missing permission to a blocked agent.
    Adds to per-agent grants and transitions back to running."""
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    import codec_agent_plan as cap

    cap.save_manifest("a1", {"agent_id": "a1",
                              "status": "blocked_on_permission", "title": "x"})
    cap.save_grants("a1", {
        "schema": 1, "agent_id": "a1", "approved_at": "2026-01-01",
        "skills": ["weather"], "read_paths": [], "write_paths": [],
        "network_domains": [], "destructive_ops": [], "auto_approved": {},
    })

    from routes.agents import router
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    r = client.post("/api/agents/a1/grant",
                     json={"kind": "skills", "value": "calculator"})
    assert r.status_code == 200
    grants = cap.load_grants("a1")
    assert "calculator" in grants["skills"]
    m = cap.load_manifest("a1")
    assert m["status"] == "running"  # unblocked


def test_post_api_agents_404_for_unknown_id(temp_codec_dir):
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from routes.agents import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    r = client.post("/api/agents/nonexistent/abort")
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests, verify fail**

`python3.13 -m pytest tests/test_agent_runner.py -k "post_api_agents_abort or pause_then_resume or grant_missing or unknown_id" -v`
Expected: FAIL — endpoints don't exist yet.

- [ ] **Step 3: Add 4 endpoints to `routes/agents.py`**

Find the bottom of the file (after the global grants endpoints). Insert before the last closing `}`:

```python
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
```

- [ ] **Step 4: Run tests, verify pass**

`python3.13 -m pytest tests/test_agent_runner.py -k "post_api_agents_abort or pause_then_resume or grant_missing or unknown_id" -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add routes/agents.py tests/test_agent_runner.py
git commit -m "feat(routes): /api/agents abort/pause/resume/grant endpoints"
```

---

## Task 10: PM2 ecosystem config + heartbeat monitoring

**Files:**
- Modify: `ecosystem.config.js`
- Modify: `codec_heartbeat.py`
- (no new tests — config-only)

- [ ] **Step 1: Add codec-agent-runner to ecosystem.config.js**

Open `ecosystem.config.js`. Find the `codec-observer` entry. Immediately after it, insert:

```javascript
    {
      name: "codec-agent-runner",
      script: "codec_agent_runner.py",
      interpreter: "/opt/homebrew/opt/python@3.13/bin/python3.13",
      cwd: "/Users/mickaelfarina/codec-repo",
      autorestart: true,
      watch: false,
      max_restarts: 10,
      env: {
        AGENT_RUNNER_ENABLED: "true",
        AGENT_RUNNER_MAX_CONCURRENT: "3",
        PYTHONUNBUFFERED: "1",
      },
      out_file: "/Users/mickaelfarina/.pm2/logs/codec-agent-runner-out.log",
      error_file: "/Users/mickaelfarina/.pm2/logs/codec-agent-runner-error.log",
    },
```

- [ ] **Step 2: Add codec-agent-runner to heartbeat monitoring**

Open `codec_heartbeat.py`. Search for the list of monitored services (look for `"codec-observer"` or `_MONITORED_SERVICES`). Add `"codec-agent-runner"` to that list.

The line probably looks like:
```python
_MONITORED_SERVICES = [
    "codec-dashboard", "codec-mcp-http", "kokoro-82m",
    "qwen3.6", "whisper-stt", "codec-observer",
]
```

Update to:
```python
_MONITORED_SERVICES = [
    "codec-dashboard", "codec-mcp-http", "kokoro-82m",
    "qwen3.6", "whisper-stt", "codec-observer",
    "codec-agent-runner",
]
```

- [ ] **Step 3: Quick smoke test (no automated test — config-only)**

```bash
# Validate JS syntax
node -e "require('./ecosystem.config.js')" && echo "ecosystem.config.js OK"

# Validate Python imports
python3.13 -c "import codec_heartbeat; print('heartbeat OK')"
```

Both should print "OK" lines.

- [ ] **Step 4: Commit**

```bash
git add ecosystem.config.js codec_heartbeat.py
git commit -m "feat(pm2): add codec-agent-runner service + heartbeat monitoring (Q15)"
```

---

## Task 11: Final verification + AGENTS.md docs + push + PR

**Files:**
- Modify: `AGENTS.md`
- (no new tests — final integration check)

- [ ] **Step 1: Run full test suite**

```bash
python3.13 -m pytest tests/ --ignore=tests/test_smoke.py -q --tb=no
```

Expected: passed count ≥ 901 (was 870 on main after Step 8); 20 failed (baseline); 73 skipped (baseline). The 31 new tests in `tests/test_agent_runner.py` should bring the count to 901.

If the count doesn't match: investigate. Any failure outside the 20 baseline = regression.

- [ ] **Step 2: Update AGENTS.md**

Open `AGENTS.md`. Find the existing "Plan + Permission Contract (Phase 3 Step 8)" sub-section under §3. Immediately after the Step 8 sub-section, before the "Other known gaps" line, insert:

```markdown
### Background Execution + Permission Gate (Phase 3 Step 8)

`codec_agent_runner.py` is the runtime layer. PM2-managed daemon `codec-agent-runner` polls `~/.codec/agents/*/state.json` every 5s, picks up `status=approved` plans, executes their checkpoints autonomously via Qwen-3.6 ↔ skill loops. **Permission gate** enforces the manifest on every action; outside-manifest = `blocked_on_permission` + `ask_user` notification.

**Per-checkpoint loop** (inside `_execute_checkpoint`):
1. `_qwen_next_action()` returns either `Action(kind="skill_call", ...)` or `Action(kind="checkpoint_done")`
2. `permission_gate(action, agent_grants, global_grants)` raises `PermissionViolation` if outside manifest
3. If `action.is_destructive`: `_enforce_destructive_gate()` calls Step 3 §1.7 strict-consent (literal verb-match required, generic "yes" rejected)
4. `_run_skill()` dispatches via `codec_dispatch.run_skill` (Step 1+2 hooks fire automatically)
5. Append result to history, loop until `checkpoint_done` OR `step_budget` cap reached

**Resume policy (Q5)**: after PM2 restart, daemon scans for `status=running` agents. Any with no live thread = crashed. Marks `crashed_resumed`, then transitions back to `running` and respawns. Worst case: one operation re-fires from the last atomic checkpoint save (idempotent skills are safe; destructive ops re-hit strict-consent).

**Multi-agent concurrency (Q6, Q8)**: default `MAX_CONCURRENT=3`, env var `AGENT_RUNNER_MAX_CONCURRENT`. Blocked agents (any `blocked_*` state) **occupy a slot** — trade-off: 3 simultaneous overnight blocks = no new agent can start until you grant.

**Plan-hash tamper detection (Q13)**: at run start, `_run_agent` verifies `manifest.plan_hash == sha256(plan.json)`. Mismatch → `aborted(plan_tampered)`. Closes the attack vector where `plan.json` is hand-edited after approval.

**Public API (`codec_agent_runner`):**
- `_run_agent(agent_id)` — main per-agent thread function (called by daemon, not directly)
- `_daemon_one_tick()` — synchronous test-only wrapper around the daemon scan
- `run_daemon()` — production entry point (PM2 `codec-agent-runner`)
- `permission_gate(action, agent_grants, global_grants)` — synchronous gate check
- Dataclasses: `Action`, `ConsentResult`
- Exceptions: `PermissionViolation`, `DestructiveOpRejected`, `StepBudgetExhausted`, `QwenUnavailableError`

**PWA endpoints (`routes/agents.py` Step 9 additions):**
- `POST /api/agents/{id}/abort`
- `POST /api/agents/{id}/pause`
- `POST /api/agents/{id}/resume`
- `POST /api/agents/{id}/grant` (body: kind, value — adds to per-agent grants, unblocks if blocked_on_permission)

**8 audit events** (paired correlation_id per operation envelope per Step 1 §1.4): `agent_started`, `agent_checkpoint_started`, `_completed`, `agent_paused`, `agent_resumed`, `agent_blocked_on_permission`, `agent_completed`, `agent_aborted`.

**Kill switches:**
- `AGENT_RUNNER_ENABLED=false` — daemon idles (still scans, never spawns threads)
- Per-agent: `POST /api/agents/{id}/abort` (immediate, atomic state write)
- Per-agent: `POST /api/agents/{id}/pause` / `/resume`

**Reuses (no new infrastructure):**
- Step 1 audit envelope (paired correlation_id)
- Step 2 plugin lifecycle hooks (every `run_skill` wrapped automatically)
- Step 3 `ask_user` (outside-manifest pause prompt)
- Step 3 §1.7 strict-consent gate (universal floor for destructive ops)
- Step 5 observer (passively records agent activity in audit log)
- Step 7 shift_report (agent activity surfaces in daily summary automatically)

Implementation: `codec_agent_runner.py` (~700 LOC), `routes/agents.py` (+120 for Step 9 endpoints), `ecosystem.config.js` (+15 for PM2 entry), `codec_heartbeat.py` (+1 monitored service entry).
```

Also extend §6 audit events table. Find the Phase 3 Step 8 audit table. Append a Phase 3 Step 9 sub-section:

```markdown
#### Phase 3 Step 9 events — agent runtime lifecycle

Eight event names. `agent_started` opens the per-agent operation envelope; subsequent events all share that single correlation_id (multi-emit op per Step 1 §1.4). `agent_blocked_on_permission` and `agent_paused` are warning level; the rest are info except `agent_aborted` (error or warning).

| Event | Source | level | extra fields |
|---|---|---|---|
| `agent_started` | `codec-agent-runner` | info | `agent_id`, `checkpoint_count`, `starting_at` (resume idx) |
| `agent_checkpoint_started` | `codec-agent-runner` | info | `agent_id`, `checkpoint_id`, `checkpoint_idx` |
| `agent_checkpoint_completed` | `codec-agent-runner` | info | `agent_id`, `checkpoint_id`, `checkpoint_idx`, `steps_used` |
| `agent_paused` | `codec-agent-runner` | warning | `agent_id`, `checkpoint_id`, `reason` |
| `agent_resumed` | `codec-agent-runner` | info | `agent_id`, `recovery` (true=PM2-restart) |
| `agent_blocked_on_permission` | `codec-agent-runner` | warning | `agent_id`, `checkpoint_id`, `reason`, `needed` |
| `agent_completed` | `codec-agent-runner` | info | `agent_id`, `total_steps` |
| `agent_aborted` | `codec-agent-runner` | error\|warning | `agent_id`, `reason` |

`PHASE3_STEP9_EVENTS` frozenset exposed.
```

Also update §10 don't-touch list. Append:

```markdown
- `codec_agent_runner.py` (Phase 3 Step 9) — runtime daemon. Don't refactor without re-running PHASE3-STEP9 design gate. The `MAX_CONCURRENT` constant and `_active_threads` global are mutated under `_threads_lock`; no other code may touch them.
- `~/.codec/agents/<id>/events.jsonl` and `messages.jsonl` (Phase 3 Step 9) — per-agent event/message logs. Append-only, never edit. Step 10 will read messages.jsonl for the chat thread UI.
- `AGENT_RUNNER_ENABLED` and `AGENT_RUNNER_MAX_CONCURRENT` env vars (Phase 3 Step 9, defaults `true` / `3`). Setting `AGENT_RUNNER_ENABLED=false` idles the daemon.
- PM2 `codec-agent-runner` service (Phase 3 Step 9). Stop/restart through PM2; `codec-heartbeat` monitors and emits `service_down` if it crashes.
```

- [ ] **Step 3: Final verify after AGENTS.md edit**

```bash
python3.13 -m pytest tests/ --ignore=tests/test_smoke.py -q --tb=no | tail -3
```

Expected: same baseline, no regression from doc edit.

- [ ] **Step 4: Commit AGENTS.md**

```bash
git add AGENTS.md
git commit -m "docs(agents): Phase 3 Step 9 module + endpoints + audit events"
```

- [ ] **Step 5: Push + open PR**

```bash
git push -u origin feat/phase3-step9-implementation

gh pr create --title "feat(phase3-step9): Background Execution + Permission Gate" --body "$(cat <<'EOF'
## Summary

Phase 3 Step 9 — Background Execution + Permission Gate. The runtime layer.

`codec-agent-runner` PM2 daemon picks up `status=approved` plans (from Step 8), executes their checkpoints autonomously via Qwen-3.6 ↔ skill loops. Permission gate enforces the manifest on every action. Strict-consent gate (Step 3 §1.7 reuse) on destructive ops. Resume after PM2 restart. Multi-agent concurrency cap = 3 (Q6).

**No UI yet** — Step 10 picks that up. Step 9 alone is shippable: agents actually run; you observe via `audit.log` + `notifications.json`.

## Reference

- Blueprint: `docs/PHASE3-BLUEPRINT.md` §3
- TDD plan: `docs/PHASE3-STEP9-PLAN.md`
- Resolved Q&A: blueprint §8 (Q5 resume from last atomic checkpoint, Q6 3 concurrent, Q7 destructive timeout = blocked not aborted, Q8 blocked occupies slot, Q13 plan-hash tamper, Q15 heartbeat monitor)

## Files

| Path | Type | Lines | Purpose |
|---|---|---|---|
| `codec_agent_runner.py` | NEW | ~700 | Daemon loop + per-agent run + permission gate + checkpoint executor |
| `tests/test_agent_runner.py` | NEW | ~900 | 31 tests, all behaviors covered |
| `codec_audit.py` | MOD | +25 | Step 9 event constants |
| `codec_agent_plan.py` | MOD | +20 | Extended `_VALID_TRANSITIONS` |
| `routes/agents.py` | MOD | +120 | abort/pause/resume/grant endpoints |
| `ecosystem.config.js` | MOD | +15 | PM2 codec-agent-runner entry |
| `codec_heartbeat.py` | MOD | +5 | monitored services list |
| `AGENTS.md` | MOD | +60 | Step 9 docs |

## Audit envelope

8 new schema:1 events. The `agent_started` opens the per-agent operation envelope; `_checkpoint_started/_completed/_blocked_on_permission/_paused/_resumed/_completed/_aborted` all share that single correlation_id (multi-emit op per Step 1 §1.4 contract).

## Permission gate (the core safety enforcement)

Every Action goes through `permission_gate(action, agent_grants, global_grants)`:
- skill not in (per-agent ∪ global) → `PermissionViolation(skill_not_authorized)`
- write path not in (per-agent ∪ global) write paths → `PermissionViolation(path_not_authorized)`
- network domain not in (per-agent ∪ global) → `PermissionViolation(domain_not_authorized)`

Destructive ops STILL hit Step 3 §1.7 strict-consent (universal floor) — even pre-approved.

## State machine extension

```
draft_pending → awaiting_approval → approved → running → completed
                                               → aborted
                                               → paused → running
                                               → blocked_on_permission → running | aborted
                                               → blocked_on_destructive → running | aborted
                                               → crashed_resumed → running | aborted
```

`completed` and `aborted` are terminal.

## Test plan
- [x] 🧪 `tests/test_agent_runner.py` → 31 passed
- [x] 🧪 Full suite — passed count = 901 (was 870), same 20/73 baseline
- [x] Permission gate matrix coverage (skill / path / domain × in-manifest / in-global / outside)
- [x] All 8 Step 9 audit events emit with paired correlation_id
- [ ] Post-merge deploy:
  ```bash
  git pull
  pm2 start ecosystem.config.js --only codec-agent-runner
  pm2 restart codec-dashboard codec-heartbeat
  ```
- [ ] Real-world test: drop a project via PWA chat (Step 10 will add UI; for now via direct `POST /api/agents`), approve, verify agent_started + checkpoint events in `~/.codec/audit.log`

## Out of scope (Step 10)

- Project mode UI / chat dropdown / status pills
- Proactive messaging from agent → user (the timeline)
- Auto-escalation from chat mode
- Reading `messages.jsonl` for chat thread (Step 10's job)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review Checklist

**Spec coverage:**

- [x] Audit constants (Q15 heartbeat reuse) → Tasks 1, 10
- [x] State machine extension → Task 2
- [x] PermissionViolation + permission_gate (skill/path/domain matrix) → Task 3
- [x] Action dataclass → Task 3
- [x] Qwen-3.6 next-action driver (local-only per Q1) → Task 4
- [x] Strict-consent gate integration (Q7 destructive timeout) → Task 5
- [x] _execute_checkpoint inner loop → Task 6
- [x] _run_agent (5 paths: happy/blocked/aborted/tampered/resume) → Task 7
- [x] Daemon outer loop + multi-agent concurrency (Q6 3 max, Q8 blocked occupies) → Task 8
- [x] PWA endpoints (abort/pause/resume/grant) → Task 9
- [x] PM2 ecosystem + heartbeat monitor (Q15) → Task 10
- [x] AGENTS.md documentation → Task 11

**Placeholder scan:** No "TBD", "TODO", "fill in later" present. Every code block is complete.

**Type consistency:** `Action`, `PermissionViolation`, `DestructiveOpRejected`, `StepBudgetExhausted`, `ConsentResult` defined in Tasks 3-6 and consistently referenced. `_qwen_next_action`, `_run_skill`, `_execute_checkpoint`, `_run_agent`, `_scan_agents`, `_daemon_one_tick`, `permission_gate`, `_strict_consent`, `_enforce_destructive_gate`, `_audit`, `_atomic_set_status`, `_now_iso_local` — all introduced in earlier tasks, reused in later tasks with the exact same signature.

---

*Plan complete. Ready for execution.*
