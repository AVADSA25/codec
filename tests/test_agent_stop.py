"""P-010: Project-mode Abort and Pause really stop a running agent.

The routes only write the new status; the runner re-reads its manifest at step
boundaries (before a model call, after one, after a destructive confirmation) and
stops with StopRequested. These tests drive the real routes through FastAPI's
TestClient while a fake slow skill or a fake model call is held on an event.

Every path is in tmp_path (plan, messaging, notifications, audit) and pushes are
recorded, never sent, so the owner's inbox, ~/.codec/agents and phone are never
touched. Reference: docs/AGENT-STOP-DESIGN.md.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path[:] = [p for p in sys.path if p != str(REPO)]
sys.path.insert(0, str(REPO))

import codec_agent_messaging as cam  # noqa: E402
import codec_agent_plan as cap  # noqa: E402
import codec_agent_runner as car  # noqa: E402

AGENT = "test_agent"
WAIT = 5  # seconds; every wait is bounded so a regression fails instead of hanging


@pytest.fixture
def iso(tmp_path, monkeypatch):
    import codec_audit
    import codec_push
    monkeypatch.setattr(cap, "_CODEC_DIR", tmp_path)
    monkeypatch.setattr(cap, "_AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(cap, "_GLOBAL_GRANTS_PATH", tmp_path / "agent_global_grants.json")
    monkeypatch.setattr(cam, "_CODEC_DIR", tmp_path)
    monkeypatch.setattr(cam, "_AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(cam, "_NOTIFICATIONS_PATH", tmp_path / "notifications.json")
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", tmp_path / "audit.log")
    events: list = []

    def record_audit(*a, **kw):
        events.append((kw.get("event") or (a[0] if a else ""), dict(kw.get("extra") or {})))
    monkeypatch.setattr(codec_audit, "audit", record_audit)
    pushes: list = []
    monkeypatch.setattr(codec_push, "notify", pushes.append)
    monkeypatch.setenv("PUSH_ENABLED", "false")
    monkeypatch.delenv("AGENT_RUNNER_ENABLED", raising=False)
    monkeypatch.setattr(car, "_active_threads", {})
    monkeypatch.setattr(car, "MAX_CONCURRENT", 3)
    return SimpleNamespace(tmp=tmp_path, events=events, pushes=pushes)


def _setup(num_checkpoints=1):
    cps = [{"id": f"cp{i}", "title": f"c{i}", "description": "d",
            "skills_needed": ["weather"], "expected_output": "o", "step_budget": 5}
           for i in range(num_checkpoints)]
    plan = cap.plan_from_dict({
        "schema": 1, "agent_id": AGENT, "goals": ["g"], "checkpoints": cps,
        "permission_manifest": {"skills": ["weather"], "read_paths": [], "write_paths": [],
                                "network_domains": [], "destructive_ops": []},
        "estimated_duration_minutes": 10, "assumptions": [],
    })
    cap.save_plan(plan)
    cap.save_grants(AGENT, {
        "schema": 1, "agent_id": AGENT, "approved_at": "2026-01-01",
        "skills": ["weather"], "read_paths": [], "write_paths": [],
        "network_domains": [], "destructive_ops": [], "auto_approved": {},
    })
    cap.save_manifest(AGENT, {
        "agent_id": AGENT, "title": "x", "status": "approved",
        "plan_hash": cap.compute_plan_hash(plan),
        "grants_hash": cap.compute_grants_hash(AGENT),
        "created_at": "2026-01-01", "updated_at": "2026-01-01",
    })
    cap.save_state(AGENT, {"current_checkpoint": 0})


def _call(task, destructive=False):
    return car.Action(skill="weather", task=task, kind="skill_call", is_destructive=destructive)


def _done():
    return car.Action(skill="", task="", kind="checkpoint_done")


class FakeModel:
    """Stands in for _qwen_next_action: replays `actions` in order, keeps a copy of
    the history each call saw, and can hold one call (by index) until released."""

    def __init__(self, actions, hold_call=None):
        self.actions, self.hold_call = list(actions), hold_call
        self.seen: list = []
        self.entered, self.release = threading.Event(), threading.Event()

    def __call__(self, plan_dict, checkpoint, history, *a, **k):
        n = len(self.seen)
        self.seen.append([dict(h) for h in history])
        if n == self.hold_call:
            self.entered.set()
            self.release.wait(WAIT)
        return self.actions[n]

    @property
    def calls(self):
        return len(self.seen)


class FakeSkill:
    """Stands in for _run_skill: records every task and holds `hold_task` until released."""

    def __init__(self, hold_task=None):
        self.tasks: list = []
        self.hold_task = hold_task
        self.entered, self.release = threading.Event(), threading.Event()

    def __call__(self, skill_name, task, agent_id):
        self.tasks.append(task)
        if task == self.hold_task:
            self.entered.set()
            self.release.wait(WAIT)
        return f"result of {task}"


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.agents import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _post(client, action):
    r = client.post(f"/api/agents/{AGENT}/{action}")
    assert r.status_code == 200, r.text


def _messages(tmp):
    path = tmp / "agents" / AGENT / "messages.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _start_run():
    t = threading.Thread(target=car._run_agent, args=(AGENT,), daemon=True)
    t.start()
    return t


def _join(t):
    t.join(WAIT)
    assert not t.is_alive(), "the runner thread must exit after a stop"


def _assert_interrupted_once(iso):
    msgs = _messages(iso.tmp)
    stopped = [m for m in msgs if m["type"] == "agent_aborted"]
    assert len(stopped) == 1 and stopped[0]["title"] == "Interrupted", msgs
    assert not [m for m in msgs if m["type"] == "agent_done"], "an aborted run must not announce completion"
    assert not [m for m in msgs if m["title"].startswith("Checkpoint")], "no checkpoint may complete"
    assert iso.pushes == ["agent_stopped"]
    names = [e for e, _ in iso.events]
    assert names.count("agent_aborted") == 1
    assert "agent_completed" not in names and "agent_checkpoint_completed" not in names
    assert [x["reason"] for e, x in iso.events if e == "agent_aborted"] == ["user_aborted"]


def test_fixture_keeps_live_state_untouched(iso):
    import codec_audit
    import codec_push
    for p in (cap._CODEC_DIR, cap._AGENTS_DIR, cap._GLOBAL_GRANTS_PATH, cam._CODEC_DIR,
              cam._AGENTS_DIR, cam._NOTIFICATIONS_PATH, codec_audit._AUDIT_LOG):
        assert Path(p).is_relative_to(iso.tmp), p
    codec_push.notify("probe")
    codec_audit.audit(event="probe_event")
    assert iso.pushes == ["probe"] and ("probe_event", {}) in iso.events


def test_abort_during_skill_stops_before_next_step(iso, monkeypatch):
    """Abort while step 1's skill runs: it finishes, then nothing new starts."""
    _setup(num_checkpoints=2)
    model = FakeModel([_call("a"), _done(), _call("b"), _done()])
    skill = FakeSkill(hold_task="a")
    monkeypatch.setattr(car, "_qwen_next_action", model)
    monkeypatch.setattr(car, "_run_skill", skill)

    t = _start_run()
    assert skill.entered.wait(WAIT)
    assert cap.load_manifest(AGENT)["status"] == "running"
    _post(_client(), "abort")
    skill.release.set()
    _join(t)

    assert model.calls == 1, "no model call may start after the abort"
    assert skill.tasks == ["a"], "no skill call may start after the abort"
    state = cap.load_state(AGENT)
    assert state["current_checkpoint"] == 0, "the checkpoint cursor must not advance"
    assert [h["task"] for h in state["cp_history"]] == ["a"]
    manifest = cap.load_manifest(AGENT)
    assert manifest["status"] == "aborted" and manifest["status_reason"] == "user_aborted"
    _assert_interrupted_once(iso)


def test_abort_during_model_call_discards_its_answer(iso, monkeypatch):
    """Abort while the model is answering: the skill call it proposes never runs."""
    _setup(num_checkpoints=1)
    model = FakeModel([_call("a"), _done()], hold_call=0)
    skill = FakeSkill()
    monkeypatch.setattr(car, "_qwen_next_action", model)
    monkeypatch.setattr(car, "_run_skill", skill)

    t = _start_run()
    assert model.entered.wait(WAIT)
    _post(_client(), "abort")
    model.release.set()
    _join(t)

    assert model.calls == 1 and skill.tasks == []
    assert cap.load_state(AGENT)["current_checkpoint"] == 0
    assert cap.load_manifest(AGENT)["status"] == "aborted"
    _assert_interrupted_once(iso)


def test_abort_during_skill_beats_the_loop_breaker(iso, monkeypatch):
    """The held skill's result is the repeat that would trip NoProgressDetected
    (already nudged once). The stop is checked first, so the abort still posts
    Interrupted instead of a quiet failed pause."""
    _setup(num_checkpoints=1)
    sig = car._result_signature("weather", "result of same")
    seeded = [{"step": i, "skill": "weather", "task": "same", "result": "result of same",
               "is_destructive": False} for i in range(3)]
    seeded.append({"step": 3, "skill": "weather", "task": "same", "result": "[LOOP DETECTED]",
                   "is_destructive": False, "_loop_correction_nudge": True, "_loop_sig": sig})
    cap.save_state(AGENT, {"current_checkpoint": 0, "cp_in_progress": "cp0",
                           "cp_history": seeded, "executed_destructive": []})
    model = FakeModel([_call("same")])
    skill = FakeSkill(hold_task="same")
    monkeypatch.setattr(car, "_qwen_next_action", model)
    monkeypatch.setattr(car, "_run_skill", skill)

    t = _start_run()
    assert skill.entered.wait(WAIT)
    _post(_client(), "abort")
    skill.release.set()
    _join(t)

    assert model.calls == 1 and skill.tasks == ["same"]
    assert cap.load_manifest(AGENT)["status_reason"] == "user_aborted"
    assert not [e for e, _ in iso.events if e == "agent_paused"]
    _assert_interrupted_once(iso)


def test_pause_exits_and_resume_continues_through_daemon(iso, monkeypatch):
    """Pause stops at the next boundary and the thread exits; Resume re-enters through
    the daemon's crash-resume branch and continues from the saved step."""
    _setup(num_checkpoints=1)
    model = FakeModel([_call("a"), _call("b"), _done()])
    skill = FakeSkill(hold_task="a")
    monkeypatch.setattr(car, "_qwen_next_action", model)
    monkeypatch.setattr(car, "_run_skill", skill)
    client = _client()

    car._daemon_one_tick()  # approved: starts the runner thread
    first = car._active_threads[AGENT]
    assert skill.entered.wait(WAIT)
    _post(client, "pause")
    skill.release.set()
    _join(first)
    assert model.calls == 1 and skill.tasks == ["a"]
    assert cap.load_manifest(AGENT)["status"] == "paused"
    state = cap.load_state(AGENT)
    assert state["current_checkpoint"] == 0 and state["cp_in_progress"] == "cp0"
    assert [x["reason"] for e, x in iso.events if e == "agent_paused"] == ["user_paused"]

    car._daemon_one_tick()  # paused: reaps the finished thread, starts nothing
    assert AGENT not in car._active_threads and model.calls == 1

    _post(client, "resume")
    car._daemon_one_tick()  # running with no live thread: the crash-resume branch
    second = car._active_threads[AGENT]
    assert second is not first
    _join(second)

    assert cap.load_manifest(AGENT)["status"] == "completed"
    assert skill.tasks == ["a", "b"], "the step done before the pause must not be replayed"
    assert [h["task"] for h in model.seen[1]] == ["a"], "resume continues from the saved step"
    resumed = [x for e, x in iso.events if e == "agent_resumed"]
    assert len(resumed) == 1 and resumed[0]["recovery"] is True
    msgs = _messages(iso.tmp)
    assert not [m for m in msgs if m["type"] == "agent_aborted"]
    assert len([m for m in msgs if m["type"] == "agent_done"]) == 1


def _pause_resume_destructive(monkeypatch, model, skill, consent):
    monkeypatch.setattr(car, "_qwen_next_action", model)
    monkeypatch.setattr(car, "_run_skill", skill)
    monkeypatch.setattr(car, "_strict_consent", consent)
    client = _client()
    car._daemon_one_tick()
    first = car._active_threads[AGENT]
    return client, first


def test_destructive_step_runs_once_across_pause_and_resume(iso, monkeypatch):
    """The at-most-once ledger still holds: an irreversible step that ran before the
    pause is skipped after resume, not run or confirmed a second time."""
    _setup(num_checkpoints=1)
    task = "send the weekly report"
    model = FakeModel([_call(task, True), _call(task, True), _done()])
    skill = FakeSkill(hold_task=task)
    asked: list = []

    def consent(action, deadline):
        asked.append(action.task)
        return car.ConsentResult(approved=True, user_response="confirm")
    client, first = _pause_resume_destructive(monkeypatch, model, skill, consent)

    assert skill.entered.wait(WAIT)
    _post(client, "pause")
    skill.release.set()
    _join(first)
    assert cap.load_state(AGENT)["executed_destructive"] == [car._fingerprint("cp0", "weather", task)]

    _post(client, "resume")
    car._daemon_one_tick()
    _join(car._active_threads[AGENT])

    assert cap.load_manifest(AGENT)["status"] == "completed"
    assert skill.tasks == [task], "the irreversible step must run exactly once"
    assert asked == [task], "and must not be confirmed again"
    assert any(h.get("_resume_skipped") for h in model.seen[2])


def test_pause_during_confirmation_runs_nothing_and_asks_again(iso, monkeypatch):
    """A pause that lands while the destructive confirmation is open wins over the
    answer: nothing runs, no marker is written, and the resume asks again."""
    _setup(num_checkpoints=1)
    task = "send the weekly report"
    model = FakeModel([_call(task, True), _call(task, True), _done()])
    skill = FakeSkill()
    asked: list = []
    held, release = threading.Event(), threading.Event()

    def consent(action, deadline):
        asked.append(action.task)
        if len(asked) == 1:
            held.set()
            release.wait(WAIT)
        return car.ConsentResult(approved=True, user_response="confirm")
    client, first = _pause_resume_destructive(monkeypatch, model, skill, consent)

    assert held.wait(WAIT)
    _post(client, "pause")
    release.set()  # the owner's "confirm" arrives after the pause
    _join(first)
    assert skill.tasks == [] and model.calls == 1
    assert cap.load_state(AGENT)["executed_destructive"] == [], "no marker for a step that never ran"

    _post(client, "resume")
    car._daemon_one_tick()
    _join(car._active_threads[AGENT])

    assert asked == [task, task], "the resume asks for confirmation again"
    assert skill.tasks == [task], "and then runs the step once"
    assert cap.load_manifest(AGENT)["status"] == "completed"


def test_json_retry_not_sent_after_stop(iso, monkeypatch):
    """The format retry inside _qwen_next_action is a second model call: it is sent
    while the agent runs (control) and not once the manifest reads aborted."""
    _setup(num_checkpoints=1)
    cap.set_status(AGENT, "running")
    plan_dict = cap.load_plan(AGENT).to_dict()
    checkpoint = {"id": "cp0", "title": "c0", "description": "d",
                  "expected_output": "o", "step_budget": 5}
    sent: list = []

    def not_json(user_prompt, system_prompt="", max_tokens=2000):
        sent.append(user_prompt)
        return "no JSON here"
    monkeypatch.setattr(car, "_qwen_chat", not_json)
    with pytest.raises(ValueError):
        car._qwen_next_action(plan_dict, checkpoint, [])
    assert len(sent) == 2, "control: the retry is sent while the agent runs"

    sent.clear()

    def abort_while_answering(user_prompt, system_prompt="", max_tokens=2000):
        sent.append(user_prompt)
        cap.set_status(AGENT, "aborted", reason="user_aborted")
        return "no JSON here"
    monkeypatch.setattr(car, "_qwen_chat", abort_while_answering)
    with pytest.raises(car.StopRequested) as exc:
        car._qwen_next_action(plan_dict, checkpoint, [])
    assert exc.value.status == "aborted" and len(sent) == 1
