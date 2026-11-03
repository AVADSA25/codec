"""Vibe agent: Continue a run from its log, and steer a running run with the owner's note (P-044).

A scripted model stands in for the real one (tests/vibe_agent_fakes.py); the command sandbox is a recording fake.
A dashboard restart is simulated the way tests/test_vibe_agent.py does: the run's log loses its end event and the
in-memory runs are forgotten.
"""
from __future__ import annotations

import json

import pytest

import codec_vibe_agent as va
import codec_vibe_agent_cmd as cmd
import codec_vibe_agent_proto as proto
import codec_vibe_projects as vp
from vibe_agent_fakes import (  # noqa: F401  (fixtures are used by name)
    agent_env, audits, blocker, cfg, end_of, kinds, make_project, run_script, scripted, wait_finished, wait_for,
)

READ = "I will read the stylesheet.\n@@ read_file style.css\n"
CSS_EDIT = ("Make the heading blue.\n@@ edit_file style.css\n<<<<<<< SEARCH\nh1 { color: red; }\n=======\n"
            "h1 { color: blue; }\n>>>>>>> REPLACE\n")
CLOUD = {"id": "mimo", "label": "MiMo", "base_url": "https://api.example.com/v1", "key_slot": "mimo_api_key",
         "price_in_per_m": 1, "price_out_per_m": 1}


def log_path(pid, run_id):
    return vp.project_dir(pid) / ".vibe" / "runs" / f"{run_id}.jsonl"


def logged(pid, run_id):
    return [json.loads(x) for x in log_path(pid, run_id).read_text().splitlines()]


def cut(pid, run_id, keep_step, drop_result=False):
    """As after a dashboard restart in step keep_step: the log keeps that step and the ones before, never the end.
    With drop_result the step itself was cut in the middle: its reply and tool events stay, its result does not."""
    evs = [e for e in logged(pid, run_id) if e["type"] != "end" and (e.get("step") is None or e["step"] <= keep_step)]
    if drop_result:
        evs = [e for e in evs if not (e.get("step") == keep_step and e["type"] in ("result", "checkpoint"))]
    log_path(pid, run_id).write_text("".join(json.dumps(e) + "\n" for e in evs))
    va._RUNS.clear()


def roles_and_texts(call):
    return [(m["role"], m["content"]) for m in call]


def assistants(call):
    return [m["content"] for m in call if m["role"] == "assistant"]


# ── Continue ──

def test_a_run_cut_mid_way_reads_interrupted_and_continuable(scripted):
    pid = make_project()
    view, _ = run_script(pid, [READ, CSS_EDIT, "@@ finish\nok\n"], scripted)
    done = va.get_run(pid, view["run"])
    assert done["status"] == "done" and done["continuable"] is False          # a finished goal offers no Continue
    cut(pid, view["run"], 2)
    again = va.get_run(pid, view["run"])
    assert again["status"] == "interrupted" and again["finished"] and again["continuable"] is True
    assert va.list_runs(pid)[0]["status"] == "interrupted"


def test_continue_first_prompt_holds_the_old_replies_and_results(scripted):
    pid = make_project()
    view, _ = run_script(pid, [READ, CSS_EDIT, "@@ finish\nok\n"], scripted, goal="Make the heading blue")
    old = view["run"]
    cut(pid, old, 2)
    model = scripted(["@@ finish\nThe heading is blue.\n"])
    started = va.continue_run(pid, old)
    assert started["continues"] == old and started["earlier_steps"] == 2
    new = wait_finished(pid, started["run"])
    first = model.calls[0]
    assert assistants(first) == [READ, CSS_EDIT]                             # the raw replies of the cut run, in order
    users = [t for r, t in roles_and_texts(first) if r == "user"]
    assert "GOAL\nMake the heading blue" in users[0] and "continues an earlier run" in users[0]
    assert users[1].startswith("[RESULT 1 read_file style.css]") and "has changed since" in users[1]   # step 2 edited it
    assert users[2].startswith("[RESULT 2 edit_file style.css (1 edit)]") and "h1 { color: blue; }" in users[2]
    tail = users[2].split("[END RESULT]", 1)[1]
    assert "The earlier run stopped here: CODEC was restarted." in tail and "PROJECT FILES" in tail
    start = next(e for e in new["events"] if e["type"] == "start")
    assert start["continues"] == old and start["earlier_steps"] == 2
    assert [e["step"] for e in new["events"] if e["type"] == "tool"] == [3]                 # numbered on
    assert end_of(new)["status"] == "done" and end_of(new)["steps"] == 1
    assert vp.read_file(pid, "style.css")["content"].endswith("h1 { color: blue; }\n")


def test_continue_has_a_fresh_step_budget(scripted, cfg):
    cfg["vibe_agent"] = {"max_steps": 2}
    pid = make_project()
    view, _ = run_script(pid, ["@@ list_files\n", "@@ read_file app.js\n", "@@ finish\nnever\n"], scripted)
    assert end_of(view)["status"] == "limit" and va.get_run(pid, view["run"])["continuable"] is True
    model = scripted(["@@ read_file index.html\n", "@@ read_file style.css\n", "@@ finish\nnever\n"])
    new = wait_finished(pid, va.continue_run(pid, view["run"])["run"])
    assert end_of(new)["status"] == "limit" and end_of(new)["steps"] == 2 and len(model.calls) == 2
    assert [e["step"] for e in new["events"] if e["type"] == "tool"] == [3, 4]
    assert "The earlier run stopped here: Stopped at the limit of 2 steps" in model.calls[0][-1]["content"]


@pytest.fixture
def commands(monkeypatch):
    calls = []
    monkeypatch.setattr(cmd, "available", lambda: True)
    monkeypatch.setattr(va, "_run_command", lambda project, root, command, **kw: calls.append(command) or cmd.Result(0, "ok", 0.1))
    return calls


def approval_in(pid, run_id, seen=()):
    return wait_for(lambda: next((e for e in va.get_run(pid, run_id)["events"] if e["type"] == "approval" and e["id"] not in seen), None))


def test_a_cut_run_check_is_never_replayed_and_asking_again_needs_a_new_approval(scripted, commands):
    pid = make_project()
    scripted(["@@ run_check\nnpm test\n", "@@ run_check\nnpm run build\n", "@@ finish\nok\n"])
    old = va.start_run(pid, "Build it", can_run=True)["run"]
    first = approval_in(pid, old)
    va.approve_run(pid, old, first["id"], "once")
    va.approve_run(pid, old, approval_in(pid, old, (first["id"],))["id"], "once")
    wait_finished(pid, old)
    assert commands == ["npm test", "npm run build"]
    cut(pid, old, 2, drop_result=True)                 # the restart came after the owner allowed npm run build
    assert [e["type"] for e in logged(pid, old) if e.get("step") == 2] == ["reply", "tool", "approval", "decision"]
    model = scripted(["Build again.\n@@ run_check\nnpm run build\n", "@@ finish\nNot checked.\n"])
    new = va.continue_run(pid, old, can_run=True)["run"]
    ask = approval_in(pid, new)                          # a fresh approval card: the old "once" does not carry over
    assert ask["step"] == 3 and ask["command"] == "npm run build"
    assert commands == ["npm test", "npm run build"]    # Continue ran nothing, and nothing runs before the owner answers
    cut_result = [t for r, t in roles_and_texts(model.calls[0]) if r == "user" and t.startswith("[RESULT 2 ")][0]
    assert cut_result.startswith("[RESULT 2 run_check npm run build: FAILED]") and "It is not run again by itself" in cut_result
    va.approve_run(pid, new, ask["id"], "decline")
    view = wait_finished(pid, new)
    assert end_of(view)["status"] == "done" and commands == ["npm test", "npm run build"]


def test_continue_of_a_cloud_run_needs_confirm_cloud_again(scripted, cfg, monkeypatch):
    import codec_cloud_models as ccm
    cfg["extra_models"] = [CLOUD]
    monkeypatch.setattr(ccm, "block_message", lambda entry: None)
    pid = make_project()
    view, _ = run_script(pid, [READ, "@@ finish\nok\n"], scripted, model="cloud", confirm_cloud=True)
    cut(pid, view["run"], 1)
    for value in (False, None):
        with pytest.raises(vp.VibeError) as e:
            va.continue_run(pid, view["run"], **({} if value is None else {"confirm_cloud": value}))
        assert e.value.status == 400 and "confirmation" in e.value.message
    model = scripted(["@@ finish\nok\n"])
    new = wait_finished(pid, va.continue_run(pid, view["run"], confirm_cloud=True)["run"])
    assert model.kwargs[0]["base_url"] == "https://api.example.com/v1"
    assert next(e for e in new["events"] if e["type"] == "start")["model"] == {"kind": "cloud", "label": "MiMo"}


def test_continue_is_refused_for_a_done_run_a_live_one_and_an_older_one(scripted):
    pid = make_project()
    done, _ = run_script(pid, ["@@ finish\nok\n"], scripted)
    with pytest.raises(vp.VibeError) as e:
        va.continue_run(pid, done["run"])
    assert e.value.status == 409 and "Start a new one" in e.value.message
    older, _ = run_script(pid, [READ, "@@ finish\nok\n"], scripted)
    cut(pid, older["run"], 1)
    run_script(pid, ["@@ finish\nok\n"], scripted)
    with pytest.raises(vp.VibeError) as e:
        va.continue_run(pid, older["run"])
    assert e.value.status == 409 and "newest" in e.value.message
    assert va.get_run(pid, older["run"])["continuable"] is False
    gate = blocker()
    model = scripted(["@@ finish\nok\n"])
    model.pause_after = (1, gate)
    live = va.start_run(pid, "g")["run"]
    wait_for(lambda: model.calls)
    with pytest.raises(vp.VibeError) as e:
        va.continue_run(pid, live)
    assert e.value.status == 409 and "still going" in e.value.message
    gate.set()
    wait_finished(pid, live)
    with pytest.raises(vp.VibeError) as e:
        va.continue_run(pid, "r00000000")
    assert e.value.status == 404


def test_a_chain_of_continues_keeps_the_first_runs_turns(scripted):
    pid = make_project()
    first, _ = run_script(pid, [READ, "@@ finish\nok\n"], scripted, goal="Fix the page")
    cut(pid, first["run"], 1)
    scripted(["I will read the script.\n@@ read_file app.js\n", "@@ finish\nok\n"])
    second = va.continue_run(pid, first["run"])["run"]
    wait_finished(pid, second)
    cut(pid, second, 2)
    model = scripted(["@@ finish\nok\n"])
    third = wait_finished(pid, va.continue_run(pid, second)["run"])
    assert assistants(model.calls[0]) == [READ, "I will read the script.\n@@ read_file app.js\n"]
    assert [e["step"] for e in third["events"] if e["type"] == "tool"] == [3]
    assert next(e for e in third["events"] if e["type"] == "start")["goal"] == "Fix the page"


# ── the owner's note to a running run ──

def paused(scripted, pid, replies):
    gate = blocker()
    model = scripted(list(replies))
    model.pause_after = (1, gate)
    run_id = va.start_run(pid, "Make the heading blue")["run"]
    wait_for(lambda: model.calls)
    return model, gate, run_id


def test_a_note_reaches_the_next_prompt_after_the_result_wrapper(scripted):
    pid = make_project()
    model, gate, run_id = paused(scripted, pid, [READ, "@@ finish\nok\n"])
    got = va.message_run(pid, run_id, "  Use teal instead of blue.  ")
    assert got["replaced"] is False and got["id"].startswith("m")
    gate.set()
    view = wait_finished(pid, run_id)
    last = model.calls[1][-1]["content"]
    assert last.startswith("[RESULT 1 read_file style.css]")
    data, after = last.split("\n[END RESULT]", 1)
    assert "Use teal" not in data                                            # never inside the data
    assert after.startswith("\n\nThe owner says: Use teal instead of blue.\n\nPROJECT FILES")
    msg = [e for e in view["events"] if e["type"] == "message"]
    assert len(msg) == 1 and msg[0]["step"] == 1 and msg[0]["id"] == got["id"] and msg[0]["text"] == "Use teal instead of blue."
    assert [e for e in logged(pid, run_id) if e["type"] == "message"][0]["text"] == "Use teal instead of blue."
    assert "Use teal" not in model.calls[0][-1]["content"]                 # sent during step 1: read at step 2


def test_a_newer_note_replaces_one_not_read_yet(scripted):
    pid = make_project()
    model, gate, run_id = paused(scripted, pid, [READ, "@@ finish\nok\n"])
    a = va.message_run(pid, run_id, "Use teal.")
    b = va.message_run(pid, run_id, "Use green.")
    assert a["replaced"] is False and b["replaced"] is True and a["id"] != b["id"]
    gate.set()
    view = wait_finished(pid, run_id)
    assert "The owner says: Use green." in model.calls[1][-1]["content"] and "teal" not in model.calls[1][-1]["content"]
    assert [e["id"] for e in view["events"] if e["type"] == "message"] == [b["id"]]


def test_a_note_the_run_never_reads_sends_nothing_and_a_finished_run_takes_none(scripted):
    pid = make_project()
    model, gate, run_id = paused(scripted, pid, ["@@ finish\nDone at once.\n"])
    va.message_run(pid, run_id, "Use teal.")
    gate.set()
    view = wait_finished(pid, run_id)
    assert end_of(view)["status"] == "done" and len(model.calls) == 1
    assert not [e for e in view["events"] if e["type"] == "message"]       # the page sends it as the next goal
    with pytest.raises(vp.VibeError) as e:
        va.message_run(pid, run_id, "too late")
    assert e.value.status == 409


def test_a_note_is_put_back_after_its_result_when_the_run_is_continued(scripted):
    pid = make_project()
    model, gate, run_id = paused(scripted, pid, [READ, "@@ list_files\n", "@@ finish\nok\n"])
    va.message_run(pid, run_id, "Keep the red.")
    gate.set()
    wait_finished(pid, run_id)
    cut(pid, run_id, 2)
    model = scripted(["@@ finish\nok\n"])
    wait_finished(pid, va.continue_run(pid, run_id)["run"])
    result1 = [t for r, t in roles_and_texts(model.calls[0]) if r == "user" and t.startswith("[RESULT 1 ")][0]
    assert result1.split("\n[END RESULT]", 1)[1].strip() == proto.owner_note("Keep the red.")
