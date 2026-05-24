"""Tests for PR-7G — Audit B B-8 (blocked_on_destructive must surface a recovery
affordance) + completing B-2 in the loop (the consent gate fires on the
SERVER-derived destructive assessment, not just the LLM's self-declared flag).

Reference: docs/PR7G-DESTRUCTIVE-RECOVERY-DESIGN.md.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path[:] = [p for p in sys.path if p != str(REPO)]
sys.path.insert(0, str(REPO))

import codec_agent_plan as cap  # noqa: E402
import codec_agent_messaging as cam  # noqa: E402
import codec_agent_runner as car  # noqa: E402


@pytest.fixture
def temp_codec_dir(tmp_path, monkeypatch):
    import codec_audit
    monkeypatch.setattr(cap, "_CODEC_DIR", tmp_path)
    monkeypatch.setattr(cap, "_AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(cap, "_GLOBAL_GRANTS_PATH", tmp_path / "agent_global_grants.json")
    monkeypatch.setattr(cam, "_AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", tmp_path / "audit.log")
    return tmp_path


def _setup_approved():
    plan = cap.plan_from_dict({
        "schema": 1, "agent_id": "test_agent", "goals": ["g"],
        "checkpoints": [{"id": "cp0", "title": "c", "description": "d",
                         "skills_needed": ["weather"], "expected_output": "o", "step_budget": 5}],
        "permission_manifest": {"skills": ["weather"], "read_paths": [], "write_paths": [],
                                "network_domains": [], "destructive_ops": []},
        "estimated_duration_minutes": 10, "assumptions": [],
    })
    cap.save_plan(plan)
    cap.save_grants("test_agent", {
        "schema": 1, "agent_id": "test_agent", "approved_at": "2026-01-01",
        "skills": ["weather"], "read_paths": [], "write_paths": [],
        "network_domains": [], "destructive_ops": [], "auto_approved": {},
    })
    cap.save_manifest("test_agent", {
        "agent_id": "test_agent", "title": "x", "status": "approved",
        "plan_hash": cap.compute_plan_hash(plan),
        "grants_hash": cap.compute_grants_hash("test_agent"),
        "created_at": "2026-01-01", "updated_at": "2026-01-01",
    })
    cap.save_state("test_agent", {"current_checkpoint": 0})


def test_blocked_on_destructive_posts_resume_affordance(monkeypatch, temp_codec_dir):
    _setup_approved()
    monkeypatch.setattr(car, "_qwen_next_action",
                        lambda *a, **k: car.Action(skill="weather", task="delete files",
                                                   is_destructive=True, kind="skill_call"))
    monkeypatch.setattr(car, "_enforce_destructive_gate",
                        lambda action, deadline=0: car.ConsentResult(approved=False, timed_out=True))
    posts = []
    monkeypatch.setattr(cam, "post_message", lambda **kw: posts.append(kw))

    car._run_agent("test_agent")

    assert cap.load_manifest("test_agent")["status"] == "blocked_on_destructive"
    resume_posts = [p for p in posts
                    if any("/api/agents/test_agent/resume" in (a.get("endpoint", ""))
                           for a in (p.get("actions") or []))]
    assert resume_posts, "blocked_on_destructive must post a notification with a Resume action (B-8)"


def test_loop_gates_unflagged_server_destructive_action(monkeypatch, temp_codec_dir):
    """B-2 in the loop: an action the LLM marks is_destructive=false but whose
    task is irreversible must STILL hit the consent gate."""
    _setup_approved()
    actions = [
        car.Action(skill="weather", task="delete the old reports", is_destructive=False, kind="skill_call"),
        car.Action(skill="", task="", kind="checkpoint_done"),
    ]
    idx = {"n": 0}

    def fake_next(*a, **k):
        x = actions[idx["n"]]; idx["n"] += 1; return x

    gate_calls = []

    def fake_gate(action, deadline=0):
        gate_calls.append(action)
        return car.ConsentResult(approved=True, timed_out=False)

    monkeypatch.setattr(car, "_qwen_next_action", fake_next)
    monkeypatch.setattr(car, "_enforce_destructive_gate", fake_gate)
    monkeypatch.setattr(car, "_run_skill", MagicMock(return_value="r"))
    monkeypatch.setattr(cam, "post_message", lambda **kw: None)

    car._run_agent("test_agent")

    assert gate_calls, "an unflagged but server-destructive action must reach the consent gate (B-2)"
