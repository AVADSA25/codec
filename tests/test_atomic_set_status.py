"""Tests for PR-4D (C-5) — `_atomic_set_status` returns bool + run-start guard
+ gated in-loop emits, so the agent state machine can no longer desync.

Before C-5, `_atomic_set_status` caught every exception (incl.
`InvalidStatusTransition`), logged, and returned None. The 14 call sites
proceeded as if the transition applied — executing checkpoints on a superseded
agent, and emitting "blocked" audit/notifications while the manifest said
"paused". This pins:
  * the bool contract (True applied / False not; never raises),
  * the run-start guard (never execute a superseded agent),
  * the in-loop gate (no misleading emit when the transition didn't apply).

Mirrors the `temp_codec_dir` fixture + `_setup_approved_agent` helper from
tests/test_agent_runner.py. Reference: docs/PR4D-ATOMIC-SET-STATUS-DESIGN.md,
docs/audits/PHASE-1-RELIABILITY.md C-5.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


# ── harness (mirrors tests/test_agent_runner.py) ──────────────────────────────


@pytest.fixture
def temp_codec_dir(tmp_path, monkeypatch):
    import codec_agent_plan as cap
    import codec_audit
    monkeypatch.setattr(cap, "_CODEC_DIR", tmp_path)
    monkeypatch.setattr(cap, "_AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(cap, "_GLOBAL_GRANTS_PATH", tmp_path / "agent_global_grants.json")
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", tmp_path / "audit.log")
    return tmp_path


def _setup_approved_agent(num_checkpoints=1):
    import codec_agent_plan as cap
    cps = [{
        "id": f"cp{i}", "title": f"checkpoint{i}", "description": f"d{i}",
        "skills_needed": ["weather"], "expected_output": "o", "step_budget": 5,
    } for i in range(num_checkpoints)]
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
    cap.save_manifest("test_agent", {
        "agent_id": "test_agent", "title": "x", "status": "approved",
        "plan_hash": cap.compute_plan_hash(plan),
        "created_at": "2026-01-01", "updated_at": "2026-01-01",
    })
    cap.save_state("test_agent", {"current_checkpoint": 0})


# ── unit: bool contract ───────────────────────────────────────────────────────


def test_returns_true_on_valid_transition(temp_codec_dir):
    import codec_agent_runner as car
    import codec_agent_plan as cap
    cap.save_manifest("a1", {"agent_id": "a1", "status": "approved"})
    assert car._atomic_set_status("a1", "running") is True
    assert cap.load_manifest("a1")["status"] == "running"


def test_returns_false_on_invalid_transition(temp_codec_dir):
    import codec_agent_runner as car
    import codec_agent_plan as cap
    cap.save_manifest("a1", {"agent_id": "a1", "status": "paused"})
    # paused → blocked_on_permission is NOT in _VALID_TRANSITIONS
    assert car._atomic_set_status("a1", "blocked_on_permission") is False
    assert cap.load_manifest("a1")["status"] == "paused"  # unchanged


def test_returns_false_on_unexpected_error_never_raises(temp_codec_dir, monkeypatch):
    import codec_agent_runner as car
    import codec_agent_plan as cap
    cap.save_manifest("a1", {"agent_id": "a1", "status": "approved"})

    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(cap, "set_status", boom)
    # Must NOT raise, must report failure.
    assert car._atomic_set_status("a1", "running") is False


def test_terminal_status_transition_returns_false(temp_codec_dir):
    import codec_agent_runner as car
    import codec_agent_plan as cap
    cap.save_manifest("a1", {"agent_id": "a1", "status": "aborted"})
    # aborted is terminal — nothing is reachable.
    assert car._atomic_set_status("a1", "running") is False
    assert cap.load_manifest("a1")["status"] == "aborted"


# ── behavioral: run-start guard ───────────────────────────────────────────────


def test_run_start_guard_skips_execution_when_superseded(temp_codec_dir, monkeypatch):
    """If the agent was aborted (e.g. user clicked Abort) before the runner
    thread reaches the run-start transition, checkpoints must NOT execute."""
    import codec_agent_runner as car
    import codec_agent_plan as cap
    _setup_approved_agent(num_checkpoints=1)
    # approved → aborted is valid; simulates an external abort pre-run.
    cap.set_status("test_agent", "aborted", reason="user_abort")

    exec_mock = MagicMock()
    monkeypatch.setattr(car, "_execute_checkpoint", exec_mock)

    car._run_agent("test_agent")

    assert exec_mock.call_count == 0, "must not execute checkpoints on a superseded agent"
    assert cap.load_manifest("test_agent")["status"] == "aborted", "status must not flip to running"


# ── behavioral: in-loop desync (the pause-race) ───────────────────────────────


def test_inloop_block_does_not_override_external_pause(temp_codec_dir, monkeypatch):
    """User pauses mid-checkpoint; the running thread then hits a
    PermissionViolation. The block must NOT override the user's pause, and the
    misleading AGENT_BLOCKED_ON_PERMISSION audit must NOT be emitted."""
    import codec_agent_runner as car
    import codec_agent_plan as cap
    _setup_approved_agent(num_checkpoints=1)

    events = []
    monkeypatch.setattr(car, "_audit", lambda event, **k: events.append(event))

    def fake_execute(*a, **k):
        # Simulate the PWA pause landing while the checkpoint is mid-flight.
        cap.set_status("test_agent", "paused", reason="user_pause")  # running → paused (valid)
        raise car.PermissionViolation(reason="skill_not_granted", needed="terminal")
    monkeypatch.setattr(car, "_execute_checkpoint", fake_execute)

    car._run_agent("test_agent")

    assert cap.load_manifest("test_agent")["status"] == "paused", (
        "external pause must win — block must not override it"
    )
    assert car.AGENT_BLOCKED_ON_PERMISSION not in events, (
        "must not emit a 'blocked' audit when the transition didn't apply"
    )


# ── source invariant ──────────────────────────────────────────────────────────


def test_atomic_set_status_declares_bool_return():
    src = (_REPO / "codec_agent_runner.py").read_text()
    assert "def _atomic_set_status(" in src
    # The contract is the bool return; lock it so a future edit can't silently
    # revert to the swallow-and-return-None wrapper.
    import codec_agent_runner as car
    import inspect
    sig = inspect.signature(car._atomic_set_status)
    # codec_agent_runner uses `from __future__ import annotations` (PEP 563),
    # so the annotation is the string "bool", not the bool type.
    assert sig.return_annotation in (bool, "bool"), (
        f"_atomic_set_status must be annotated -> bool, got {sig.return_annotation!r}"
    )
