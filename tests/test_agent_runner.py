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


# ─────────────────────────────────────────────────────────────────────────────
# Task 3 — PermissionViolation + permission_gate (4 tests)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def basic_grants():
    """Default per-agent grants used in permission gate tests."""
    return {
        "skills": ["weather", "calculator"],
        "read_paths": ["~/Documents/**"],
        "write_paths": ["~/.codec/agents/test/artifacts/**"],
        "network_domains": ["example.com"],
    }


@pytest.fixture
def empty_global_grants():
    return {
        "schema": 1, "version": 0,
        "skills": [], "read_paths": [], "write_paths": [], "network_domains": [],
    }


def test_permission_gate_allows_action_in_manifest(basic_grants, empty_global_grants):
    from codec_agent_runner import permission_gate, Action
    action = Action(skill="weather", task="weather in Paris",
                    is_destructive=False, network_call=False, touches_path=False)
    # No exception means allowed
    permission_gate(action, basic_grants, empty_global_grants)


def test_permission_gate_blocks_skill_not_in_grants(basic_grants, empty_global_grants):
    from codec_agent_runner import permission_gate, Action, PermissionViolation
    action = Action(skill="terminal", task="ls",
                    is_destructive=False, network_call=False, touches_path=False)
    with pytest.raises(PermissionViolation) as exc:
        permission_gate(action, basic_grants, empty_global_grants)
    assert exc.value.reason == "skill_not_authorized"
    assert exc.value.needed == "terminal"


def test_permission_gate_blocks_path_outside_write_paths(basic_grants, empty_global_grants):
    from codec_agent_runner import permission_gate, Action, PermissionViolation
    action = Action(skill="weather", task="x",
                    is_destructive=False, network_call=False,
                    touches_path=True, path="/etc/passwd")
    with pytest.raises(PermissionViolation) as exc:
        permission_gate(action, basic_grants, empty_global_grants)
    assert exc.value.reason == "path_not_authorized"
    assert exc.value.needed == "/etc/passwd"


def test_permission_gate_allows_via_global_allowlist(basic_grants):
    from codec_agent_runner import permission_gate, Action
    global_grants = {
        "schema": 1, "version": 1,
        "skills": ["terminal"],  # not in per-agent grants, but in global
        "read_paths": [], "write_paths": [], "network_domains": [],
    }
    action = Action(skill="terminal", task="ls",
                    is_destructive=False, network_call=False, touches_path=False)
    # Should NOT raise — global allowlist covers it
    permission_gate(action, basic_grants, global_grants)
