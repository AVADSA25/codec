"""Tests for PR-7F (Audit B / B-6) — user replies to a running agent must be
fed into the loop (get_unread_user_replies was defined but never called).
Reference: docs/PR7F-WIRE-REPLIES-DESIGN.md.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path[:] = [p for p in sys.path if p != str(REPO)]
sys.path.insert(0, str(REPO))

import codec_agent_plan as cap  # noqa: E402
import codec_agent_messaging as cam  # noqa: E402
import codec_agent_runner as car  # noqa: E402


@pytest.fixture
def temp_codec_dir(tmp_path, monkeypatch):
    import codec_audit
    agents = tmp_path / "agents"
    monkeypatch.setattr(cap, "_CODEC_DIR", tmp_path)
    monkeypatch.setattr(cap, "_AGENTS_DIR", agents)
    monkeypatch.setattr(cap, "_GLOBAL_GRANTS_PATH", tmp_path / "agent_global_grants.json")
    monkeypatch.setattr(cam, "_AGENTS_DIR", agents)
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", tmp_path / "audit.log")
    return tmp_path


def _write_reply(agent_id, body, ts=None):
    d = cam._AGENTS_DIR / agent_id
    d.mkdir(parents=True, exist_ok=True)
    rec = {"type": "user_reply", "body": body,
           "ts": (ts or datetime.now(timezone.utc).isoformat())}
    with open(d / "messages.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def test_drain_returns_reply_and_advances_cursor(temp_codec_dir):
    _write_reply("a1", "please use the other file")
    # B-20: cursor is a monotonic consumed-offset (count), not a float ts.
    entries, cursor = car._drain_user_replies("a1", 0)
    assert len(entries) == 1
    assert "please use the other file" in entries[0]["result"]
    assert cursor == 1


def test_drain_excludes_replies_before_cursor(temp_codec_dir):
    _write_reply("a1", "old reply")
    # B-20: an offset already past the only reply yields nothing.
    entries, _ = car._drain_user_replies("a1", 1)
    assert entries == []


def _setup_approved():
    plan = cap.plan_from_dict({
        "schema": 1, "agent_id": "test_agent", "goals": ["g"],
        "checkpoints": [{"id": "cp0", "title": "c", "description": "d",
                         "skills_needed": ["weather"], "expected_output": "o", "step_budget": 5}],
        "permission_manifest": {"skills": ["weather"], "read_paths": [], "write_paths": [],
                                "network_domains": [], "destructive_ops": []},
        "estimated_duration_minutes": 10, "assumptions": [],
    })
    cap.save_plan(plan)
    cap.save_grants("test_agent", {
        "schema": 1, "agent_id": "test_agent", "approved_at": "2026-01-01",
        "skills": ["weather"], "read_paths": [], "write_paths": [],
        "network_domains": [], "destructive_ops": [], "auto_approved": {},
    })
    cap.save_manifest("test_agent", {
        "agent_id": "test_agent", "title": "x", "status": "approved",
        "plan_hash": cap.compute_plan_hash(plan),
        "grants_hash": cap.compute_grants_hash("test_agent"),
        "created_at": "2026-01-01", "updated_at": "2026-01-01",
    })
    cap.save_state("test_agent", {"current_checkpoint": 0})


def test_run_agent_feeds_user_reply_into_qwen(monkeypatch, temp_codec_dir):
    _setup_approved()
    _write_reply("test_agent", "STOP and summarize instead")

    captured = {}

    def fake_next(plan_dict, checkpoint, history, *a, **k):
        captured["history"] = list(history)
        return car.Action(skill="", task="", kind="checkpoint_done")

    monkeypatch.setattr(car, "_qwen_next_action", fake_next)
    monkeypatch.setattr(car, "_run_skill", MagicMock(return_value="r"))
    car._run_agent("test_agent")

    assert "history" in captured, "qwen was called"
    blob = json.dumps(captured["history"])
    assert "STOP and summarize instead" in blob, "the user reply must reach the model"
