"""Vibe coding agent, step 3a: the HTTP routes (routes/vibe_agent.py) on top of a scripted model."""
from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import codec_vibe_projects as vp
from routes.vibe_agent import router
from vibe_agent_fakes import (  # noqa: F401  (fixtures are used by name)
    agent_env, audits, blocker, cfg, make_project, scripted, wait_for,
)


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
    paths = {getattr(r, "path", "") for r in codec_dashboard.app.routes}
    for path in ("/api/vibe/agent/models", "/api/vibe/projects/{pid}/agent/run", "/api/vibe/projects/{pid}/agent/runs",
                 "/api/vibe/projects/{pid}/agent/{run_id}", "/api/vibe/projects/{pid}/agent/{run_id}/stop",
                 "/api/vibe/projects/{pid}/agent/{run_id}/answer"):
        assert path in paths
    # none of them is on the list of paths that skip the login
    auth = codec_dashboard.AuthMiddleware
    sample = "/api/vibe/projects/some-project/agent/r0123abcd"
    assert sample not in auth.PUBLIC_ROUTES and not any(sample.startswith(p) for p in auth.PUBLIC_PREFIXES)
    assert "/api/vibe/agent/models" not in auth.PUBLIC_ROUTES
