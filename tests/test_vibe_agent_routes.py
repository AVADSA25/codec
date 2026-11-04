"""Vibe coding agent, step 3a: the HTTP routes (routes/vibe_agent.py) on top of a scripted model."""
from __future__ import annotations

import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import codec_vibe_agent as va
import codec_vibe_agent_cmd as cmd
import codec_vibe_projects as vp
from routes.vibe_agent import router
from vibe_agent_fakes import (  # noqa: F401  (fixtures are used by name)
    agent_env, audits, blocker, cfg, make_project, scripted, wait_for,
)
from route_table import route_methods


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def start(client, pid, goal="Do it", **extra):
    return client.post(f"/api/vibe/projects/{pid}/agent/run", json={"goal": goal, **extra})


def finish_view(client, pid, run_id, timeout=10.0):
    end = time.time() + timeout
    since, events = 0, []
    while time.time() < end:
        r = client.get(f"/api/vibe/projects/{pid}/agent/{run_id}", params={"since": since, "wait": 0.3})
        assert r.status_code == 200
        body = r.json()
        events += body["events"]
        since = body["next"]
        if body["finished"]:
            return body, events
    raise AssertionError("the run did not finish")


def test_start_poll_and_read_the_result(client, scripted):
    pid = make_project()
    scripted(["@@ read_file style.css\n", "@@ finish\nLooked at the stylesheet.\n"])
    r = start(client, pid)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "running" and body["model"] == {"kind": "local", "label": "Local"}
    assert body["limits"] == {"max_steps": 30, "max_seconds": 900}
    last, events = finish_view(client, pid, body["run"])
    assert last["status"] == "done" and events[-1]["type"] == "end" and events[-1]["report"] == "Looked at the stylesheet."
    listing = client.get(f"/api/vibe/projects/{pid}/agent/runs").json()
    assert listing[0]["run"] == body["run"] and listing[0]["status"] == "done" and listing[0]["steps"] == 2


def test_models_endpoint(client, cfg):
    cfg.update(llm_base_url="http://localhost:8083/v1", llm_model="qwen-local")
    r = client.get("/api/vibe/agent/models")
    assert r.status_code == 200 and r.json()["local"] == {"label": "Local", "model": "qwen-local"} and r.json()["cloud"] is None


def test_bad_requests(client, scripted):
    pid = make_project()
    assert client.post(f"/api/vibe/projects/{pid}/agent/run", content=b"not json").status_code == 400
    assert client.post(f"/api/vibe/projects/{pid}/agent/run", json=["a"]).status_code == 400
    assert start(client, pid, goal="").status_code == 400
    assert start(client, "no-such-project").status_code == 404
    assert start(client, pid, model="huge").status_code == 400
    assert client.post(f"/api/vibe/projects/{pid}/agent/r00000000/answer", content=b"x").status_code == 400


def test_a_cloud_run_needs_confirm_cloud_to_be_exactly_true(client, scripted):
    pid = make_project()
    for value in (None, "yes", 1, False):
        extra = {"model": "cloud"} if value is None else {"model": "cloud", "confirm_cloud": value}
        r = start(client, pid, **extra)
        assert r.status_code == 400 and "confirmation" in r.json()["error"]


def test_a_second_run_is_refused_while_one_is_working(client, scripted):
    pid = make_project()
    model = scripted(["@@ finish\nok\n"])
    gate = blocker()
    model.pause_after = (1, gate)
    first = start(client, pid).json()["run"]
    wait_for(lambda: model.calls)
    r = start(client, pid)
    assert r.status_code == 409 and "already working" in r.json()["error"]
    with pytest.raises(vp.VibeError) as e:
        vp.write_file(pid, "a.txt", "x")
    assert e.value.status == 409
    gate.set()
    finish_view(client, pid, first)


def test_stop_route_ends_the_run(client, scripted):
    pid = make_project()
    model = scripted(["@@ finish\nok\n"])
    gate = blocker()
    model.pause_after = (1, gate)
    run_id = start(client, pid).json()["run"]
    wait_for(lambda: model.calls)
    r = client.post(f"/api/vibe/projects/{pid}/agent/{run_id}/stop")
    assert r.status_code == 200 and r.json()["stopping"] is True
    gate.set()
    last, events = finish_view(client, pid, run_id)
    assert last["status"] == "stopped" and events[-1]["status"] == "stopped"


def test_answer_route_takes_text_or_a_choice(client, scripted):
    pid = make_project()
    model = scripted(["@@ ask\nWhich colour?\n- Blue\n- Green\n", "@@ finish\nok\n"])
    run_id = start(client, pid).json()["run"]
    q = wait_for(lambda: next((e for e in client.get(f"/api/vibe/projects/{pid}/agent/{run_id}").json()["events"] if e["type"] == "question"), None))
    assert client.post(f"/api/vibe/projects/{pid}/agent/{run_id}/answer", json={"id": q["id"]}).status_code == 400
    assert client.post(f"/api/vibe/projects/{pid}/agent/{run_id}/answer", json={"id": "q-wrong", "choice": "Blue"}).status_code == 409
    r = client.post(f"/api/vibe/projects/{pid}/agent/{run_id}/answer", json={"id": q["id"], "choice": "Green"})
    assert r.status_code == 200
    finish_view(client, pid, run_id)
    assert "The owner answered: Green" in model.calls[1][-1]["content"]


def test_unknown_runs_are_404(client):
    pid = make_project()
    for run_id in ("r00000000", "r12", "nonsense"):
        assert client.get(f"/api/vibe/projects/{pid}/agent/{run_id}").status_code == 404
        assert client.post(f"/api/vibe/projects/{pid}/agent/{run_id}/stop").status_code == 404
    assert client.get("/api/vibe/projects/no-such-project/agent/runs").status_code == 404


def test_switched_off_means_404_everywhere(client, scripted, monkeypatch):
    pid = make_project()
    monkeypatch.setenv("VIBE_AGENT_ENABLED", "false")
    assert start(client, pid).status_code == 404
    assert client.get(f"/api/vibe/projects/{pid}/agent/runs").status_code == 404
    assert client.get("/api/vibe/agent/models").status_code == 404
    assert client.get(f"/api/vibe/projects/{pid}/agent/r00000000").status_code == 404
    monkeypatch.delenv("VIBE_AGENT_ENABLED")
    monkeypatch.setenv("VIBE_PROJECTS_ENABLED", "false")
    assert start(client, pid).status_code == 404


def test_a_run_started_through_the_tunnel_cannot_run_commands(client, scripted):
    pid = make_project()
    scripted(["@@ finish\nok\n", "@@ finish\nok\n"])
    remote = client.post(f"/api/vibe/projects/{pid}/agent/run", json={"goal": "from the phone"},
                         headers={"cf-connecting-ip": "203.0.113.9"}).json()["run"]
    _, events = finish_view(client, pid, remote)
    assert [e for e in events if e["type"] == "start"][0]["can_run"] is False
    local = start(client, pid, goal="at the Mac").json()["run"]
    _, events = finish_view(client, pid, local)
    assert [e for e in events if e["type"] == "start"][0]["can_run"] is True


def test_the_dashboard_registers_the_agent_routes_behind_its_login():
    import codec_dashboard
    paths = set(route_methods(codec_dashboard.app))
    for path in ("/api/vibe/agent/models", "/api/vibe/agent/live", "/api/vibe/projects/{pid}/agent/run", "/api/vibe/projects/{pid}/agent/runs",
                 "/api/vibe/projects/{pid}/agent/{run_id}", "/api/vibe/projects/{pid}/agent/{run_id}/stop",
                 "/api/vibe/projects/{pid}/agent/{run_id}/answer", "/api/vibe/projects/{pid}/agent/{run_id}/message",
                 "/api/vibe/projects/{pid}/agent/{run_id}/continue"):
        assert path in paths
    # none of them is on the list of paths that skip the login
    auth = codec_dashboard.AuthMiddleware
    sample = "/api/vibe/projects/some-project/agent/r0123abcd"
    assert sample not in auth.PUBLIC_ROUTES and not any(sample.startswith(p) for p in auth.PUBLIC_PREFIXES)
    assert "/api/vibe/agent/models" not in auth.PUBLIC_ROUTES and "/api/vibe/agent/live" not in auth.PUBLIC_ROUTES


# ── commands: approval and the list of allowed ones (Mac only) ──

REMOTE = {"cf-connecting-ip": "203.0.113.9"}


@pytest.fixture
def commands_on(monkeypatch):
    calls = []
    monkeypatch.setattr(cmd, "available", lambda: True)
    monkeypatch.setattr(va, "_run_command", lambda project, root, command, **kw: calls.append((command, kw["network"])) or cmd.Result(0, "ok", 0.1))
    return calls


def approval(client, pid, run_id):
    return wait_for(lambda: next((e for e in client.get(f"/api/vibe/projects/{pid}/agent/{run_id}").json()["events"] if e["type"] == "approval"), None))


def test_the_owner_approves_a_command_over_http_at_the_mac(client, scripted, commands_on):
    pid = make_project()
    scripted(["@@ run_check\nnpm test\n", "@@ finish\nok\n"])
    run_id = start(client, pid).json()["run"]
    ev = approval(client, pid, run_id)
    assert ev["command"] == "npm test" and commands_on == []
    url = f"/api/vibe/projects/{pid}/agent/{run_id}/approve"
    assert client.post(url, content=b"nope").status_code == 400
    assert client.post(url, json={"id": "a-wrong", "decision": "once"}).status_code == 409
    assert client.post(url, json={"id": ev["id"], "decision": "maybe"}).status_code == 400
    r = client.post(url, json={"id": ev["id"], "decision": "once", "network": "true"})          # only the boolean true counts
    assert r.status_code == 200
    finish_view(client, pid, run_id)
    assert commands_on == [("npm test", False)]


def test_nothing_about_commands_is_open_through_the_tunnel(client, scripted, commands_on):
    pid = make_project()
    vp.allow_command(pid, "npm run lint", False)
    scripted(["@@ run_check\nnpm test\n", "@@ finish\nok\n"])
    run_id = start(client, pid).json()["run"]
    ev = approval(client, pid, run_id)
    base = f"/api/vibe/projects/{pid}/agent"
    assert client.post(f"{base}/{run_id}/approve", json={"id": ev["id"], "decision": "once"}, headers=REMOTE).status_code == 403
    assert client.get(f"{base}/commands", headers=REMOTE).status_code == 403
    assert client.post(f"{base}/commands/forget", json={"command": "npm run lint"}, headers=REMOTE).status_code == 403
    assert client.post(f"{base}/{run_id}/approve", json={"id": ev["id"], "decision": "decline"}).status_code == 200
    finish_view(client, pid, run_id)
    assert vp.allowed_commands(pid) == [{"cmd": "npm run lint", "network": False}] and commands_on == []


def test_a_run_started_through_the_tunnel_cannot_be_approved_even_at_the_mac(client, scripted, commands_on):
    pid = make_project()
    scripted(["@@ finish\nok\n"])
    run_id = client.post(f"/api/vibe/projects/{pid}/agent/run", json={"goal": "from the phone"}, headers=REMOTE).json()["run"]
    finish_view(client, pid, run_id)
    r = client.post(f"/api/vibe/projects/{pid}/agent/{run_id}/approve", json={"id": "a1", "decision": "once"})
    assert r.status_code == 403 and "started at this Mac" in r.json()["error"]


def test_the_list_of_allowed_commands_can_be_read_and_a_command_forgotten(client):
    pid = make_project()
    vp.allow_command(pid, "npm test", False)
    vp.allow_command(pid, "npm install", True)
    base = f"/api/vibe/projects/{pid}/agent/commands"
    assert client.get(base).json() == [{"cmd": "npm test", "network": False}, {"cmd": "npm install", "network": True}]
    assert client.post(f"{base}/forget", json={"command": "npm install"}).json() == [{"cmd": "npm test", "network": False}]
    assert client.post(f"{base}/forget", json={"command": "npm install"}).status_code == 404
    assert client.post(f"{base}/forget", json={"command": 5}).status_code == 400
    assert client.post(f"{base}/forget", content=b"x").status_code == 400
    assert client.get("/api/vibe/projects/no-such-project/agent/commands").status_code == 404


# ── Continue and the owner's note (P-044) ──

def cut_log(pid, run_id, keep_step):
    """As after a dashboard restart: the log keeps the steps up to keep_step and loses its end."""
    import json
    path = vp.project_dir(pid) / ".vibe" / "runs" / f"{run_id}.jsonl"
    evs = [json.loads(x) for x in path.read_text().splitlines()]
    evs = [e for e in evs if e["type"] != "end" and (e.get("step") is None or e["step"] <= keep_step)]
    path.write_text("".join(json.dumps(e) + "\n" for e in evs))
    va._RUNS.clear()


def start_event(events):
    return [e for e in events if e["type"] == "start"][0]


def test_continue_route_can_run_follows_the_request_origin(client, scripted):
    pid = make_project()
    scripted(["@@ list_files\n", "@@ finish\nok\n"])
    first = start(client, pid, goal="at the Mac").json()["run"]
    finish_view(client, pid, first)
    cut_log(pid, first, 1)
    last, _ = finish_view(client, pid, first)
    assert last["status"] == "interrupted" and last["continuable"] is True
    scripted(["@@ list_files\n", "@@ finish\nok\n"])
    r = client.post(f"/api/vibe/projects/{pid}/agent/{first}/continue", json={}, headers=REMOTE)
    assert r.status_code == 200 and r.json()["continues"] == first and r.json()["earlier_steps"] == 1
    away = r.json()["run"]
    _, events = finish_view(client, pid, away)
    assert start_event(events)["can_run"] is False and start_event(events)["continues"] == first
    cut_log(pid, away, 2)
    scripted(["@@ finish\nok\n"])
    home = client.post(f"/api/vibe/projects/{pid}/agent/{away}/continue", json={}).json()["run"]
    _, events = finish_view(client, pid, home)
    assert start_event(events)["can_run"] is True
    assert client.post(f"/api/vibe/projects/{pid}/agent/{home}/continue", json={}).status_code == 409    # a done run
    assert client.post(f"/api/vibe/projects/{pid}/agent/{home}/continue", content=b"x").status_code == 400
    assert client.post(f"/api/vibe/projects/{pid}/agent/r00000000/continue", json={}).status_code == 404


def test_continue_route_needs_confirm_cloud_exactly_true_for_a_cloud_run(client, scripted, cfg, monkeypatch):
    import codec_cloud_models as ccm
    cfg["extra_models"] = [{"id": "mimo", "label": "MiMo", "base_url": "https://api.example.com/v1", "key_slot": "mimo_api_key",
                            "price_in_per_m": 1, "price_out_per_m": 1}]
    monkeypatch.setattr(ccm, "block_message", lambda entry: None)
    pid = make_project()
    scripted(["@@ list_files\n", "@@ finish\nok\n"])
    first = start(client, pid, model="cloud", confirm_cloud=True).json()["run"]
    finish_view(client, pid, first)
    cut_log(pid, first, 1)
    url = f"/api/vibe/projects/{pid}/agent/{first}/continue"
    for value in (None, "yes", 1, False):
        r = client.post(url, json={} if value is None else {"confirm_cloud": value})
        assert r.status_code == 400 and "confirmation" in r.json()["error"]
    scripted(["@@ finish\nok\n"])
    r = client.post(url, json={"confirm_cloud": True})
    assert r.status_code == 200 and r.json()["model"] == {"kind": "cloud", "label": "MiMo"}
    finish_view(client, pid, r.json()["run"])


def test_message_route_works_from_the_tunnel_and_checks_its_text(client, scripted):
    pid = make_project()
    model = scripted(["@@ list_files\n", "@@ finish\nok\n"])
    gate = blocker()
    model.pause_after = (1, gate)
    run_id = start(client, pid).json()["run"]
    wait_for(lambda: model.calls)
    url = f"/api/vibe/projects/{pid}/agent/{run_id}/message"
    assert client.post(url, content=b"x").status_code == 400
    assert client.post(url, json={"text": "   "}).status_code == 400
    assert client.post(url, json={"text": 7}).status_code == 400
    assert client.post(url, json={"text": "x" * 301}).status_code == 400
    assert client.post(f"/api/vibe/projects/{pid}/agent/r00000000/message", json={"text": "hi"}).status_code == 404
    r = client.post(url, json={"text": "y" * 300}, headers=REMOTE)          # not Mac-only, like /answer
    assert r.status_code == 200 and r.json()["replaced"] is False
    gate.set()
    _, events = finish_view(client, pid, run_id)
    assert [e["text"] for e in events if e["type"] == "message"] == ["y" * 300]
    assert "The owner says: " + "y" * 300 in model.calls[1][-1]["content"]
    assert client.post(url, json={"text": "late"}).status_code == 409


def test_raw_replies_come_only_when_asked_by_name(client, scripted):
    pid = make_project()
    token = "Bearer " + "k9" * 15                   # a secret shape, built here so that no key-like text sits in the repo
    write = f"I will add the notes.\n@@ write_file notes.txt\n<<<<<<< CONTENT\nAuthorization: {token}\n>>>>>>> END\n"
    replies = ["Hmm.\n@@ read_file style.css\n", write, "@@ finish\nAdded notes.txt.\n"]
    scripted(replies)
    run_id = start(client, pid).json()["run"]
    last, events = finish_view(client, pid, run_id)
    url = f"/api/vibe/projects/{pid}/agent/{run_id}"
    plain = client.get(url).json()
    assert "replies" not in last and "replies" not in plain                       # the default view never carries them
    assert all(e["type"] != "reply" for e in events + plain["events"])
    every = client.get(url, params={"replies": 0}).json()
    assert [r["step"] for r in every["replies"]] == [1, 2, 3] and every["events"] == plain["events"]
    assert every["replies"][0]["text"] == replies[0] and every["replies"][0]["redacted"] is False
    assert every["replies"][1]["redacted"] is True and every["replies"][1]["chars"] == len(write)
    assert "<REDACTED:bearer_token>" in every["replies"][1]["text"] and token not in json.dumps(every)
    assert [r["step"] for r in client.get(url, params={"replies": 2}).json()["replies"]] == [3]
    assert client.get(url, params={"replies": "x"}).status_code == 422
    assert client.get(f"/api/vibe/projects/{pid}/agent/r00000000", params={"replies": 0}).status_code == 404
