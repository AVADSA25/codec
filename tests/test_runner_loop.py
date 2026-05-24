"""Tests for PR-7N (Audit B / B-12 + B-14) — _qwen_next_action is decomposed into
testable pure units (parse / build-prompt / Action construction / file-iteration tracker),
and the step budget bounds every LLM call (corrections counted) with a cumulative
extend_budget ceiling.

Reference: docs/PR7N-RUNNER-LOOP-DESIGN.md.
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


# ── B-12: pure units ──────────────────────────────────────────────────────────
def test_parse_action_json_bare():
    assert car._parse_action_json('{"kind": "checkpoint_done"}') == {"kind": "checkpoint_done"}


def test_parse_action_json_fenced():
    assert car._parse_action_json('```json\n{"skill": "weather"}\n```') == {"skill": "weather"}


def test_parse_action_json_truncated_with_prose():
    # First balanced {...} block extracted out of surrounding prose.
    assert car._parse_action_json('sure! {"skill": "x", "task": "y"} done') == {"skill": "x", "task": "y"}


def test_parse_action_json_garbage_returns_none():
    assert car._parse_action_json("no json at all") is None


def test_action_from_json_checkpoint_done():
    a = car._action_from_json({"kind": "checkpoint_done"})
    assert a.kind == "checkpoint_done"


def test_action_from_json_skill_call_coerces_and_ignores_unknown():
    a = car._action_from_json({"skill": "weather", "task": "t", "is_destructive": True,
                               "priority": "high"})  # 'priority' is unknown → ignored
    assert a.kind == "skill_call" and a.skill == "weather" and a.is_destructive is True


def test_extract_file_list_and_already_read():
    history = [
        {"result": "Files (2):\n/a/b.txt\n/a/c.txt"},
        {"result": "File: /a/b.txt\ncontents..."},
    ]
    assert car._extract_file_list(history) == ["/a/b.txt", "/a/c.txt"]
    assert car._already_read(history) == {"/a/b.txt"}


def test_build_action_prompt_contains_key_context():
    plan = {"goals": ["ship it"], "permission_manifest": {"skills": ["weather"]}}
    cp = {"title": "CP", "description": "do thing", "expected_output": "out", "step_budget": 5}
    prompt = car._build_action_prompt(plan, cp, history=[])
    assert "ship it" in prompt and "weather" in prompt and "CP" in prompt


# ── B-14: budget backstop ─────────────────────────────────────────────────────
def test_qwen_calls_capped_counting_corrections(monkeypatch, temp_codec_dir):
    """Every _qwen_next_action call (incl. the correction-nudge retry) counts against
    the budget — a correction-heavy runaway is bounded by `budget` calls, not 2x."""
    monkeypatch.setattr(car, "DEFAULT_STEP_BUDGET_PER_CHECKPOINT", 4)
    grants = {"skills": ["file_write"], "read_paths": [],
              "write_paths": ["~/ok/**"], "network_domains": []}
    gg = {"schema": 1, "version": 0, "skills": [], "read_paths": [],
          "write_paths": [], "network_domains": []}
    cp = {"id": "cp0", "title": "t", "description": "d", "expected_output": "o", "step_budget": 4}

    calls = {"n": 0}

    def fake_next(plan_dict, checkpoint, history, *a, **k):
        calls["n"] += 1
        # Alternate unauthorized→authorized so every step triggers a correction
        # (2 calls/step) and we never reach checkpoint_done.
        bad = (calls["n"] % 2 == 1)
        return car.Action(skill="file_write", task="w", kind="skill_call",
                          touches_path=True,
                          path=("/bad/x.txt" if bad else "~/ok/x.txt"))
    monkeypatch.setattr(car, "_qwen_next_action", fake_next)
    monkeypatch.setattr(car, "_run_skill", MagicMock(return_value="ok"))

    with pytest.raises(car.StepBudgetExhausted):
        car._execute_checkpoint(plan_dict={"goals": ["g"]}, checkpoint=cp,
                                agent_grants=grants, global_grants=gg, agent_id="test_agent")
    assert calls["n"] <= 5, \
        f"qwen calls must be capped at ~budget (4)+1, counting corrections; got {calls['n']} (B-14)"


def test_extend_budget_caps_cumulative(monkeypatch, temp_codec_dir):
    """extend_budget cannot push a checkpoint's override above MAX_CHECKPOINT_STEP_BUDGET."""
    import routes.agents as ra
    assert hasattr(car, "MAX_CHECKPOINT_STEP_BUDGET")
    ceiling = car.MAX_CHECKPOINT_STEP_BUDGET

    plan = cap.plan_from_dict({
        "schema": 1, "agent_id": "test_agent", "goals": ["g"],
        "checkpoints": [{"id": "cp0", "title": "c", "description": "d",
                         "skills_needed": ["weather"], "expected_output": "o", "step_budget": 5}],
        "permission_manifest": {"skills": ["weather"], "read_paths": [], "write_paths": [],
                                "network_domains": [], "destructive_ops": []},
        "estimated_duration_minutes": 10, "assumptions": [],
    })
    cap.save_plan(plan)
    cap.save_manifest("test_agent", {"agent_id": "test_agent", "title": "x",
                                     "status": "paused", "status_reason": "step_budget_exhausted"})
    # Pre-seed an override already at the ceiling.
    cap.save_state("test_agent", {"current_checkpoint": 0,
                                  "step_budget_overrides": {"cp0": ceiling}})

    with pytest.raises(ra.HTTPException) as exc:
        ra.extend_budget("test_agent", ra.ExtendBudgetBody(additional_steps=100))
    assert exc.value.status_code == 409, "extend_budget must 409 once at the cumulative ceiling (B-14)"
