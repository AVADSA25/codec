"""R-02: the plan path blocklist compares case-folded, refuses a grant that
contains a blocked folder, and anchors the system folders at `/`.

`_is_path_blocklisted` (typed paths, plan_from_dict / approve_plan, the /grant
and global-grant routes) compared path segments exactly, so `~/.SSH/**` passed
on the Mac's case-insensitive disk; a parent such as `~/**` or `~/.codec/**`
passed, and the runner's realpath prefix gate then let the agent into
`~/.ssh`; and `/var/` matched a folder of that name anywhere, so
`~/work/var/site` was refused. The D-16 segment rule stays: `notes_ssh` is not
`.ssh`. Nothing here touches ~/.codec.
"""
from __future__ import annotations

import json
import os

import pytest

import codec_agent_plan as cap


def _plan_dict(read_paths=(), write_paths=("~/codec-projects/r02/**",)):
    return {
        "schema": 1, "agent_id": "r02", "goals": ["Collect notes"],
        "checkpoints": [{"id": "c1", "title": "Collect", "description": "...",
                         "skills_needed": ["weather"], "expected_output": "notes",
                         "step_budget": 60}],
        "permission_manifest": {"read_paths": list(read_paths), "write_paths": list(write_paths),
                                "network_domains": [], "skills": ["weather"],
                                "destructive_ops": []},
        "estimated_duration_minutes": 5, "assumptions": [],
    }


@pytest.mark.parametrize("path", [
    # (a) case-folded
    "~/.SSH/**", "~/.Codec/Skills/x.py", "/ETC/passwd", "/system/Library/x",
    # (b) a grant whose root contains a blocked folder
    "~", "~/", "~/**", "~/.codec/**", "~/.codec", "~/Library/**", "~/.config/**",
    "/", "/**", "**", "*.md", "/Users/**", "/usr/**", "/Library/**",
    "~/*/skills/**", "~/.co*/**",
    # system folders at the root are still refused
    "/var/log/**", "/private/etc/hosts", "/usr/local/etc/x",
])
def test_plan_with_blocked_or_containing_path_is_refused(path):
    with pytest.raises(ValueError, match="blocked path"):
        cap.plan_from_dict(_plan_dict([path]))
    with pytest.raises(ValueError, match="blocked path"):
        cap.plan_from_dict(_plan_dict([], [path]))


@pytest.mark.parametrize("path", [
    # (c) a folder named like a system folder, inside a project
    "~/work/var/site/**", "~/work/etc/conf/**", "~/src/private/x/**", "~/work/System/x",
    # D-16 kept, and ordinary grants
    "~/Documents/notes_ssh/**", "~/Documents/**", "~/Documents/report*.md",
    "~/codec-projects/r02/**", "/tmp/agent-work", "/usr/local/bin/x",
])
def test_plan_with_ordinary_path_is_accepted(path):
    plan = cap.plan_from_dict(_plan_dict([path]))
    assert plan.permission_manifest.read_paths == [path]


def test_relative_grant_is_read_from_the_working_folder(monkeypatch):
    monkeypatch.chdir(os.path.expanduser("~"))
    assert cap._is_path_blocklisted("./**")          # the home folder
    assert cap._is_path_blocklisted("../../etc/passwd")  # /etc, anchored
    assert not cap._is_path_blocklisted("work/var/site")
    assert not cap._is_path_blocklisted("")          # grants nothing in the runner


def test_typed_paths_follow_the_same_rule():
    reads, writes = cap.extract_user_paths(
        "Read ~/Library and ~/.SSH/config, then save into ~/work/var/site")
    assert reads == ["~/work/var/site/**"] and writes == ["~/work/var/site/**"]


def test_grant_routes_refuse_a_containing_path(tmp_path, monkeypatch):
    import codec_audit
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.agents import _grant_path_unsafe, router
    monkeypatch.setattr(cap, "_CODEC_DIR", tmp_path)
    monkeypatch.setattr(cap, "_AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(cap, "_GLOBAL_GRANTS_PATH", tmp_path / "agent_global_grants.json")
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", tmp_path / "audit.log")
    assert _grant_path_unsafe("~/.codec/**") and _grant_path_unsafe("~/.SSH/id_rsa")
    assert not _grant_path_unsafe("~/work/var/site")

    cap.save_manifest("r02", {"agent_id": "r02", "status": "blocked_on_permission", "title": "x"})
    cap.save_grants("r02", {"schema": 1, "agent_id": "r02", "approved_at": "2026-01-01",
                            "skills": ["weather"], "read_paths": [], "write_paths": [],
                            "network_domains": [], "destructive_ops": [], "auto_approved": {}})
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    r = client.post("/api/agents/r02/grant", json={"kind": "read_paths", "value": "~/Library/**"})
    assert r.status_code == 400 and cap.load_grants("r02")["read_paths"] == []
    r = client.post("/api/agent_global_grants", json={"kind": "read_paths", "value": "~/**"})
    assert r.status_code == 400 and cap.load_global_grants()["read_paths"] == []
    r = client.post("/api/agents/r02/grant", json={"kind": "read_paths", "value": "~/work/var/site"})
    assert r.status_code == 200 and cap.load_grants("r02")["read_paths"] == ["~/work/var/site"]


def test_approve_plan_refuses_a_containing_path(tmp_path, monkeypatch):
    import codec_audit
    monkeypatch.setattr(cap, "_CODEC_DIR", tmp_path)
    monkeypatch.setattr(cap, "_AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(cap, "_GLOBAL_GRANTS_PATH", tmp_path / "agent_global_grants.json")
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", tmp_path / "audit.log")
    d = tmp_path / "agents" / "r02"
    d.mkdir(parents=True)
    (d / "plan.json").write_text(json.dumps(_plan_dict(["~/**"])))
    cap.save_manifest("r02", {"agent_id": "r02", "status": "awaiting_approval", "title": "x"})
    with pytest.raises(cap.PlanValidationError, match="blocked path"):
        cap.approve_plan("r02")
    assert not (d / "grants.json").exists()
    assert cap.load_manifest("r02")["status"] == "awaiting_approval"
