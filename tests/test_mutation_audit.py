"""Tests for PR-7P (Audit B / B-3 remainder) — per-agent ownership authz stays deferred
(single-user threat model), but state-changing /api/agents/* mutations now emit a forensic
`agent_mutation` audit event with the caller IP so a localhost-foothold abuse is detectable.

Reference: docs/PR7P-CAPABILITY-AUTHZ-DESIGN.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path[:] = [p for p in sys.path if p != str(REPO)]
sys.path.insert(0, str(REPO))

import codec_agent_plan as cap  # noqa: E402


@pytest.fixture
def temp_codec_dir(tmp_path, monkeypatch):
    import codec_audit
    monkeypatch.setattr(cap, "_CODEC_DIR", tmp_path)
    monkeypatch.setattr(cap, "_AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(cap, "_GLOBAL_GRANTS_PATH", tmp_path / "agent_global_grants.json")
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", tmp_path / "audit.log")
    return tmp_path


class _FakeClient:
    host = "10.1.2.3"


class _FakeReq:
    client = _FakeClient()


def test_grant_emits_mutation_audit_with_ip(monkeypatch, temp_codec_dir):
    import routes.agents as ra
    import codec_audit

    cap.save_manifest("a1", {"agent_id": "a1", "title": "x", "status": "blocked_on_permission"})
    cap.save_grants("a1", {"schema": 1, "agent_id": "a1", "skills": [], "read_paths": [],
                           "write_paths": [], "network_domains": [], "auto_approved": {}})

    events = []
    monkeypatch.setattr(codec_audit, "audit",
                        lambda **kw: events.append(kw))

    ra.grant_permission("a1", ra.GrantBody(kind="skills", value="weather"), _FakeReq())

    mutation = [e for e in events if e.get("event") == "agent_mutation"]
    assert mutation, "a /grant mutation must emit an agent_mutation forensic audit event (B-3)"
    extra = mutation[0].get("extra", {})
    assert extra.get("client_ip") == "10.1.2.3", "the caller IP must be recorded"
    assert extra.get("mutation") == "grant" and extra.get("agent_id") == "a1"
