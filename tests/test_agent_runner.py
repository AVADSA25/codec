"""Phase 3 Step 9 tests — codec_agent_runner.

31 tests covering: audit constants, state machine, permission gate,
Action dataclass, qwen next-action driver, strict-consent integration,
checkpoint executor, run_agent paths, daemon outer loop, multi-agent
concurrency, resume-after-restart, plan-hash tamper, PWA endpoints.

All tests:
  - Mock Qwen-3.6 via monkeypatch._qwen_next_action / _qwen_chat
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


# ─────────────────────────────────────────────────────────────────────────────
# Task 1 — Audit event constants (1 test)
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# Task 2 — Extend state machine (1 test)
# ─────────────────────────────────────────────────────────────────────────────

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


def test_run_agent_missing_plan_hash_aborts(monkeypatch, temp_codec_dir):
    """Review fix I1: empty/missing plan_hash means agent was never properly
    approved or hash was cleared by attacker → ABORT (no silent bypass)."""
    import codec_agent_runner as car
    import codec_agent_plan as cap
    _setup_approved_agent(temp_codec_dir, monkeypatch, num_checkpoints=1)

    # Clear the plan_hash from manifest (simulating tampered manifest or missing hash)
    manifest = cap.load_manifest("test_agent")
    manifest["plan_hash"] = ""
    cap.save_manifest("test_agent", manifest)

    car._run_agent("test_agent")

    m = cap.load_manifest("test_agent")
    assert m["status"] == "aborted"
    assert "missing" in (m.get("status_reason", "") or "").lower()


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
