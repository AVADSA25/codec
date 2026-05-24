"""Tests for PR-7H (Audit B / B-9) — approval writes status + hashes atomically
(no crash window where status=approved but a hash is missing → run-start brick),
and a pre-approval agent can be aborted (the previously-illegal recovery path).

Reference: docs/PR7H-ATOMIC-APPROVAL-DESIGN.md.
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


def test_set_status_writes_extra_fields_atomically(temp_codec_dir):
    cap.save_manifest("a1", {"agent_id": "a1", "status": "awaiting_approval", "title": "x"})
    cap.set_status("a1", "approved", extra={"plan_hash": "h123", "grants_hash": "g456"})
    m = cap.load_manifest("a1")
    assert m["status"] == "approved"
    assert m["plan_hash"] == "h123" and m["grants_hash"] == "g456", \
        "status + hashes must be written together (B-9)"


def test_awaiting_approval_can_be_aborted(temp_codec_dir):
    cap.save_manifest("a2", {"agent_id": "a2", "status": "awaiting_approval", "title": "x"})
    cap.set_status("a2", "aborted")  # previously raised InvalidStatusTransition
    assert cap.load_manifest("a2")["status"] == "aborted"


def test_draft_pending_can_be_aborted(temp_codec_dir):
    cap.save_manifest("a3", {"agent_id": "a3", "status": "draft_pending", "title": "x"})
    cap.set_status("a3", "aborted")
    assert cap.load_manifest("a3")["status"] == "aborted"


def test_approve_plan_leaves_consistent_state(monkeypatch, temp_codec_dir):
    from unittest.mock import MagicMock
    fake_reg = MagicMock()
    fake_reg.names.return_value = ["weather"]
    monkeypatch.setattr("codec_dispatch.registry", fake_reg, raising=False)
    plan = cap.plan_from_dict({
        "schema": 1, "agent_id": "a4", "goals": ["g"],
        "checkpoints": [{"id": "cp0", "title": "c", "description": "d",
                         "skills_needed": ["weather"], "expected_output": "o", "step_budget": 5}],
        "permission_manifest": {"skills": ["weather"], "read_paths": [], "write_paths": [],
                                "network_domains": [], "destructive_ops": []},
        "estimated_duration_minutes": 10, "assumptions": [],
    })
    cap.save_plan(plan)
    cap.save_manifest("a4", {"agent_id": "a4", "status": "awaiting_approval", "title": "x"})
    cap.approve_plan("a4")
    m = cap.load_manifest("a4")
    # The invariant B-9 protects: status=approved NEVER coexists with a missing hash.
    assert m["status"] == "approved"
    assert m.get("plan_hash") and m.get("grants_hash"), \
        "approved manifest must carry both hashes (single atomic write)"
