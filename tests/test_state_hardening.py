"""Tests for PR-7J (Audit B / B-10 + B-11) — agent state files are written 0600
(not world-readable), and notifications.json's read-modify-write honours the
cross-process flock contract every other writer already uses.

Reference: docs/PR7J-STATE-HARDENING-DESIGN.md.
"""
from __future__ import annotations

import contextlib
import os
import stat
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path[:] = [p for p in sys.path if p != str(REPO)]
sys.path.insert(0, str(REPO))

import codec_agent_plan as cap  # noqa: E402
import codec_agent_messaging as cam  # noqa: E402


@pytest.fixture
def temp_codec_dir(tmp_path, monkeypatch):
    import codec_audit
    monkeypatch.setattr(cap, "_CODEC_DIR", tmp_path)
    monkeypatch.setattr(cap, "_AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(cap, "_GLOBAL_GRANTS_PATH", tmp_path / "agent_global_grants.json")
    monkeypatch.setattr(cam, "_CODEC_DIR", tmp_path)
    monkeypatch.setattr(cam, "_AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(cam, "_NOTIFICATIONS_PATH", tmp_path / "notifications.json")
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", tmp_path / "audit.log")
    return tmp_path


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_plan_state_files_are_0600(temp_codec_dir):
    plan = cap.plan_from_dict({
        "schema": 1, "agent_id": "a1", "goals": ["g"],
        "checkpoints": [{"id": "cp0", "title": "c", "description": "d",
                         "skills_needed": ["weather"], "expected_output": "o", "step_budget": 5}],
        "permission_manifest": {"skills": ["weather"], "read_paths": [], "write_paths": [],
                                "network_domains": [], "destructive_ops": []},
        "estimated_duration_minutes": 10, "assumptions": [],
    })
    cap.save_plan(plan)
    cap.save_manifest("a1", {"agent_id": "a1", "status": "draft_pending", "title": "x"})
    cap.save_state("a1", {"current_checkpoint": 0})
    cap.save_grants("a1", {"schema": 1, "agent_id": "a1", "skills": [], "read_paths": [],
                           "write_paths": [], "network_domains": [], "destructive_ops": [],
                           "auto_approved": {}})
    d = temp_codec_dir / "agents" / "a1"
    for fname in ("plan.json", "manifest.json", "state.json", "grants.json"):
        assert _mode(d / fname) == 0o600, f"{fname} must be 0600 (B-10), got {oct(_mode(d / fname))}"


def test_messages_jsonl_is_0600(temp_codec_dir):
    cam.post_message(agent_id="a2", type="agent_update", title="t", body="b")
    p = temp_codec_dir / "agents" / "a2" / "messages.jsonl"
    assert _mode(p) == 0o600, f"messages.jsonl must be 0600 (B-10), got {oct(_mode(p))}"


def test_notifications_json_is_0600(temp_codec_dir):
    cam.post_message(agent_id="a3", type="agent_update", title="t", body="b")
    p = temp_codec_dir / "notifications.json"
    assert _mode(p) == 0o600, f"notifications.json must be 0600 (B-10), got {oct(_mode(p))}"


def test_agent_silence_json_is_0600(temp_codec_dir):
    cam.set_silenced("a4", True)
    p = temp_codec_dir / "agent_silence.json"
    assert _mode(p) == 0o600, f"agent_silence.json must be 0600 (B-10), got {oct(_mode(p))}"


def test_notifications_write_uses_cross_process_flock(monkeypatch, temp_codec_dir):
    """post_message must acquire codec_jsonstore.file_lock for the notifications.json
    read-modify-write — same contract every other notifications writer uses (B-11)."""
    import codec_jsonstore
    calls = []
    real = codec_jsonstore.file_lock

    @contextlib.contextmanager
    def spy(path):
        calls.append(str(path))
        with real(path):
            yield
    monkeypatch.setattr(codec_jsonstore, "file_lock", spy)

    cam.post_message(agent_id="a5", type="agent_update", title="t", body="b")

    assert any("notifications.json" in c for c in calls), \
        "post_message must hold the cross-process flock for the notifications.json RMW (B-11)"
