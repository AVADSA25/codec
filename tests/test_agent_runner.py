"""Phase 3 Step 9 tests — codec_agent_runner.

31 tests covering: audit constants, state machine, permission gate,
Action dataclass, qwen next-action driver, strict-consent integration,
checkpoint executor, run_agent paths, daemon outer loop, multi-agent
concurrency, resume-after-restart, plan-hash tamper, PWA endpoints.

All tests:
  - Mock Qwen-3.6 via monkeypatch._qwen_next_action / _qwen_chat
  - Mock codec_dispatch.run_skill (never fire real skills)
  - Mock codec_ask_user.ask + strict_consent_gate
  - Use tmp_path + temp_codec_dir fixture (mirror Step 8)
  - No real notifications, no real audit emits to live log
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


# ─────────────────────────────────────────────────────────────────────────────
# Task 1 — Audit event constants (1 test)
# ─────────────────────────────────────────────────────────────────────────────

def test_step9_audit_constants_present():
    """Phase 3 Step 9 adds 8 named events + 1 frozenset."""
    import codec_audit
    assert codec_audit.AGENT_STARTED == "agent_started"
    assert codec_audit.AGENT_CHECKPOINT_STARTED == "agent_checkpoint_started"
    assert codec_audit.AGENT_CHECKPOINT_COMPLETED == "agent_checkpoint_completed"
    assert codec_audit.AGENT_PAUSED == "agent_paused"
    assert codec_audit.AGENT_RESUMED == "agent_resumed"
    assert codec_audit.AGENT_BLOCKED_ON_PERMISSION == "agent_blocked_on_permission"
    assert codec_audit.AGENT_COMPLETED == "agent_completed"
    assert codec_audit.AGENT_ABORTED == "agent_aborted"
    assert codec_audit.PHASE3_STEP9_EVENTS == frozenset({
        "agent_started", "agent_checkpoint_started", "agent_checkpoint_completed",
        "agent_paused", "agent_resumed", "agent_blocked_on_permission",
        "agent_completed", "agent_aborted",
    })


# ─────────────────────────────────────────────────────────────────────────────
# Task 2 — Extend state machine (1 test)
# ─────────────────────────────────────────────────────────────────────────────

def test_step9_state_transitions_extend_valid_map():
    from codec_agent_plan import _VALID_TRANSITIONS
    # approved can now transition to running (Step 9 NEW)
    assert "running" in _VALID_TRANSITIONS["approved"]
    # running can transition to: completed, aborted, paused, blocked_on_permission, blocked_on_destructive, crashed_resumed
    assert {"completed", "aborted", "paused",
            "blocked_on_permission", "blocked_on_destructive",
            "crashed_resumed"} <= _VALID_TRANSITIONS["running"]
    # paused can resume → running
    assert "running" in _VALID_TRANSITIONS["paused"]
    # blocked_on_permission can resume (after grant) → running, OR be aborted
    assert {"running", "aborted"} <= _VALID_TRANSITIONS["blocked_on_permission"]
    # blocked_on_destructive can resume (next morning consent) → running, OR be aborted
    assert {"running", "aborted"} <= _VALID_TRANSITIONS["blocked_on_destructive"]
    # crashed_resumed can re-enter running, or be aborted
    assert {"running", "aborted"} <= _VALID_TRANSITIONS["crashed_resumed"]
    # completed and aborted are terminal
    assert _VALID_TRANSITIONS["completed"] == frozenset()
    assert _VALID_TRANSITIONS["aborted"] == frozenset()
