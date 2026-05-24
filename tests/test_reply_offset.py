"""Tests for PR-7M (Audit B / B-20) — user-reply dedup uses a monotonic consumed-offset
(count of replies already fed in), not a fragile strict-`>` millisecond timestamp compare.
Two replies in the same millisecond can no longer be dropped or double-read.

Reference: docs/PR7M-REPLY-OFFSET-DESIGN.md.
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
    monkeypatch.setattr(cap, "_CODEC_DIR", tmp_path)
    monkeypatch.setattr(cap, "_AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(cap, "_GLOBAL_GRANTS_PATH", tmp_path / "agent_global_grants.json")
    monkeypatch.setattr(cam, "_AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", tmp_path / "audit.log")
    return tmp_path


def _write_reply(agent_id, body, ts=None):
    d = cam._AGENTS_DIR / agent_id
    d.mkdir(parents=True, exist_ok=True)
    rec = {"type": "user_reply", "body": body,
           "ts": (ts or datetime.now(timezone.utc).isoformat())}
    with open(d / "messages.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def test_offset_cursor_skips_consumed_replies(temp_codec_dir):
    _write_reply("a1", "one")
    _write_reply("a1", "two")
    _write_reply("a1", "three")
    unread = cam.get_unread_user_replies("a1", since_index=2)
    assert [r["body"] for r in unread] == ["three"], \
        "since_index must skip the first N user replies, not compare timestamps (B-20)"


def test_same_millisecond_replies_each_consumed_once(temp_codec_dir):
    same = "2026-01-01T00:00:00.500000+00:00"
    _write_reply("a1", "first", ts=same)
    _write_reply("a1", "second", ts=same)
    first_batch, cursor = car._drain_user_replies("a1", 0)
    assert [e["result"].replace("[USER REPLY] ", "") for e in first_batch] == ["first", "second"]
    assert cursor == 2
    # A third reply in the SAME millisecond must still be delivered exactly once —
    # a ts-based cursor at that ms would drop it.
    _write_reply("a1", "third", ts=same)
    third_batch, cursor2 = car._drain_user_replies("a1", cursor)
    assert [e["result"].replace("[USER REPLY] ", "") for e in third_batch] == ["third"]
    assert cursor2 == 3


def test_drain_advances_offset_by_reply_count(temp_codec_dir):
    _write_reply("a1", "x")
    _write_reply("a1", "y")
    entries, cursor = car._drain_user_replies("a1", 0)
    assert len(entries) == 2
    assert cursor == 2, "cursor is a monotonic reply-count offset (B-20)"


def test_empty_body_reply_advances_cursor(temp_codec_dir):
    _write_reply("a1", "   ")  # whitespace-only → no history entry, but consumed
    entries, cursor = car._drain_user_replies("a1", 0)
    assert entries == []
    assert cursor == 1, "an empty-body reply must still advance the cursor (no infinite re-read)"


def _setup_approved(agent_id="test_agent"):
    plan = cap.plan_from_dict({
        "schema": 1, "agent_id": agent_id, "goals": ["g"],
        "checkpoints": [{"id": "cp0", "title": "c", "description": "d",
                         "skills_needed": ["weather"], "expected_output": "o", "step_budget": 5}],
        "permission_manifest": {"skills": ["weather"], "read_paths": [], "write_paths": [],
                                "network_domains": [], "destructive_ops": []},
        "estimated_duration_minutes": 10, "assumptions": [],
    })
    cap.save_plan(plan)
    cap.save_grants(agent_id, {
        "schema": 1, "agent_id": agent_id, "approved_at": "2026-01-01",
        "skills": ["weather"], "read_paths": [], "write_paths": [],
        "network_domains": [], "destructive_ops": [], "auto_approved": {},
    })
    cap.save_manifest(agent_id, {
        "agent_id": agent_id, "title": "x", "status": "approved",
        "plan_hash": cap.compute_plan_hash(plan),
        "grants_hash": cap.compute_grants_hash(agent_id),
        "created_at": "2026-01-01", "updated_at": "2026-01-01",
    })


def test_legacy_last_reply_ts_heals_forward(monkeypatch, temp_codec_dir):
    """An agent mid-run across the upgrade (state has last_reply_ts, no
    replies_consumed) must NOT re-inject a reply posted before the upgrade."""
    _setup_approved("test_agent")
    _write_reply("test_agent", "pre-upgrade reply")
    # Legacy cursor present, new cursor absent.
    cap.save_state("test_agent", {"current_checkpoint": 0, "last_reply_ts": 1.0e9})

    captured = {}

    def fake_next(plan_dict, checkpoint, history, *a, **k):
        captured["history"] = list(history)
        return car.Action(skill="", task="", kind="checkpoint_done")
    monkeypatch.setattr(car, "_qwen_next_action", fake_next)
    monkeypatch.setattr(car, "_run_skill", MagicMock(return_value="r"))
    monkeypatch.setattr(cam, "post_message", lambda **kw: None)

    car._run_agent("test_agent")

    blob = json.dumps(captured.get("history", []))
    assert "pre-upgrade reply" not in blob, \
        "a legacy last_reply_ts must heal forward — pre-upgrade replies are not re-injected (B-20)"
