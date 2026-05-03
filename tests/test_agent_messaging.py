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
