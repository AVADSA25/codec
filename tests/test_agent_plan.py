"""Phase 3 Step 8 tests — codec_agent_plan + routes/agents.py.

25 tests covering: audit constants, dataclasses, atomic R/W, validation,
plan-hash, LLM drafter, clarifying loop, global allowlist, state machine,
PWA endpoints, and end-to-end integration.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def test_audit_constants_present():
    """Phase 3 Step 8 adds 6 named events + 1 frozenset."""
    import codec_audit
    assert codec_audit.AGENT_PLAN_DRAFTED == "agent_plan_drafted"
    assert codec_audit.AGENT_PLAN_APPROVED == "agent_plan_approved"
    assert codec_audit.AGENT_PLAN_REJECTED == "agent_plan_rejected"
    assert codec_audit.AGENT_PLAN_REVISED == "agent_plan_revised"
    assert codec_audit.AGENT_GLOBAL_GRANT_ADDED == "agent_global_grant_added"
    assert codec_audit.AGENT_GLOBAL_GRANT_REMOVED == "agent_global_grant_removed"
    assert codec_audit.PHASE3_STEP8_EVENTS == frozenset({
        "agent_plan_drafted", "agent_plan_approved", "agent_plan_rejected",
        "agent_plan_revised", "agent_global_grant_added", "agent_global_grant_removed",
    })


# ─────────────────────────────────────────────────────────────────────────────
# Task 2 — Plan/Checkpoint/PermissionManifest dataclasses (3 tests)
# ─────────────────────────────────────────────────────────────────────────────

def test_plan_dataclass_basic():
    from codec_agent_plan import Plan, Checkpoint, PermissionManifest
    cp = Checkpoint(
        id="abc123ab", title="Scrape listings", description="...",
        skills_needed=["chrome_open"], expected_output="JSON of listings",
        step_budget=30,
    )
    pm = PermissionManifest(
        read_paths=["~/Documents/**"], write_paths=["~/.codec/agents/test/artifacts/**"],
        network_domains=["example.com"], skills=["chrome_open"], destructive_ops=[],
    )
    plan = Plan(
        schema=1, agent_id="test_agent",
        goals=["Scrape data"], checkpoints=[cp], permission_manifest=pm,
        estimated_duration_minutes=15, assumptions=[],
    )
    assert plan.schema == 1
    assert plan.checkpoints[0].title == "Scrape listings"
    assert plan.permission_manifest.skills == ["chrome_open"]


def test_plan_dataclass_to_dict_roundtrip():
    from codec_agent_plan import Plan, Checkpoint, PermissionManifest, plan_from_dict
    cp = Checkpoint(id="x", title="t", description="d",
                    skills_needed=["s"], expected_output="o", step_budget=10)
    pm = PermissionManifest(read_paths=[], write_paths=[], network_domains=[],
                            skills=["s"], destructive_ops=[])
    plan = Plan(schema=1, agent_id="a1", goals=["g"], checkpoints=[cp],
                permission_manifest=pm, estimated_duration_minutes=5, assumptions=[])
    d = plan.to_dict()
    plan2 = plan_from_dict(d)
    assert plan2.agent_id == plan.agent_id
    assert plan2.checkpoints[0].id == plan.checkpoints[0].id
    assert plan2.permission_manifest.skills == plan.permission_manifest.skills


def test_plan_from_dict_rejects_bad_schema():
    from codec_agent_plan import plan_from_dict
    with pytest.raises(ValueError, match="unsupported plan schema"):
        plan_from_dict({"schema": 99, "agent_id": "x"})


# ─────────────────────────────────────────────────────────────────────────────
# Task 3 — Plan-hash for tamper detection (Q13) (2 tests)
# ─────────────────────────────────────────────────────────────────────────────

def test_plan_hash_stable_for_identical_content():
    from codec_agent_plan import Plan, Checkpoint, PermissionManifest, compute_plan_hash
    cp = Checkpoint(id="x", title="t", description="d", skills_needed=["s"],
                    expected_output="o", step_budget=10)
    pm = PermissionManifest(read_paths=[], write_paths=[], network_domains=[],
                            skills=["s"], destructive_ops=[])
    plan_a = Plan(schema=1, agent_id="a1", goals=["g"], checkpoints=[cp],
                  permission_manifest=pm, estimated_duration_minutes=5, assumptions=[])
    plan_b = Plan(schema=1, agent_id="a1", goals=["g"], checkpoints=[cp],
                  permission_manifest=pm, estimated_duration_minutes=5, assumptions=[])
    assert compute_plan_hash(plan_a) == compute_plan_hash(plan_b)


def test_plan_hash_changes_when_content_changes():
    from codec_agent_plan import Plan, Checkpoint, PermissionManifest, compute_plan_hash
    cp = Checkpoint(id="x", title="t", description="d", skills_needed=["s"],
                    expected_output="o", step_budget=10)
    pm = PermissionManifest(read_paths=[], write_paths=[], network_domains=[],
                            skills=["s"], destructive_ops=[])
    plan_a = Plan(schema=1, agent_id="a1", goals=["g"], checkpoints=[cp],
                  permission_manifest=pm, estimated_duration_minutes=5, assumptions=[])
    plan_b = Plan(schema=1, agent_id="a1", goals=["g_modified"], checkpoints=[cp],
                  permission_manifest=pm, estimated_duration_minutes=5, assumptions=[])
    assert compute_plan_hash(plan_a) != compute_plan_hash(plan_b)


# ─────────────────────────────────────────────────────────────────────────────
# Task 4 — Atomic R/W (2 tests)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_codec_dir(tmp_path, monkeypatch):
    import codec_agent_plan as cap
    monkeypatch.setattr(cap, "_CODEC_DIR", tmp_path)
    monkeypatch.setattr(cap, "_AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(cap, "_GLOBAL_GRANTS_PATH", tmp_path / "agent_global_grants.json")
    return tmp_path


def test_save_and_load_plan_roundtrip(temp_codec_dir):
    from codec_agent_plan import (
        Plan, Checkpoint, PermissionManifest, save_plan, load_plan,
    )
    cp = Checkpoint(id="x", title="t", description="d", skills_needed=["s"],
                    expected_output="o", step_budget=10)
    pm = PermissionManifest(read_paths=[], write_paths=[], network_domains=[],
                            skills=["s"], destructive_ops=[])
    plan = Plan(schema=1, agent_id="agent_test", goals=["g"], checkpoints=[cp],
                permission_manifest=pm, estimated_duration_minutes=5, assumptions=[])
    save_plan(plan)
    loaded = load_plan("agent_test")
    assert loaded.agent_id == "agent_test"
    assert loaded.checkpoints[0].title == "t"


def test_save_state_atomic(temp_codec_dir):
    from codec_agent_plan import save_state, load_state
    save_state("agent_x", {"current_checkpoint": 0, "status": "draft_pending"})
    state = load_state("agent_x")
    assert state["current_checkpoint"] == 0
    assert state["status"] == "draft_pending"
    # Verify atomic: tmp file is gone after save
    agent_dir = temp_codec_dir / "agents" / "agent_x"
    assert not (agent_dir / "state.json.tmp").exists()
    assert (agent_dir / "state.json").exists()


# ─────────────────────────────────────────────────────────────────────────────
# Task 5 — Skill-registry validation (2 tests)
# ─────────────────────────────────────────────────────────────────────────────

def test_validate_plan_against_registry_ok():
    from codec_agent_plan import (
        Plan, Checkpoint, PermissionManifest, validate_plan_skills,
    )
    cp = Checkpoint(id="x", title="t", description="d",
                    skills_needed=["weather"], expected_output="o", step_budget=10)
    pm = PermissionManifest(read_paths=[], write_paths=[], network_domains=[],
                            skills=["weather"], destructive_ops=[])
    plan = Plan(schema=1, agent_id="a", goals=["g"], checkpoints=[cp],
                permission_manifest=pm, estimated_duration_minutes=5, assumptions=[])

    fake_registry = MagicMock()
    fake_registry.names.return_value = ["weather", "calculator"]
    ok, missing = validate_plan_skills(plan, registry=fake_registry)
    assert ok is True
    assert missing == []


def test_validate_plan_against_registry_rejects_unknown_skill():
    from codec_agent_plan import (
        Plan, Checkpoint, PermissionManifest, validate_plan_skills,
    )
    cp = Checkpoint(id="x", title="t", description="d",
                    skills_needed=["nonexistent_skill"], expected_output="o", step_budget=10)
    pm = PermissionManifest(read_paths=[], write_paths=[], network_domains=[],
                            skills=["nonexistent_skill"], destructive_ops=[])
    plan = Plan(schema=1, agent_id="a", goals=["g"], checkpoints=[cp],
                permission_manifest=pm, estimated_duration_minutes=5, assumptions=[])

    fake_registry = MagicMock()
    fake_registry.names.return_value = ["weather", "calculator"]
    ok, missing = validate_plan_skills(plan, registry=fake_registry)
    assert ok is False
    assert "nonexistent_skill" in missing
