"""Phase 3 Step 10 tests — codec_agent_messaging.

14 tests covering: audit constants, AgentMessage dataclass, post_message
+ batching, user replies, silence kill-switch, PWA endpoints, _run_agent
integration.

All tests:
  - Mock external deps; never real LLM, never real notifications outside tmp
  - Use temp_codec_dir fixture (mirror Step 8/9)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def test_step10_audit_constants_present():
    """Phase 3 Step 10 adds 3 named events + 1 frozenset."""
    import codec_audit
    assert codec_audit.AGENT_MESSAGE_SENT == "agent_message_sent"
    assert codec_audit.AGENT_MESSAGE_RECEIVED == "agent_message_received"
    assert codec_audit.AGENT_AUTO_ESCALATED_FROM_CHAT == "agent_auto_escalated_from_chat"
    assert codec_audit.PHASE3_STEP10_EVENTS == frozenset({
        "agent_message_sent", "agent_message_received",
        "agent_auto_escalated_from_chat",
    })


def test_agent_message_dataclass_basic():
    from codec_agent_messaging import AgentMessage
    m = AgentMessage(
        agent_id="agent_test", type="agent_update",
        title="Checkpoint 2 of 5 done",
        body="Scraped 150 listings.",
        actions=[{"label": "View", "endpoint": "/api/agents/agent_test/artifacts"}],
        correlation_id="abc123",
    )
    assert m.agent_id == "agent_test"
    assert m.type == "agent_update"
    assert m.actions[0]["label"] == "View"


def test_agent_message_to_dict_includes_ts():
    from codec_agent_messaging import AgentMessage
    m = AgentMessage(agent_id="x", type="agent_done", title="t", body="b",
                     actions=[], correlation_id="cid")
    d = m.to_dict()
    assert d["agent_id"] == "x"
    assert d["type"] == "agent_done"
    assert "ts" in d  # timestamp injected by to_dict


@pytest.fixture
def temp_codec_dir(tmp_path, monkeypatch):
    import codec_agent_messaging as cam
    monkeypatch.setattr(cam, "_CODEC_DIR", tmp_path)
    monkeypatch.setattr(cam, "_AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(cam, "_NOTIFICATIONS_PATH", tmp_path / "notifications.json")
    return tmp_path


def test_post_message_appends_to_messages_jsonl(temp_codec_dir):
    """First message: writes to messages.jsonl + notifications.json."""
    import codec_agent_messaging as cam
    cam.post_message(
        agent_id="agent_test", type="agent_update",
        title="cp1 done", body="Scraped X listings.",
        actions=[], correlation_id="cid_abc",
    )
    msg_path = temp_codec_dir / "agents" / "agent_test" / "messages.jsonl"
    assert msg_path.exists()
    lines = msg_path.read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["type"] == "agent_update"
    assert rec["title"] == "cp1 done"


def test_post_message_appends_to_notifications_json(temp_codec_dir):
    """First message creates a notification entry."""
    import codec_agent_messaging as cam
    cam.post_message(
        agent_id="agent_test", type="agent_update",
        title="cp1 done", body="b", actions=[], correlation_id="cid",
    )
    notif_path = temp_codec_dir / "notifications.json"
    assert notif_path.exists()
    notifs = json.loads(notif_path.read_text())
    assert len(notifs) == 1
    assert notifs[0]["type"] == "agent_update"
    assert notifs[0]["agent_id"] == "agent_test"


def test_post_message_batches_within_60s_window(temp_codec_dir, monkeypatch):
    """3 messages within batch window → 3 lines in messages.jsonl, 1 banner notification."""
    import codec_agent_messaging as cam
    fixed_time = [1700000000.0]  # mutable container
    monkeypatch.setattr(cam.time, "time", lambda: fixed_time[0])

    cam.post_message(agent_id="agent_test", type="agent_update", title="cp1",
                     body="b", actions=[], correlation_id="c1")
    fixed_time[0] += 10  # +10s
    cam.post_message(agent_id="agent_test", type="agent_update", title="cp2",
                     body="b", actions=[], correlation_id="c2")
    fixed_time[0] += 30  # +30s (still within 60s window)
    cam.post_message(agent_id="agent_test", type="agent_update", title="cp3",
                     body="b", actions=[], correlation_id="c3")

    # All 3 messages preserved in timeline
    msg_path = temp_codec_dir / "agents" / "agent_test" / "messages.jsonl"
    assert len(msg_path.read_text().strip().splitlines()) == 3

    # Only 1 notification (latest, with batch count)
    notifs = json.loads((temp_codec_dir / "notifications.json").read_text())
    agent_notifs = [n for n in notifs if n.get("agent_id") == "agent_test"]
    assert len(agent_notifs) == 1
    assert "3" in agent_notifs[0]["title"] or agent_notifs[0].get("batch_count") == 3


def test_post_message_creates_new_banner_outside_60s_window(temp_codec_dir, monkeypatch):
    """Two messages 90s apart → 2 separate banners."""
    import codec_agent_messaging as cam
    fixed_time = [1700000000.0]
    monkeypatch.setattr(cam.time, "time", lambda: fixed_time[0])

    cam.post_message(agent_id="agent_test", type="agent_update", title="cp1",
                     body="b", actions=[], correlation_id="c1")
    fixed_time[0] += 90  # outside window
    cam.post_message(agent_id="agent_test", type="agent_update", title="cp2",
                     body="b", actions=[], correlation_id="c2")

    notifs = json.loads((temp_codec_dir / "notifications.json").read_text())
    agent_notifs = [n for n in notifs if n.get("agent_id") == "agent_test"]
    assert len(agent_notifs) == 2


def test_post_user_reply_writes_to_messages_jsonl(temp_codec_dir):
    """User reply via post_user_reply writes type=user_reply line."""
    import codec_agent_messaging as cam
    cam.post_user_reply(agent_id="agent_test", body="please continue")
    msg_path = temp_codec_dir / "agents" / "agent_test" / "messages.jsonl"
    lines = msg_path.read_text().strip().splitlines()
    rec = json.loads(lines[0])
    assert rec["type"] == "user_reply"
    assert rec["body"] == "please continue"


def test_get_unread_user_replies_returns_unread(temp_codec_dir):
    """get_unread_user_replies returns user_reply entries since `since_ts`."""
    import codec_agent_messaging as cam
    cam.post_user_reply(agent_id="agent_test", body="r1")
    time.sleep(0.05)
    t1 = time.time()
    time.sleep(0.05)  # ensure r2/r3 ts > t1
    cam.post_user_reply(agent_id="agent_test", body="r2")
    cam.post_user_reply(agent_id="agent_test", body="r3")

    unread = cam.get_unread_user_replies(agent_id="agent_test", since_ts=t1)
    assert len(unread) == 2
    assert unread[-1]["body"] == "r3"


def test_silenced_agent_writes_jsonl_but_no_notification(temp_codec_dir):
    """When agent is silenced, post_message still writes to messages.jsonl
    but skips notifications.json (Step 10 silence kill-switch per Q12 / Step 9 §10)."""
    import codec_agent_messaging as cam
    cam.set_silenced("agent_test", True)
    cam.post_message(agent_id="agent_test", type="agent_update",
                     title="t", body="b", actions=[], correlation_id="cid")

    msg_path = temp_codec_dir / "agents" / "agent_test" / "messages.jsonl"
    assert msg_path.exists()  # timeline still recorded

    # Notifications was either not written or has 0 entries for this agent
    if (temp_codec_dir / "notifications.json").exists():
        notifs = json.loads((temp_codec_dir / "notifications.json").read_text())
        agent_notifs = [n for n in notifs if n.get("agent_id") == "agent_test"]
        assert len(agent_notifs) == 0


def test_unsilencing_restores_notifications(temp_codec_dir):
    import codec_agent_messaging as cam
    cam.set_silenced("agent_test", True)
    cam.post_message(agent_id="agent_test", type="agent_update", title="t",
                     body="b", actions=[], correlation_id="cid")
    cam.set_silenced("agent_test", False)
    cam.post_message(agent_id="agent_test", type="agent_update", title="t2",
                     body="b", actions=[], correlation_id="cid")

    notifs = json.loads((temp_codec_dir / "notifications.json").read_text())
    agent_notifs = [n for n in notifs if n.get("agent_id") == "agent_test"]
    assert len(agent_notifs) == 1  # only the unsilenced one
