"""Tests for PR-7C (Audit B / B-3) — the /api/agents/{id}/grant endpoint must
refuse blocklisted / over-broad path grants (it previously appended any value,
so a caller could grant write_paths=/ and turn an agent into an arbitrary-write
primitive).

Reference: docs/PR7C-GRANT-BLOCKLIST-DESIGN.md, docs/audits/PHASE-1-PROJECTS-PILOT.md (B-3).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path[:] = [p for p in sys.path if p != str(REPO)]
sys.path.insert(0, str(REPO))

from routes.agents import _grant_path_unsafe  # noqa: E402


# ---- the path-safety predicate -------------------------------------------

@pytest.mark.parametrize("bad", [
    "/", "~", "", "   ",
    "~/.ssh/id_rsa", "~/.aws/credentials", "~/.codec/oauth_state.json",
    "/etc/passwd", "/usr/lib/x", "/System/foo", "/Library/Keychains",
    "../etc/passwd", "~/Documents/../../etc",
])
def test_unsafe_grants_rejected(bad):
    assert _grant_path_unsafe(bad) is True, f"{bad!r} must be rejected"


@pytest.mark.parametrize("ok", [
    "~/Documents/report.md", "~/Projects/myapp", "~/Projects/myapp/*.py",
    "/tmp/agent-work", "~/Desktop/out.txt",
])
def test_safe_grants_allowed(ok):
    assert _grant_path_unsafe(ok) is False, f"{ok!r} must be allowed"


# ---- endpoint enforcement -------------------------------------------------

@pytest.fixture
def temp_codec_dir(tmp_path, monkeypatch):
    import codec_agent_plan as cap
    import codec_audit
    monkeypatch.setattr(cap, "_CODEC_DIR", tmp_path)
    monkeypatch.setattr(cap, "_AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(cap, "_GLOBAL_GRANTS_PATH", tmp_path / "agent_global_grants.json")
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", tmp_path / "audit.log")
    return tmp_path


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.agents import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _approved_agent():
    import codec_agent_plan as cap
    cap.save_manifest("a1", {"agent_id": "a1", "status": "blocked_on_permission", "title": "x"})
    cap.save_grants("a1", {
        "schema": 1, "agent_id": "a1", "approved_at": "2026-01-01",
        "skills": ["weather"], "read_paths": [], "write_paths": [],
        "network_domains": [], "destructive_ops": [], "auto_approved": {},
    })


def test_grant_endpoint_refuses_root_write_path(temp_codec_dir):
    import codec_agent_plan as cap
    _approved_agent()
    r = _client().post("/api/agents/a1/grant", json={"kind": "write_paths", "value": "/"})
    assert r.status_code == 400, "granting write_paths=/ must be refused"
    assert "/" not in cap.load_grants("a1")["write_paths"], "the dangerous grant must NOT be saved"


def test_grant_endpoint_refuses_blocklisted_path(temp_codec_dir):
    import codec_agent_plan as cap
    _approved_agent()
    r = _client().post("/api/agents/a1/grant", json={"kind": "read_paths", "value": "~/.ssh/id_rsa"})
    assert r.status_code == 400
    assert cap.load_grants("a1")["read_paths"] == []


def test_grant_endpoint_allows_safe_path(temp_codec_dir):
    import codec_agent_plan as cap
    _approved_agent()
    r = _client().post("/api/agents/a1/grant", json={"kind": "write_paths", "value": "~/Documents/out.md"})
    assert r.status_code == 200
    assert "~/Documents/out.md" in cap.load_grants("a1")["write_paths"]


def test_grant_endpoint_still_allows_skill_grant(temp_codec_dir):
    import codec_agent_plan as cap
    _approved_agent()
    r = _client().post("/api/agents/a1/grant", json={"kind": "skills", "value": "calculator"})
    assert r.status_code == 200
    assert "calculator" in cap.load_grants("a1")["skills"]
