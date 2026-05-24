"""Tests for PR-7K (Audit B / B-13 + B-19) — plan_from_dict runs a schema migration
ladder before the strict version check (so a future PLAN_SCHEMA_VERSION bump doesn't
brick on-disk plans), filters unknown dataclass keys (LLM drift doesn't raise
TypeError), and raises a clean ValueError on malformed structure. Grants carry a real
version constant.

Reference: docs/PR7K-PLAN-LOADING-DESIGN.md.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

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


def _plan_dict(**over):
    d = {
        "schema": cap.PLAN_SCHEMA_VERSION, "agent_id": "a1", "goals": ["g"],
        "checkpoints": [{"id": "cp0", "title": "c", "description": "d",
                         "skills_needed": ["weather"], "expected_output": "o", "step_budget": 5}],
        "permission_manifest": {"skills": ["weather"], "read_paths": [], "write_paths": [],
                                "network_domains": [], "destructive_ops": []},
        "estimated_duration_minutes": 10, "assumptions": [],
    }
    d.update(over)
    return d


def test_v1_plan_still_loads():
    plan = cap.plan_from_dict(_plan_dict())
    assert plan.schema == cap.PLAN_SCHEMA_VERSION
    assert plan.checkpoints[0].id == "cp0"


def test_migration_ladder_upgrades_old_schema(monkeypatch):
    """A registered vN→vN+1 migration must upgrade an old-schema dict instead of the
    strict check rejecting it — the mechanism a future PLAN_SCHEMA_VERSION bump relies on."""
    cur = cap.PLAN_SCHEMA_VERSION
    old = cur - 1

    def fake_mig(d):
        d = dict(d)
        d["schema"] = cur
        d["goals"] = list(d.get("goals", [])) + ["migrated"]
        return d
    monkeypatch.setitem(cap._PLAN_MIGRATIONS, old, fake_mig)

    plan = cap.plan_from_dict(_plan_dict(schema=old))
    assert plan.schema == cur
    assert "migrated" in plan.goals, "migration ladder must run before the strict check (B-13)"


def test_future_schema_rejected():
    """A schema newer than we understand (no migration) is rejected cleanly, never
    silently loaded."""
    with pytest.raises(ValueError):
        cap.plan_from_dict(_plan_dict(schema=cap.PLAN_SCHEMA_VERSION + 99))


def test_unknown_checkpoint_key_tolerated():
    """An extra checkpoint key (LLM emits e.g. 'priority') must NOT raise TypeError (B-19)."""
    d = _plan_dict()
    d["checkpoints"][0]["priority"] = "high"  # not a Checkpoint field
    plan = cap.plan_from_dict(d)
    assert plan.checkpoints[0].id == "cp0"


def test_unknown_manifest_key_tolerated():
    """An extra permission_manifest key must NOT raise TypeError (B-19)."""
    d = _plan_dict()
    d["permission_manifest"]["max_spend_usd"] = 50  # not a PermissionManifest field
    plan = cap.plan_from_dict(d)
    assert plan.permission_manifest.skills == ["weather"]


def test_malformed_plan_raises_valueerror():
    """A checkpoint missing a required field raises a clean ValueError, not a raw
    TypeError that callers may not catch (B-19)."""
    d = _plan_dict()
    del d["checkpoints"][0]["id"]  # required field
    with pytest.raises(ValueError):
        cap.plan_from_dict(d)


def test_grants_carry_version_constant(monkeypatch, temp_codec_dir):
    """approve_plan writes grants.json with a real GRANTS_SCHEMA_VERSION (B-13)."""
    assert hasattr(cap, "GRANTS_SCHEMA_VERSION")
    fake_reg = MagicMock()
    fake_reg.names.return_value = ["weather"]
    monkeypatch.setattr("codec_dispatch.registry", fake_reg, raising=False)
    plan = cap.plan_from_dict(_plan_dict(agent_id="a7"))
    cap.save_plan(plan)
    cap.save_manifest("a7", {"agent_id": "a7", "status": "awaiting_approval", "title": "x"})
    cap.approve_plan("a7")
    grants = cap.load_grants("a7")
    assert grants.get("schema") == cap.GRANTS_SCHEMA_VERSION
