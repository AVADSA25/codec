"""Tests for PR-7I (Audit B / B-5) — a crash mid-checkpoint must not (a) lose the
in-checkpoint history (replay-from-zero) nor (b) re-execute a destructive action that
already fired before the crash. State persists the running history per step + an
at-most-once fingerprint ledger for destructive ops; both reload on resume.

Reference: docs/PR7I-RESUME-HISTORY-DESIGN.md.
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


def _setup(num_checkpoints=1):
    cps = [{"id": f"cp{i}", "title": f"c{i}", "description": "d",
            "skills_needed": ["weather"], "expected_output": "o", "step_budget": 5}
           for i in range(num_checkpoints)]
    plan = cap.plan_from_dict({
        "schema": 1, "agent_id": "test_agent", "goals": ["g"],
        "checkpoints": cps,
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


def _seq(actions):
    """Return a fake _qwen_next_action that yields `actions` in order."""
    idx = {"n": 0}

    def fake_next(*a, **k):
        x = actions[idx["n"]]
        idx["n"] += 1
        return x
    return fake_next


def test_execute_checkpoint_persists_history_each_step(monkeypatch, temp_codec_dir):
    """History is persisted to state.json after each step — not only at checkpoint
    completion. A spy reads state at skill-call time; by the 2nd step the 1st is saved."""
    _setup(num_checkpoints=1)
    monkeypatch.setattr(car, "_qwen_next_action", _seq([
        car.Action(skill="weather", task="a", kind="skill_call"),
        car.Action(skill="weather", task="b", kind="skill_call"),
        car.Action(skill="", task="", kind="checkpoint_done"),
    ]))
    seen = []

    def spy(skill, task, agent_id):
        st = cap.load_state("test_agent")
        seen.append(len(st.get("cp_history", [])))
        return "r"
    monkeypatch.setattr(car, "_run_skill", spy)

    cp = {"id": "cp0", "title": "t", "description": "d", "expected_output": "o", "step_budget": 10}
    car._execute_checkpoint(plan_dict={"goals": ["g"]}, checkpoint=cp,
                            agent_grants=cap.load_grants("test_agent"),
                            global_grants=cap.load_global_grants(), agent_id="test_agent")

    assert seen and seen[-1] >= 1, \
        "history must be persisted incrementally, not only at checkpoint completion (B-5)"


def test_resume_seeds_history_from_persisted_state(monkeypatch, temp_codec_dir):
    """On resume, the in-progress checkpoint's persisted history is reloaded and fed to
    the model — instead of starting empty (replay-from-zero)."""
    _setup(num_checkpoints=1)
    cap.save_state("test_agent", {
        "current_checkpoint": 0,
        "cp_in_progress": "cp0",
        "cp_history": [{"step": 0, "skill": "weather", "task": "prior step", "result": "did work"}],
    })
    captured = []

    def fake_next(plan, cp, history):
        captured.append(list(history))
        return car.Action(skill="", task="", kind="checkpoint_done")
    monkeypatch.setattr(car, "_qwen_next_action", fake_next)
    monkeypatch.setattr(car, "_run_skill", MagicMock(return_value="r"))
    monkeypatch.setattr(cam, "post_message", lambda **kw: None)

    car._run_agent("test_agent")

    assert captured, "qwen should have been consulted"
    assert "prior step" in [e.get("task") for e in captured[0]], \
        "resume must seed the in-progress history from state.json (B-5)"


def test_destructive_action_skipped_if_already_executed_on_resume(monkeypatch, temp_codec_dir):
    """A destructive action whose fingerprint is already in the ledger MUST NOT re-run
    on resume (at-most-once) — and must not re-prompt consent."""
    _setup(num_checkpoints=1)
    fp = car._fingerprint("cp0", "weather", "delete the records")
    cap.save_state("test_agent", {
        "current_checkpoint": 0, "cp_in_progress": "cp0",
        "cp_history": [], "executed_destructive": [fp],
    })
    monkeypatch.setattr(car, "_qwen_next_action", _seq([
        car.Action(skill="weather", task="delete the records", is_destructive=True, kind="skill_call"),
        car.Action(skill="", task="", kind="checkpoint_done"),
    ]))
    run_skill = MagicMock(return_value="r")
    monkeypatch.setattr(car, "_run_skill", run_skill)
    gate = MagicMock(return_value=car.ConsentResult(approved=True, timed_out=False))
    monkeypatch.setattr(car, "_enforce_destructive_gate", gate)
    monkeypatch.setattr(cam, "post_message", lambda **kw: None)

    car._run_agent("test_agent")

    assert run_skill.call_count == 0, \
        "an already-attempted destructive action must NOT re-run on resume (B-5 at-most-once)"
    assert gate.call_count == 0, "a skipped destructive action must not re-prompt consent"


def test_destructive_marker_persisted_before_execution(monkeypatch, temp_codec_dir):
    """The destructive fingerprint is written to state BEFORE the skill executes, so a
    crash during execution still leaves the marker (→ skipped on resume)."""
    _setup(num_checkpoints=1)
    monkeypatch.setattr(car, "_qwen_next_action", _seq([
        car.Action(skill="weather", task="send the report", is_destructive=True, kind="skill_call"),
        car.Action(skill="", task="", kind="checkpoint_done"),
    ]))
    monkeypatch.setattr(car, "_enforce_destructive_gate",
                        lambda *a, **k: car.ConsentResult(approved=True, timed_out=False))
    observed = {}

    def spy(skill, task, agent_id):
        st = cap.load_state(agent_id)
        observed["marked"] = car._fingerprint("cp0", skill, task) in st.get("executed_destructive", [])
        return "sent"
    monkeypatch.setattr(car, "_run_skill", spy)
    monkeypatch.setattr(cam, "post_message", lambda **kw: None)

    car._run_agent("test_agent")

    assert observed.get("marked") is True, \
        "destructive marker must be persisted BEFORE the skill executes (at-most-once on crash) (B-5)"


def test_checkpoint_completion_clears_progress_preserves_cursor(monkeypatch, temp_codec_dir):
    """Checkpoint completion clears the per-checkpoint progress keys but preserves
    current_checkpoint advance + last_reply_ts (today's full-overwrite dropped them)."""
    _setup(num_checkpoints=1)
    cap.save_state("test_agent", {"current_checkpoint": 0, "last_reply_ts": 123.5})
    monkeypatch.setattr(car, "_qwen_next_action", _seq([
        car.Action(skill="weather", task="x", kind="skill_call"),
        car.Action(skill="", task="", kind="checkpoint_done"),
    ]))
    monkeypatch.setattr(car, "_run_skill", MagicMock(return_value="r"))
    monkeypatch.setattr(cam, "post_message", lambda **kw: None)

    car._run_agent("test_agent")

    st = cap.load_state("test_agent")
    assert "cp_history" not in st and "cp_in_progress" not in st \
        and "executed_destructive" not in st, \
        "checkpoint completion must clear per-checkpoint progress keys (B-5)"
    assert st.get("current_checkpoint") == 1, "completion advances the checkpoint cursor"
    assert "last_reply_ts" in st, "completion must not drop last_reply_ts"
