"""Vibe agent, step 3c: run_check in the loop (approval, stored allowances, results, audit). The sandbox itself is
replaced by a recording fake here; tests/test_vibe_agent_cmd.py runs the real one."""
from __future__ import annotations

import itertools
import json

import pytest

import codec_vibe_agent as va
import codec_vibe_agent_cmd as cmd
import codec_vibe_projects as vp
from vibe_agent_fakes import (  # noqa: F401  (fixtures are used by name)
    agent_env, audits, blocker, cfg, end_of, kinds, make_project, scripted, wait_finished, wait_for,
)

RUN = "Check it.\n@@ run_check\nnpm test\n"


class FakeCommands:
    """Stands in for the sandbox runner: remembers what it was asked to run."""

    def __init__(self, monkeypatch, result=None, writes=None):
        self.calls = []
        self.result = result or cmd.Result(0, "3 passed", 0.4)
        self.writes = writes or {}
        monkeypatch.setattr(va, "_run_command", self)
        monkeypatch.setattr(cmd, "available", lambda: True)

    def __call__(self, project, root, command, **kw):
        self.calls.append({"project": project, "root": root, "command": command, **kw})
        for name, text in self.writes.items():
            (project / name).write_text(text)
        return self.result


@pytest.fixture
def fake(monkeypatch):
    return FakeCommands(monkeypatch)


def start(pid, **kw):
    return va.start_run(pid, "Make the tests pass", can_run=kw.pop("can_run", True), **kw)["run"]


def approval_of(pid, run_id):
    def look():
        return next((e for e in va.get_run(pid, run_id)["events"] if e["type"] == "approval" and e["id"] not in getattr(look, "seen", ())), None)
    return wait_for(look)


def decide(pid, run_id, decision="once", network=False):
    ev = approval_of(pid, run_id)
    va.approve_run(pid, run_id, ev["id"], decision, network)
    return ev


# ── offered only when it can be ──

def test_run_check_is_offered_to_a_run_started_at_the_mac_and_not_to_one_started_away(scripted, fake):
    pid = make_project()
    model = scripted(["@@ finish\nok\n", "@@ finish\nok\n"])
    wait_finished(pid, start(pid))
    assert "@@ run_check" in model.calls[0][0]["content"] and "approves each command" in model.calls[0][0]["content"]
    wait_finished(pid, start(pid, can_run=False))
    assert "run_check" not in model.calls[1][0]["content"]


def test_an_unavailable_sandbox_means_no_run_check(scripted, monkeypatch):
    monkeypatch.setattr(cmd, "available", lambda: False)
    pid = make_project()
    model = scripted(["@@ run_check\nnpm test\n", "@@ finish\nok\n"])
    view = wait_finished(pid, start(pid))
    assert "run_check" not in model.calls[0][0]["content"]
    assert "no tool called 'run_check'" in model.calls[1][-1]["content"] and end_of(view)["status"] == "done"


def test_a_run_started_away_cannot_use_the_tool_even_if_the_model_asks(scripted, fake):
    pid = make_project()
    scripted([RUN, "@@ finish\nok\n"])
    view = wait_finished(pid, start(pid, can_run=False))
    assert fake.calls == [] and not [e for e in view["events"] if e["type"] == "approval"]


# ── the approval ──

def test_the_owner_approves_once_and_the_result_goes_back_to_the_model(scripted, fake, audits):
    pid = make_project()
    model = scripted([RUN, "@@ finish\nTests pass.\n"])
    run_id = start(pid)
    ev = approval_of(pid, run_id)
    assert ev["command"] == "npm test" and va.get_run(pid, run_id)["status"] == "waiting"
    assert fake.calls == []                                           # nothing runs before the owner says so
    va.approve_run(pid, run_id, ev["id"], "once", False)
    view = wait_finished(pid, run_id)
    assert len(fake.calls) == 1 and fake.calls[0]["command"] == "npm test" and fake.calls[0]["network"] is False
    assert fake.calls[0]["timeout"] <= cmd.DEFAULT_TIMEOUT and fake.calls[0]["stop_event"] is va._RUNS[run_id].stop_event
    assert [e["type"] for e in view["events"] if e["type"] in ("tool", "approval", "decision", "result")][:4] == ["tool", "approval", "decision", "result"]
    result = [e for e in view["events"] if e["type"] == "result" and e["tool"] == "run_check"][0]
    assert result["ok"] and result["summary"] == "Ran npm test: exit 0 (0.4 s)"
    assert "Exit code 0 after 0.4 s." in model.calls[1][-1]["content"] and "3 passed" in model.calls[1][-1]["content"]
    assert vp.allowed_commands(pid) == []
    cmds = [x for e, x in audits if e == "vibe_agent_command"]
    assert cmds == [{"project": pid, "run": run_id, "n": 1, "decision": "once", "program": "npm", "network": False, "exit_code": 0, "ms": cmds[0]["ms"]}]


def test_declining_means_it_does_not_run_and_the_model_is_told_not_to_ask_again(scripted, fake, audits):
    pid = make_project()
    model = scripted([RUN, "@@ finish\nCould not check.\n"])
    run_id = start(pid)
    decide(pid, run_id, "decline")
    view = wait_finished(pid, run_id)
    assert fake.calls == [] and end_of(view)["status"] == "done"
    assert "did not allow that command" in model.calls[1][-1]["content"] and "Do not ask for it again" in model.calls[1][-1]["content"]
    assert [x["decision"] for e, x in audits if e == "vibe_agent_command"] == ["declined"]


def test_the_network_tick_is_passed_on_and_only_when_allowing(scripted, fake):
    pid = make_project()
    scripted(["@@ run_check\nnpm install\n", "@@ run_check\nnpm test\n", "@@ finish\nok\n"])
    run_id = start(pid)
    decide(pid, run_id, "once", network=True)
    wait_for(lambda: len(fake.calls) == 1)
    ev = approval_of_second = None
    ev = wait_for(lambda: next((e for e in va.get_run(pid, run_id)["events"] if e["type"] == "approval" and e["command"] == "npm test"), None))
    va.approve_run(pid, run_id, ev["id"], "decline", True)             # a decline never carries a network ticket
    wait_finished(pid, run_id)
    assert fake.calls[0]["network"] is True and len(fake.calls) == 1
    decisions = [e for e in va.get_run(pid, run_id)["events"] if e["type"] == "decision"]
    assert [(d["decision"], d["network"]) for d in decisions] == [("once", True), ("decline", False)]


def test_always_allow_is_stored_exactly_and_the_next_run_does_not_ask(scripted, fake, audits):
    pid = make_project()
    scripted([RUN, "@@ finish\nok\n", RUN, "@@ finish\nok\n", "@@ run_check\nnpm  test\n", "@@ finish\nok\n"])
    first = start(pid)
    decide(pid, first, "always", network=True)
    wait_finished(pid, first)
    assert vp.allowed_commands(pid) == [{"cmd": "npm test", "network": True}]
    assert json.loads((vp.project_dir(pid) / ".vibe" / "project.json").read_text())["allowed_commands"] == [{"cmd": "npm test", "network": True}]
    second = start(pid)
    view = wait_finished(pid, second)
    assert not [e for e in view["events"] if e["type"] == "approval"] and len(fake.calls) == 2
    assert fake.calls[1]["network"] is True                           # what was stored with it
    third = start(pid)                                                # other spacing: a different command, so it asks again
    approval_of(pid, third)
    assert len(fake.calls) == 2
    va.stop_run(pid, third)
    wait_finished(pid, third)
    assert [x["decision"] for e, x in audits if e == "vibe_agent_command"] == ["always", "stored"]


def test_a_command_can_be_forgotten_and_the_list_is_capped(scripted, fake):
    pid = make_project()
    for i in range(vp.MAX_ALLOWED_COMMANDS + 3):
        vp.allow_command(pid, f"echo {i}", i % 2 == 0)
    items = vp.allowed_commands(pid)
    assert len(items) == vp.MAX_ALLOWED_COMMANDS and items[0]["cmd"] == "echo 3" and items[-1]["cmd"] == f"echo {vp.MAX_ALLOWED_COMMANDS + 2}"
    assert va.commands(pid) == items
    left = va.forget(pid, "echo 5")
    assert all(e["cmd"] != "echo 5" for e in left) and len(left) == vp.MAX_ALLOWED_COMMANDS - 1
    with pytest.raises(vp.VibeError) as e:
        va.forget(pid, "echo 5")
    assert e.value.status == 404
    with pytest.raises(vp.VibeError):
        vp.allow_command(pid, "", False)
    scripted(["@@ finish\nok\n"])
    wait_finished(pid, start(pid))
    assert va.commands(pid) == left


def test_a_dangerous_command_is_refused_without_asking(scripted, fake, audits):
    pid = make_project()
    model = scripted(["@@ run_check\nrm -rf /\n", "@@ finish\nok\n"])
    view = wait_finished(pid, start(pid))
    assert fake.calls == [] and not [e for e in view["events"] if e["type"] == "approval"]
    assert "refused" in model.calls[1][-1]["content"]
    assert [x["decision"] for e, x in audits if e == "vibe_agent_command"] == ["refused"]
    assert [x["reason"] for e, x in audits if e == "vibe_agent_blocked"] == ["dangerous"]


def test_wrong_decisions_are_refused(scripted, fake):
    pid = make_project()
    scripted([RUN, "@@ finish\nok\n"])
    run_id = start(pid)
    ev = approval_of(pid, run_id)
    for args, status in ((("a-wrong", "once"), 409), ((ev["id"], "maybe"), 400), ((None, "once"), 409), (("", "once"), 409)):
        with pytest.raises(vp.VibeError) as e:
            va.approve_run(pid, run_id, *args)
        assert e.value.status == status
    assert va.get_run(pid, run_id)["status"] == "waiting" and fake.calls == []
    va.approve_run(pid, run_id, ev["id"], "once", False)
    wait_finished(pid, run_id)
    with pytest.raises(vp.VibeError) as e:
        va.approve_run(pid, run_id, ev["id"], "once", False)          # nothing waits any more
    assert e.value.status == 409


def test_an_answer_to_a_question_cannot_pass_as_an_approval(scripted, fake):
    pid = make_project()
    scripted(["@@ ask\nWhich?\n- A\n", RUN, "@@ finish\nok\n"])
    run_id = start(pid)
    q = wait_for(lambda: next((e for e in va.get_run(pid, run_id)["events"] if e["type"] == "question"), None))
    with pytest.raises(vp.VibeError):
        va.approve_run(pid, run_id, q["id"], "once", False)           # a question id is not an approval id
    va.answer_run(pid, run_id, q["id"], "A")
    decide(pid, run_id, "decline")
    assert end_of(wait_finished(pid, run_id))["status"] == "done"


def test_a_run_started_away_cannot_be_approved_at_all(scripted, fake):
    pid = make_project()
    scripted(["@@ finish\nok\n"])
    run_id = start(pid, can_run=False)
    with pytest.raises(vp.VibeError) as e:
        va.approve_run(pid, run_id, "a123", "once", False)
    assert e.value.status == 403
    wait_finished(pid, run_id)


def test_stopping_or_waiting_too_long_for_an_approval_ends_the_run_without_running_anything(scripted, fake, cfg, monkeypatch):
    pid = make_project()
    scripted([RUN])
    run_id = start(pid)
    approval_of(pid, run_id)
    va.stop_run(pid, run_id)
    assert end_of(wait_finished(pid, run_id))["status"] == "stopped" and fake.calls == []
    cfg["vibe_agent"] = {"wait_seconds": 5}
    scripted([RUN])
    clock = itertools.count(0, 1)
    monkeypatch.setattr(va, "_mono", lambda: next(clock))
    monkeypatch.setattr(va, "WAIT_STEP", 0.01)
    view = wait_finished(pid, start(pid))
    assert end_of(view)["status"] == "stopped" and "No answer" in end_of(view)["report"] and fake.calls == []


# ── what comes back ──

def test_a_failing_command_is_a_result_not_an_error_and_a_timeout_says_so(scripted, monkeypatch):
    fake = FakeCommands(monkeypatch, cmd.Result(1, "1 failed: test_add\nAssertionError", 2.0))
    pid = make_project()
    model = scripted([RUN, "@@ finish\nok\n"])
    run_id = start(pid)
    decide(pid, run_id)
    view = wait_finished(pid, run_id)
    result = [e for e in view["events"] if e["type"] == "result" and e["tool"] == "run_check"][0]
    assert result["ok"] is False and result["summary"] == "Ran npm test: exit 1 (2.0 s)"
    assert "Exit code 1 after 2.0 s." in model.calls[1][-1]["content"] and "AssertionError" in model.calls[1][-1]["content"]
    fake.result = cmd.Result(None, "", 60.0, timed_out=True)
    model = scripted([RUN, "@@ finish\nok\n"])
    run_id = start(pid)
    ev = approval_of(pid, run_id)
    va.approve_run(pid, run_id, ev["id"], "once", False)
    wait_finished(pid, run_id)
    text = model.calls[1][-1]["content"]
    assert "Timed out after 60 s" in text and "Do not start servers" in text and "(no output)" in text


def test_a_blocked_action_in_the_sandbox_is_explained_to_the_model(scripted, monkeypatch):
    FakeCommands(monkeypatch, cmd.Result(1, "ls: ../outside: Operation not permitted", 0.1, denied=True))
    pid = make_project()
    model = scripted(["@@ run_check\nls ../outside\n", "@@ finish\nok\n"])
    run_id = start(pid)
    decide(pid, run_id)
    wait_finished(pid, run_id)
    assert "The sandbox stopped something" in model.calls[1][-1]["content"] and "no network" in model.calls[1][-1]["content"]


def test_files_a_command_changes_become_a_checkpoint_and_old_reads_go_stale(scripted, monkeypatch):
    FakeCommands(monkeypatch, writes={"report.txt": "built\n"})
    pid = make_project()
    model = scripted(["@@ read_file style.css\n", RUN, "@@ list_files\n", "@@ finish\nok\n"])
    run_id = start(pid)
    decide(pid, run_id)
    view = wait_finished(pid, run_id)
    cps = [e for e in view["events"] if e["type"] == "checkpoint"]
    assert len(cps) == 1 and cps[0]["step"] == 2 and cps[0]["changed"] == ["report.txt"] and cps[0]["label"] == "Agent: ran npm test"
    assert vp.read_file(pid, "report.txt")["content"] == "built\n"
    vp.restore(pid, end_of(view)["start_checkpoint"])
    assert not (vp.project_dir(pid) / "report.txt").exists()


def test_a_command_that_changes_nothing_makes_no_checkpoint(scripted, fake):
    pid = make_project()
    scripted([RUN, "@@ finish\nok\n"])
    run_id = start(pid)
    decide(pid, run_id)
    assert not [e for e in wait_finished(pid, run_id)["events"] if e["type"] == "checkpoint"]


def test_the_audit_for_commands_has_the_program_name_never_the_command_text(scripted, fake, audits):
    pid = make_project()
    scripted(["@@ run_check\nnpm run build --mode=SECRET-ARG-TEXT\n", "@@ run_check\n./b\u00efld.sh --token=abc123\n", "@@ finish\nok\n"])
    run_id = start(pid)
    decide(pid, run_id)
    ev = wait_for(lambda: next((e for e in va.get_run(pid, run_id)["events"] if e["type"] == "approval" and "token" in e["command"]), None))
    va.approve_run(pid, run_id, ev["id"], "once", False)
    wait_finished(pid, run_id)
    blob = json.dumps([x for e, x in audits if e.startswith("vibe_agent")])
    assert "SECRET-ARG-TEXT" not in blob and "abc123" not in blob and "--mode" not in blob
    assert [x["program"] for e, x in audits if e == "vibe_agent_command"] == ["npm", ""]     # a name that is not plain ASCII is left out


def test_the_list_of_commands_survives_in_project_json_next_to_the_rest(scripted, fake):
    pid = make_project()
    before = json.loads((vp.project_dir(pid) / ".vibe" / "project.json").read_text())
    vp.allow_command(pid, "pytest -q", False)
    after = json.loads((vp.project_dir(pid) / ".vibe" / "project.json").read_text())
    assert {k: v for k, v in after.items() if k != "allowed_commands"} == before
    assert after["allowed_commands"] == [{"cmd": "pytest -q", "network": False}]


def test_what_test_runners_leave_behind_is_not_part_of_the_project(scripted, monkeypatch):
    class Leaves(FakeCommands):
        def __call__(self, project, root, command, **kw):
            for d in (".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", "__pycache__", "node_modules"):
                (project / d).mkdir(exist_ok=True)
                (project / d / "x.txt").write_text("left behind\n")
            return super().__call__(project, root, command, **kw)
    Leaves(monkeypatch)
    pid = make_project()
    scripted([RUN, "@@ finish\nok\n"])
    run_id = start(pid)
    decide(pid, run_id)
    view = wait_finished(pid, run_id)
    assert not [e for e in view["events"] if e["type"] == "checkpoint"]
    assert sorted(f["path"] for f in vp.list_files(pid)) == sorted(["index.html", "style.css", "app.js"])
