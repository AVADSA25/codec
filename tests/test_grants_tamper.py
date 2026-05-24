"""Tests for PR-7E (Audit B / B-4) — grants.json (the actual enforcement input)
must be covered by the tamper hash: stored at approval, re-synced on every legit
/grant, verified at run start. Reference: docs/PR7E-GRANTS-TAMPER-DESIGN.md.
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


def _grants(agent_id="g1"):
    return {
        "schema": 1, "agent_id": agent_id, "approved_at": "2026-01-01",
        "skills": ["weather"], "read_paths": [], "write_paths": [],
        "network_domains": [], "destructive_ops": [], "auto_approved": {},
    }


def test_compute_grants_hash_deterministic_and_sensitive(temp_codec_dir):
    cap.save_grants("g1", _grants())
    h1 = cap.compute_grants_hash("g1")
    assert len(h1) == 64
    assert cap.compute_grants_hash("g1") == h1, "deterministic"
    g = _grants()
    g["write_paths"] = ["/"]
    cap.save_grants("g1", g)
    assert cap.compute_grants_hash("g1") != h1, "must change when grants change"


def test_set_grants_hash_resyncs(temp_codec_dir):
    cap.save_grants("g1", _grants())
    cap.save_manifest("g1", {"agent_id": "g1", "status": "running", "title": "x"})
    cap.set_grants_hash("g1")
    assert cap.load_manifest("g1")["grants_hash"] == cap.compute_grants_hash("g1")


def _setup_approved(num_checkpoints=1):
    cps = [{"id": f"cp{i}", "title": f"c{i}", "description": "d",
            "skills_needed": ["weather"], "expected_output": "o", "step_budget": 5}
           for i in range(num_checkpoints)]
    plan = cap.plan_from_dict({
        "schema": 1, "agent_id": "test_agent", "goals": ["g"], "checkpoints": cps,
        "permission_manifest": {"skills": ["weather"], "read_paths": [], "write_paths": [],
                                "network_domains": [], "destructive_ops": []},
        "estimated_duration_minutes": 10, "assumptions": [],
    })
    cap.save_plan(plan)
    cap.save_grants("test_agent", _grants("test_agent"))
    cap.save_manifest("test_agent", {
        "agent_id": "test_agent", "title": "x", "status": "approved",
        "plan_hash": cap.compute_plan_hash(plan),
        "grants_hash": cap.compute_grants_hash("test_agent"),
        "created_at": "2026-01-01", "updated_at": "2026-01-01",
    })
    cap.save_state("test_agent", {"current_checkpoint": 0})


def test_run_agent_grants_tamper_aborts(monkeypatch, temp_codec_dir):
    import codec_agent_runner as car
    _setup_approved()
    # Tamper: stored grants_hash no longer matches current grants.json.
    m = cap.load_manifest("test_agent")
    m["grants_hash"] = "0" * 64
    cap.save_manifest("test_agent", m)

    car._run_agent("test_agent")
    out = cap.load_manifest("test_agent")
    assert out["status"] == "aborted"
    assert "grants" in (out.get("status_reason", "") or "").lower()


def test_run_agent_absent_grants_hash_heals_not_aborts(monkeypatch, temp_codec_dir):
    import codec_agent_runner as car
    _setup_approved()
    # Legacy agent: no grants_hash in manifest.
    m = cap.load_manifest("test_agent")
    m.pop("grants_hash", None)
    cap.save_manifest("test_agent", m)

    actions = [car.Action(skill="", task="", kind="checkpoint_done")]
    monkeypatch.setattr(car, "_qwen_next_action", lambda *a, **k: actions[0])
    monkeypatch.setattr(car, "_run_skill", MagicMock(return_value="r"))
    car._run_agent("test_agent")

    out = cap.load_manifest("test_agent")
    assert out["status"] != "aborted", "absent grants_hash must heal, not abort (legacy compat)"
    assert out.get("grants_hash"), "absence should heal-forward by storing the hash"
