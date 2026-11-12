"""E-02: approval grants only the network domains a plan lists.

approve_plan used to add "*" to every plan that declared a domain or used a web
skill (#227), so the P-098 task-host check bound almost no agent. Now the
granted domains are the plan's own list; a host outside it blocks the run and
the owner grants it from the Inbox (POST /api/agents/{id}/grant), after which
the gate lets it through. Nothing here opens Chrome, fetches a page or touches
~/.codec.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

import codec_agent_plan as cap
from codec_agent_runner import Action, PermissionViolation, permission_gate

NO_GLOBAL = {"skills": [], "network_domains": [], "read_paths": [], "write_paths": []}


@pytest.fixture
def agents_dir(tmp_path, monkeypatch):
    import codec_audit
    monkeypatch.setattr(cap, "_CODEC_DIR", tmp_path)
    monkeypatch.setattr(cap, "_AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(cap, "_GLOBAL_GRANTS_PATH", tmp_path / "agent_global_grants.json")
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", tmp_path / "audit.log")
    reg = MagicMock()
    reg.names.return_value = ["web_fetch", "chrome_open", "web_search", "file_ops"]
    monkeypatch.setattr("codec_dispatch.registry", reg, raising=False)
    return tmp_path / "agents"


def _waiting_plan(agents_dir, domains, skills):
    d = agents_dir / "e02"
    d.mkdir(parents=True)
    (d / "plan.json").write_text(json.dumps({
        "schema": 1, "agent_id": "e02", "goals": ["Read a page"],
        "checkpoints": [{"id": "c1", "title": "Read", "description": "...",
                         "skills_needed": list(skills), "expected_output": "notes",
                         "step_budget": 60}],
        "permission_manifest": {"read_paths": [], "write_paths": ["~/codec-projects/e02/**"],
                                "network_domains": list(domains), "skills": list(skills),
                                "destructive_ops": []},
        "estimated_duration_minutes": 5, "assumptions": [],
    }))
    cap.save_manifest("e02", {"agent_id": "e02", "status": "awaiting_approval", "title": "x"})


@pytest.mark.parametrize("domains,skills", [
    (["example.com"], ["web_fetch"]),
    (["example.com", "docs.example.org"], ["chrome_open", "file_ops"]),
    ([], ["web_search"]),          # a web skill and no domain: still no "*"
])
def test_approval_grants_only_the_listed_domains(agents_dir, domains, skills):
    _waiting_plan(agents_dir, domains, skills)
    grants = cap.approve_plan("e02")
    assert grants["network_domains"] == domains
    assert cap.load_grants("e02")["network_domains"] == domains


def test_unlisted_host_blocks_then_inbox_grant_lets_it_through(agents_dir):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.agents import router
    from routes.inbox import _agent_actions

    _waiting_plan(agents_dir, ["example.com"], ["web_fetch"])
    cap.approve_plan("e02")
    listed = Action(skill="web_fetch", task="fetch https://example.com/a")
    other = Action(skill="web_fetch", task="fetch https://news.example.net/b")
    permission_gate(listed, cap.load_grants("e02"), NO_GLOBAL)
    with pytest.raises(PermissionViolation) as e:
        permission_gate(other, cap.load_grants("e02"), NO_GLOBAL)
    assert (e.value.reason, e.value.needed) == ("domain_not_authorized", "news.example.net")

    # The runner's block message, as the Inbox turns it into a Grant button.
    m = cap.load_manifest("e02")
    m["status"] = "blocked_on_permission"
    cap.save_manifest("e02", m)
    note = {"agent_id": "e02", "type": "agent_blocked", "title": f"Blocked: {e.value.reason}",
            "actions": [{"label": "Grant", "endpoint": "/api/agents/e02/grant",
                         "body_hint": {"kind": "<infer from reason>", "value": e.value.needed}}]}
    [button] = _agent_actions(note, "blocked_on_permission")
    assert button["body"] == {"kind": "network_domains", "value": "news.example.net"}

    app = FastAPI()
    app.include_router(router)
    r = TestClient(app).post(button["endpoint"], json=button["body"])
    assert r.status_code == 200 and r.json()["status"] == "running"
    assert cap.load_grants("e02")["network_domains"] == ["example.com", "news.example.net"]
    permission_gate(other, cap.load_grants("e02"), NO_GLOBAL)
