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
    # Redirect codec_audit._AUDIT_LOG so post_message audit emits don't
    # leak into the production ~/.codec/audit.log (test pollution fix).
    try:
        import codec_audit
        monkeypatch.setattr(codec_audit, "_AUDIT_LOG", tmp_path / "audit.log")
    except Exception:
        pass
    # Also patch codec_agent_plan paths so _run_agent tests don't touch real ~/.codec
    try:
        import codec_agent_plan as cap
        monkeypatch.setattr(cap, "_CODEC_DIR", tmp_path)
        monkeypatch.setattr(cap, "_AGENTS_DIR", tmp_path / "agents")
        monkeypatch.setattr(cap, "_GLOBAL_GRANTS_PATH", tmp_path / "agent_global_grants.json")
    except Exception:
        pass
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


def test_run_agent_posts_started_message_on_spawn(monkeypatch, temp_codec_dir):
    """When _run_agent transitions approved → running, it posts an
    agent_update message announcing the start."""
    import codec_agent_runner as car
    import codec_agent_plan as cap
    import codec_agent_messaging as cam

    # Set up an approved agent (mirror Step 9 test fixture pattern)
    plan_dict = {
        "schema": 1, "agent_id": "test_agent", "goals": ["g"],
        "checkpoints": [{"id": "cp0", "title": "t", "description": "d",
                         "skills_needed": ["weather"], "expected_output": "o",
                         "step_budget": 5}],
        "permission_manifest": {"skills": ["weather"], "read_paths": [],
                                 "write_paths": [], "network_domains": [],
                                 "destructive_ops": []},
        "estimated_duration_minutes": 5, "assumptions": [],
    }
    plan = cap.plan_from_dict(plan_dict)
    cap.save_plan(plan)
    cap.save_grants("test_agent", {"schema": 1, "agent_id": "test_agent",
                                     "skills": ["weather"], "read_paths": [],
                                     "write_paths": [], "network_domains": [],
                                     "destructive_ops": [], "auto_approved": {},
                                     "approved_at": "x"})
    cap.save_manifest("test_agent", {"agent_id": "test_agent", "title": "x",
                                      "status": "approved",
                                      "plan_hash": cap.compute_plan_hash(plan),
                                      "created_at": "x", "updated_at": "x"})
    cap.save_state("test_agent", {"current_checkpoint": 0})

    monkeypatch.setattr(car, "_qwen_next_action", lambda *a, **k:
        car.Action(skill="", task="", kind="checkpoint_done"))

    car._run_agent("test_agent")

    # messages.jsonl should have at least started + completed messages
    msg_path = temp_codec_dir / "agents" / "test_agent" / "messages.jsonl"
    lines = msg_path.read_text().strip().splitlines()
    types = [json.loads(line)["type"] for line in lines]
    assert "agent_update" in types  # checkpoint_completed message
    assert "agent_done" in types or "agent_update" in types  # final completion


def test_run_agent_posts_blocked_message_on_permission_violation(monkeypatch, temp_codec_dir):
    """When _run_agent blocks on permission, posts agent_blocked message
    with Grant action available."""
    import codec_agent_runner as car
    import codec_agent_plan as cap

    plan_dict = {
        "schema": 1, "agent_id": "test_agent", "goals": ["g"],
        "checkpoints": [{"id": "cp0", "title": "t", "description": "d",
                         "skills_needed": ["weather"], "expected_output": "o",
                         "step_budget": 5}],
        "permission_manifest": {"skills": ["weather"], "read_paths": [],
                                 "write_paths": [], "network_domains": [],
                                 "destructive_ops": []},
        "estimated_duration_minutes": 5, "assumptions": [],
    }
    plan = cap.plan_from_dict(plan_dict)
    cap.save_plan(plan)
    cap.save_grants("test_agent", {"schema": 1, "agent_id": "test_agent",
                                     "skills": ["weather"], "read_paths": [],
                                     "write_paths": [], "network_domains": [],
                                     "destructive_ops": [], "auto_approved": {}})
    cap.save_manifest("test_agent", {"agent_id": "test_agent", "title": "x",
                                      "status": "approved",
                                      "plan_hash": cap.compute_plan_hash(plan),
                                      "created_at": "x", "updated_at": "x"})
    cap.save_state("test_agent", {"current_checkpoint": 0})

    # Try to call a skill not in grants
    monkeypatch.setattr(car, "_qwen_next_action", lambda *a, **k:
        car.Action(skill="terminal", task="ls", kind="skill_call",
                   is_destructive=False, network_call=False, touches_path=False))
    monkeypatch.setattr(car, "_run_skill", MagicMock())

    car._run_agent("test_agent")

    # Find blocked message
    msg_path = temp_codec_dir / "agents" / "test_agent" / "messages.jsonl"
    lines = msg_path.read_text().strip().splitlines()
    blocked = [json.loads(l) for l in lines if json.loads(l)["type"] == "agent_blocked"]
    assert len(blocked) >= 1
    # Has Grant action
    grant_actions = [a for a in blocked[0]["actions"] if "grant" in str(a).lower()]
    assert len(grant_actions) >= 1


def test_get_api_agents_messages_returns_jsonl(temp_codec_dir):
    """GET /api/agents/{id}/messages returns messages.jsonl as a list."""
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    import codec_agent_messaging as cam
    import codec_agent_plan as cap

    cam.post_message(agent_id="a1", type="agent_update", title="t1",
                     body="b1", actions=[], correlation_id="c1")
    cam.post_message(agent_id="a1", type="agent_update", title="t2",
                     body="b2", actions=[], correlation_id="c2")

    cap.save_manifest("a1", {"agent_id": "a1", "status": "running", "title": "x"})

    from routes.agents import router
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    r = client.get("/api/agents/a1/messages")
    assert r.status_code == 200
    body = r.json()
    assert len(body["messages"]) == 2
    assert body["messages"][0]["type"] == "agent_update"


def test_post_api_agents_messages_writes_user_reply(temp_codec_dir):
    """POST /api/agents/{id}/messages writes type=user_reply."""
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    import codec_agent_messaging as cam
    import codec_agent_plan as cap

    cap.save_manifest("a1", {"agent_id": "a1", "status": "running", "title": "x"})

    from routes.agents import router
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    r = client.post("/api/agents/a1/messages", json={"body": "please continue"})
    assert r.status_code == 200

    msg_path = temp_codec_dir / "agents" / "a1" / "messages.jsonl"
    rec = json.loads(msg_path.read_text().strip().splitlines()[-1])
    assert rec["type"] == "user_reply"
    assert rec["body"] == "please continue"


def test_post_api_agents_silence_toggles_state(temp_codec_dir):
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    import codec_agent_messaging as cam
    import codec_agent_plan as cap

    cap.save_manifest("a1", {"agent_id": "a1", "status": "running", "title": "x"})

    from routes.agents import router
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Silence
    r1 = client.post("/api/agents/a1/silence", json={"silenced": True})
    assert r1.status_code == 200
    assert cam.is_silenced("a1") is True

    # Unsilence
    r2 = client.post("/api/agents/a1/silence", json={"silenced": False})
    assert r2.status_code == 200
    assert cam.is_silenced("a1") is False


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3.5 — Multi-channel notification dispatch (5 tests)
# ─────────────────────────────────────────────────────────────────────────────

def test_pwa_only_default_no_extra_dispatch(monkeypatch, temp_codec_dir):
    """Default channels=['pwa']: no macOS/iMessage/Telegram dispatch."""
    import codec_agent_messaging as cam
    import codec_agent_plan as cap
    cap.save_manifest("a1", {"agent_id": "a1", "title": "x",
                              "notification_channels": ["pwa"]})
    dispatched = []
    monkeypatch.setattr(cam, "_dispatch_to_channel",
                        lambda ch, *a, **k: dispatched.append(ch))
    cam.post_message(agent_id="a1", type="agent_update", title="t",
                     body="b", actions=[], correlation_id="cid")
    assert dispatched == []  # pwa is skipped (handled inline)


def test_macos_channel_dispatched_when_configured(monkeypatch, temp_codec_dir):
    """When manifest includes 'macos', _dispatch_to_channel called for it."""
    import codec_agent_messaging as cam
    import codec_agent_plan as cap
    cap.save_manifest("a1", {"agent_id": "a1", "title": "x",
                              "notification_channels": ["pwa", "macos"]})
    dispatched = []
    monkeypatch.setattr(cam, "_dispatch_to_channel",
                        lambda ch, *a, **k: dispatched.append(ch))
    cam.post_message(agent_id="a1", type="agent_update", title="t",
                     body="b", actions=[], correlation_id="cid")
    assert dispatched == ["macos"]


def test_dispatch_to_channel_macos_invokes_osascript(monkeypatch, temp_codec_dir):
    """_dispatch_to_channel('macos', ...) builds an osascript command and runs it."""
    import codec_agent_messaging as cam
    captured = {"args": None}
    class FakeProc:
        returncode = 0
    def fake_run(args, **kw):
        captured["args"] = list(args)
        return FakeProc()
    import subprocess
    monkeypatch.setattr(subprocess, "run", fake_run)
    cam._dispatch_to_channel("macos", "agent_test", "Test title", "Test body", "agent_update")
    assert captured["args"] is not None
    assert captured["args"][0] == "osascript"
    assert "-e" in captured["args"]
    # AppleScript text contains the title + body somewhere
    full = " ".join(captured["args"])
    assert "Test title" in full
    assert "Test body" in full


def test_imessage_channel_skipped_when_recipient_unset(monkeypatch, temp_codec_dir):
    """Without config notifications.imessage_recipient, channel is no-op (no exception)."""
    import codec_agent_messaging as cam
    # No config.json → _channel_config returns ""
    cam._dispatch_to_channel("imessage", "agent_test", "T", "B", "agent_update")
    # Should not raise; should not call any send


def test_telegram_channel_skipped_when_unconfigured(monkeypatch, temp_codec_dir):
    """Without telegram token/chat_id, channel is no-op."""
    import codec_agent_messaging as cam
    cam._dispatch_to_channel("telegram", "agent_test", "T", "B", "agent_update")
    # Should not raise
