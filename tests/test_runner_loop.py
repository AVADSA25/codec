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


# ── Loop-breaker (2026-07): detect a stuck repeat-loop well before budget ──────
def test_no_progress_detected_on_repeated_identical_results(monkeypatch, temp_codec_dir):
    """Reproduces the live failure: a research checkpoint kept re-fetching the
    same URL and re-issuing the same search, getting back identical results,
    and burned its ENTIRE 80-step budget without ever finishing. The
    loop-breaker must raise NoProgressDetected after _REPEAT_THRESHOLD
    identical results — long before a large budget would exhaust — so the
    agent pauses early with an honest reason instead of grinding pointlessly."""
    grants = {"skills": ["web_fetch"], "read_paths": [], "write_paths": [],
              "network_domains": ["*"]}
    gg = {"schema": 1, "version": 0, "skills": [], "read_paths": [],
          "write_paths": [], "network_domains": []}
    # A large budget — if the loop-breaker didn't exist, this would grind for
    # 60 steps before StepBudgetExhausted, exactly like the real incident.
    cp = {"id": "cp0", "title": "research", "description": "d",
          "expected_output": "o", "step_budget": 60}

    call_n = {"n": 0}
    def fake_next(plan_dict, checkpoint, history, *a, **k):
        call_n["n"] += 1
        # The model keeps varying its phrasing (as it did live) but keeps
        # calling the same skill against the same effective target.
        return car.Action(skill="web_fetch", task=f"Fetch attempt #{call_n['n']} of the page",
                          kind="skill_call")
    monkeypatch.setattr(car, "_qwen_next_action", fake_next)
    # Every fetch returns the EXACT same raw content — no new information.
    monkeypatch.setattr(car, "_run_skill",
                        MagicMock(return_value="<!DOCTYPE html>...same page every time..."))

    with pytest.raises(car.NoProgressDetected):
        car._execute_checkpoint(plan_dict={"goals": ["g"]}, checkpoint=cp,
                                agent_grants=grants, global_grants=gg, agent_id="test_agent")
    assert call_n["n"] <= car._REPEAT_THRESHOLD + 1, (
        f"loop-breaker must fire within ~{car._REPEAT_THRESHOLD} repeats, not grind "
        f"toward the 60-step budget; got {call_n['n']} calls"
    )


def test_no_progress_not_triggered_by_genuinely_different_results(monkeypatch, temp_codec_dir):
    """Sanity check: normal progress (each step yields new information) must
    NOT trip the loop-breaker — only true repeats should."""
    grants = {"skills": ["web_fetch"], "read_paths": [], "write_paths": [],
              "network_domains": ["*"]}
    gg = {"schema": 1, "version": 0, "skills": [], "read_paths": [],
          "write_paths": [], "network_domains": []}
    cp = {"id": "cp0", "title": "research", "description": "d",
          "expected_output": "o", "step_budget": 5}

    step = {"n": 0}
    def fake_next(plan_dict, checkpoint, history, *a, **k):
        step["n"] += 1
        if step["n"] > 4:
            return car.Action(skill="", task="", kind="checkpoint_done")
        return car.Action(skill="web_fetch", task=f"Fetch source #{step['n']}", kind="skill_call")
    monkeypatch.setattr(car, "_qwen_next_action", fake_next)
    # Each call returns DIFFERENT content — genuine progress, not a loop.
    monkeypatch.setattr(car, "_run_skill",
                        lambda skill, task, agent_id: f"unique content for step {step['n']}")

    history = car._execute_checkpoint(plan_dict={"goals": ["g"]}, checkpoint=cp,
                                      agent_grants=grants, global_grants=gg, agent_id="test_agent")
    assert len(history) == 4  # all 4 distinct fetches ran, then checkpoint_done


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


# ── #17 gdoc completion verifier: a "save to Drive" checkpoint can't finish on a
# local .md; the agent is nudged once to make the real doc. ──
def test_gdoc_deliverable_nudges_before_completion(monkeypatch, temp_codec_dir):
    grants = {"skills": ["web_search"], "read_paths": [], "write_paths": [],
              "network_domains": ["*"]}
    gg = {"schema": 1, "version": 0, "skills": [], "read_paths": [],
          "write_paths": [], "network_domains": []}
    cp = {"id": "cp0", "title": "report", "description": "d",
          "expected_output": "save the competitor report to Google Drive",
          "step_budget": 8}

    calls = {"n": 0}

    def fake_next(plan_dict, checkpoint, history, *a, **k):
        calls["n"] += 1
        # 1st: a web_search (produces only local text). 2nd onward: try to finish.
        if calls["n"] == 1:
            return car.Action(skill="web_search", task="competitors", kind="skill_call")
        return car.Action(skill="", task="", kind="checkpoint_done")

    monkeypatch.setattr(car, "_qwen_next_action", fake_next)
    monkeypatch.setattr(car, "_run_skill",
                        lambda skill, task, agent_id: "Found 3 competitors: A, B, C")

    history = car._execute_checkpoint(
        plan_dict={"goals": ["g"]}, checkpoint=cp,
        agent_grants=grants, global_grants=gg, agent_id="test_agent")

    # The verifier must have injected exactly one nudge (no real doc URL present).
    nudges = [h for h in history if h.get("_gdoc_verify_nudge")]
    assert len(nudges) == 1, "must nudge once when a Drive deliverable has no real doc"
    assert "google doc" in nudges[0]["result"].lower()


def test_gdoc_deliverable_accepts_real_doc_url(monkeypatch, temp_codec_dir):
    grants = {"skills": ["google_docs"], "read_paths": [], "write_paths": [],
              "network_domains": ["*"]}
    gg = {"schema": 1, "version": 0, "skills": [], "read_paths": [],
          "write_paths": [], "network_domains": []}
    cp = {"id": "cp0", "title": "report", "description": "d",
          "expected_output": "save the report to a Google Doc", "step_budget": 6}

    step = {"n": 0}

    def fake_next(plan_dict, checkpoint, history, *a, **k):
        step["n"] += 1
        if step["n"] == 1:
            return car.Action(skill="google_docs", task="create doc", kind="skill_call")
        return car.Action(skill="", task="", kind="checkpoint_done")

    monkeypatch.setattr(car, "_qwen_next_action", fake_next)
    monkeypatch.setattr(car, "_run_skill",
                        lambda skill, task, agent_id: "Created https://docs.google.com/document/d/REAL123/edit")

    history = car._execute_checkpoint(
        plan_dict={"goals": ["g"]}, checkpoint=cp,
        agent_grants=grants, global_grants=gg, agent_id="test_agent")

    # A real doc URL is present → no verifier nudge, checkpoint completes.
    assert not [h for h in history if h.get("_gdoc_verify_nudge")], "must not nudge when a real doc exists"
