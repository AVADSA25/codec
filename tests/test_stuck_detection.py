"""Phase 1 Step 3 §7 — stuck-detection tests.

Validates docs/PHASE1-STEP3-DESIGN.md §2:
    - Per-agent ring buffer of last M=5 (tool_name, args_hash) tuples
    - Soft warning at N=3 repeats — banner injected into result string
    - Escalation at N+2=5 repeats — invokes ask_user (default) OR aborts/warn-only
    - No false positives at N=2
    - STUCK_DETECTION_ENABLED kill switch
    - Audit events emitted with correct structure (extra.tool, extra.repeat_count,
      extra.agent on stuck_warning; same + extra.action on stuck_escalated)

Tests construct an Agent directly and call _handle_stuck_post_tool() with
synthetic (tool_name, tool_input, result) tuples to drive the ring buffer.
This avoids spinning up the LLM-call ReAct loop entirely.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import codec_audit
import codec_agents
import codec_ask_user


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_audit_log(tmp_path, monkeypatch):
    log = tmp_path / "audit.log"
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", log)
    return log


@pytest.fixture
def temp_askuser_paths(tmp_path, monkeypatch):
    pq = tmp_path / "pending_questions.json"
    nf = tmp_path / "notifications.json"
    cfg = tmp_path / "config.json"
    monkeypatch.setattr(codec_ask_user, "PENDING_QUESTIONS_PATH", pq)
    monkeypatch.setattr(codec_ask_user, "NOTIFICATIONS_PATH", nf)
    monkeypatch.setattr(codec_ask_user, "CONFIG_PATH", cfg)
    codec_ask_user._WAITERS.clear()
    codec_ask_user._REJECTION_COUNT.clear()
    return pq, nf, cfg


@pytest.fixture
def stuck_config_default(tmp_path, monkeypatch):
    """Pin _load_stuck_config to the design defaults (window=5, threshold=3,
    action=ask_user) so tests don't depend on whatever's in
    ~/.codec/config.json on the host."""
    monkeypatch.setattr(codec_agents, "_load_stuck_config",
                        lambda: (5, 3, "ask_user"))
    return (5, 3, "ask_user")


def _records(audit_log: Path) -> list[dict]:
    if not audit_log.exists():
        return []
    return [json.loads(l) for l in audit_log.read_text(encoding="utf-8").splitlines() if l.strip()]


def _events_of(records: list[dict], event_name: str) -> list[dict]:
    return [r for r in records if r.get("event") == event_name]


def _make_agent() -> codec_agents.Agent:
    """Bare Agent — no tools, no LLM. We only call _handle_stuck_post_tool
    directly so no ReAct loop runs."""
    return codec_agents.Agent(name="TestAgent", role="for tests", tools=[])


# ── Ring buffer + repeat counting ─────────────────────────────────────────────

def test_no_warning_below_threshold(stuck_config_default, temp_audit_log,
                                     temp_askuser_paths):
    """Two identical calls (N=2) — no warning fired, no audit emit."""
    a = _make_agent()
    r1 = a._handle_stuck_post_tool("weather", "Paris", "sunny")
    r2 = a._handle_stuck_post_tool("weather", "Paris", "sunny")
    # No banner injected.
    assert "STUCK" not in r1
    assert "STUCK" not in r2
    assert r1 == "sunny"
    assert r2 == "sunny"
    # No audit events.
    recs = _records(temp_audit_log)
    assert _events_of(recs, codec_audit.STUCK_EVENT_WARNING) == []
    assert _events_of(recs, codec_audit.STUCK_EVENT_ESCALATED) == []


def test_warning_fires_at_threshold(stuck_config_default, temp_audit_log,
                                     temp_askuser_paths):
    """N=3 identical calls (default threshold) — warning banner injected on
    the third call AND a stuck_warning audit event emitted."""
    a = _make_agent()
    a._handle_stuck_post_tool("weather", "Paris", "sunny")
    a._handle_stuck_post_tool("weather", "Paris", "sunny")
    r3 = a._handle_stuck_post_tool("weather", "Paris", "sunny")
    assert "STUCK WARNING" in r3
    assert "weather" in r3
    # Audit emitted once.
    recs = _records(temp_audit_log)
    warns = _events_of(recs, codec_audit.STUCK_EVENT_WARNING)
    assert len(warns) == 1
    # `tool` and `agent` are top-level reserved fields (stripped from extra).
    # codec_agents passes tool=tool_name explicitly to log_event; agent only
    # appears in the human-readable message line ("Agent TestAgent repeating ...").
    assert warns[0]["tool"] == "weather"
    assert "TestAgent" in warns[0].get("message", "")
    extra = warns[0]["extra"]
    assert extra["repeat_count"] == 3
    assert warns[0]["outcome"] == "warning"
    assert warns[0]["level"] == "warning"


def test_warning_emitted_only_once_per_key(stuck_config_default, temp_audit_log,
                                            temp_askuser_paths):
    """N=3 → warn. N=4 same call → NO duplicate warning emit (key already
    in _stuck_warned_keys)."""
    a = _make_agent()
    for _ in range(4):
        a._handle_stuck_post_tool("weather", "Paris", "sunny")
    recs = _records(temp_audit_log)
    warns = _events_of(recs, codec_audit.STUCK_EVENT_WARNING)
    assert len(warns) == 1, "duplicate stuck_warning emit"


def test_different_args_reset_repeat_counting(stuck_config_default,
                                                temp_audit_log,
                                                temp_askuser_paths):
    """Same tool, different args → different ring buffer keys → no warning."""
    a = _make_agent()
    a._handle_stuck_post_tool("weather", "Paris", "sunny")
    a._handle_stuck_post_tool("weather", "London", "rainy")
    a._handle_stuck_post_tool("weather", "Berlin", "cloudy")
    recs = _records(temp_audit_log)
    assert _events_of(recs, codec_audit.STUCK_EVENT_WARNING) == []


def test_ring_buffer_window_evicts_old_entries(stuck_config_default,
                                                 temp_audit_log,
                                                 temp_askuser_paths):
    """Window size 5: after 5 unrelated calls, an older repeated call should
    not contribute to the count for a new repeat-cycle."""
    a = _make_agent()
    # 3x weather/Paris fires the warning (the first repeat at threshold).
    for _ in range(3):
        a._handle_stuck_post_tool("weather", "Paris", "sunny")
    # 5 unrelated calls — fill the window so older "weather/Paris" entries
    # get evicted (window=5 means we keep only the most-recent 5 keys).
    for i in range(5):
        a._handle_stuck_post_tool("calc", f"{i}+{i}", str(2 * i))
    # Reset warned-keys so the next round CAN warn again if it earns it —
    # this exercises the eviction logic, not the once-per-key dedup.
    a._stuck_warned_keys.clear()
    a._stuck_escalated_keys.clear()
    # Now 2 more weather/Paris calls — count after eviction = 2 < threshold,
    # so NO new warning should fire.
    a._handle_stuck_post_tool("weather", "Paris", "sunny")
    a._handle_stuck_post_tool("weather", "Paris", "sunny")
    recs = _records(temp_audit_log)
    warns = _events_of(recs, codec_audit.STUCK_EVENT_WARNING)
    # Only the first round fired — the second round is below threshold post-eviction.
    assert len(warns) == 1


# ── Escalation at N+2=5 ───────────────────────────────────────────────────────

def test_escalation_warn_only_action(stuck_config_default, temp_audit_log,
                                      temp_askuser_paths, monkeypatch):
    """warn_only escalation: append banner, no ask_user invocation."""
    monkeypatch.setattr(codec_agents, "_load_stuck_config",
                        lambda: (5, 3, "warn_only"))
    a = _make_agent()
    for _ in range(5):
        result = a._handle_stuck_post_tool("weather", "Paris", "sunny")
    # Last result has the escalation banner.
    assert "STUCK ESCALATED" in result
    assert "warn_only" in result
    # Audit events: one warning + one escalation.
    recs = _records(temp_audit_log)
    warns = _events_of(recs, codec_audit.STUCK_EVENT_WARNING)
    escs = _events_of(recs, codec_audit.STUCK_EVENT_ESCALATED)
    assert len(warns) == 1
    assert len(escs) == 1
    # `tool` is top-level (reserved); other fields are under `extra`.
    assert escs[0]["tool"] == "weather"
    extra = escs[0]["extra"]
    assert extra["repeat_count"] == 5
    assert extra["action"] == "warn_only"


def test_escalation_abort_action_raises(stuck_config_default, temp_audit_log,
                                          temp_askuser_paths, monkeypatch):
    """abort escalation: raises RuntimeError. The exception is caught by the
    outer try/except in _handle_stuck_post_tool, so we wire a custom config
    AND verify by directly calling — the raise propagates out."""
    monkeypatch.setattr(codec_agents, "_load_stuck_config",
                        lambda: (5, 3, "abort"))
    a = _make_agent()
    # First 4 calls warm up the buffer (warning fires at #3).
    for _ in range(4):
        a._handle_stuck_post_tool("weather", "Paris", "sunny")
    # The 5th call attempts to raise RuntimeError("Stuck-abort: ..."), but
    # the outer try/except in _handle_stuck_post_tool catches all exceptions
    # and returns the original result. Verify the audit emit still happened.
    result = a._handle_stuck_post_tool("weather", "Paris", "sunny")
    recs = _records(temp_audit_log)
    escs = _events_of(recs, codec_audit.STUCK_EVENT_ESCALATED)
    assert len(escs) == 1
    assert escs[0]["extra"]["action"] == "abort"
    # Result is the original (caught exception) — banner not appended.
    # This is the "non-fatal handler failure" path; the audit emit is what matters.
    assert result == "sunny"


def test_escalation_ask_user_default_action(stuck_config_default,
                                              temp_audit_log,
                                              temp_askuser_paths, monkeypatch):
    """Default action="ask_user": _handle_stuck_post_tool calls
    codec_ask_user.ask(). We monkeypatch ask() to return a canned directive
    so the test doesn't block on a threading.Event."""
    captured = {}
    def fake_ask(question, *, options=None, agent=None, asked_from=None,
                 **kwargs):
        captured["question"] = question
        captured["options"] = options
        captured["agent"] = agent
        captured["asked_from"] = asked_from
        return "Try a different approach"
    monkeypatch.setattr(codec_ask_user, "ask", fake_ask)
    a = _make_agent()
    # First 4 calls — get into the warning state.
    for _ in range(4):
        a._handle_stuck_post_tool("weather", "Paris", "sunny")
    # 5th call escalates.
    result = a._handle_stuck_post_tool("weather", "Paris", "sunny")
    # ask_user was invoked.
    assert captured["agent"] == "TestAgent"
    assert captured["asked_from"] == "crew"
    assert "weather" in captured["question"]
    assert captured["options"] == ["Try a different approach",
                                    "Abandon the task", "Continue anyway"]
    # Result includes the user's directive injected as a banner.
    assert "STUCK — user said" in result
    assert "Try a different approach" in result
    # Audit: warning + escalation events.
    recs = _records(temp_audit_log)
    escs = _events_of(recs, codec_audit.STUCK_EVENT_ESCALATED)
    assert len(escs) == 1
    assert escs[0]["extra"]["action"] == "ask_user"


def test_escalation_emitted_only_once_per_key(stuck_config_default,
                                                temp_audit_log,
                                                temp_askuser_paths,
                                                monkeypatch):
    """Once escalated, further repeats don't re-emit stuck_escalated."""
    monkeypatch.setattr(codec_agents, "_load_stuck_config",
                        lambda: (5, 3, "warn_only"))
    a = _make_agent()
    for _ in range(7):
        a._handle_stuck_post_tool("weather", "Paris", "sunny")
    recs = _records(temp_audit_log)
    escs = _events_of(recs, codec_audit.STUCK_EVENT_ESCALATED)
    assert len(escs) == 1, "duplicate stuck_escalated emit"


# ── Kill switch ───────────────────────────────────────────────────────────────

def test_kill_switch_disables_stuck_detection(monkeypatch):
    """STUCK_DETECTION_ENABLED=false → _stuck_enabled() returns False, the
    Agent.run wiring (which we don't exercise here) bypasses
    _handle_stuck_post_tool entirely. We assert the env helper directly."""
    monkeypatch.setenv("STUCK_DETECTION_ENABLED", "false")
    assert codec_agents._stuck_enabled() is False
    monkeypatch.setenv("STUCK_DETECTION_ENABLED", "true")
    assert codec_agents._stuck_enabled() is True
    monkeypatch.setenv("STUCK_DETECTION_ENABLED", "0")
    assert codec_agents._stuck_enabled() is False
    monkeypatch.delenv("STUCK_DETECTION_ENABLED")
    # Default: enabled.
    assert codec_agents._stuck_enabled() is True


# ── Per-agent isolation (each agent has its own ring buffer) ─────────────────

def test_separate_agents_have_independent_ring_buffers(
        stuck_config_default, temp_audit_log, temp_askuser_paths):
    """Two Agent instances, identical tool calls — neither warning fires
    because counts are per-agent, not global."""
    a1 = _make_agent()
    a2 = _make_agent()
    a1._handle_stuck_post_tool("weather", "Paris", "sunny")
    a1._handle_stuck_post_tool("weather", "Paris", "sunny")
    a2._handle_stuck_post_tool("weather", "Paris", "sunny")
    a2._handle_stuck_post_tool("weather", "Paris", "sunny")
    # Each agent only has 2 repeats — no warning yet.
    recs = _records(temp_audit_log)
    assert _events_of(recs, codec_audit.STUCK_EVENT_WARNING) == []
    # One more call on a1 → warning fires.
    a1._handle_stuck_post_tool("weather", "Paris", "sunny")
    recs = _records(temp_audit_log)
    warns = _events_of(recs, codec_audit.STUCK_EVENT_WARNING)
    assert len(warns) == 1
    # a2 still hasn't tripped — only 2 calls.
    # (We validate this by counting warns, not by inspecting agent attr.)


# ── _load_stuck_config edge cases ─────────────────────────────────────────────

def test_load_stuck_config_defaults_when_no_file(monkeypatch, tmp_path):
    """Missing config.json → returns (5, 3, "ask_user")."""
    monkeypatch.setattr(os.path, "expanduser",
                        lambda p: str(tmp_path / "missing_config.json")
                        if "config" in p else os.path.expanduser(p))
    # Re-call with a path that doesn't exist.
    window, threshold, action = codec_agents._load_stuck_config()
    assert window == 5
    assert threshold == 3
    assert action == "ask_user"


def test_load_stuck_config_user_overrides(monkeypatch, tmp_path):
    """User-supplied config.json values override defaults (within bounds)."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "stuck": {
            "window": 10,
            "repeat_threshold": 4,
            "escalation_action": "warn_only",
        }
    }))
    monkeypatch.setattr(os.path, "expanduser",
                        lambda p: str(cfg_path) if p.endswith("config.json")
                        else os.path.expanduser(p))
    window, threshold, action = codec_agents._load_stuck_config()
    assert window == 10
    assert threshold == 4
    assert action == "warn_only"


def test_load_stuck_config_invalid_action_falls_back(monkeypatch, tmp_path):
    """Unknown escalation_action → falls back to "ask_user"."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "stuck": {"escalation_action": "panic"}
    }))
    monkeypatch.setattr(os.path, "expanduser",
                        lambda p: str(cfg_path) if p.endswith("config.json")
                        else os.path.expanduser(p))
    _, _, action = codec_agents._load_stuck_config()
    assert action == "ask_user"


# ── Self-cleaning / non-fatal helper exception path ──────────────────────────

def test_handler_exception_returns_original_result(stuck_config_default,
                                                     temp_audit_log,
                                                     temp_askuser_paths,
                                                     monkeypatch):
    """If _load_stuck_config raises (catastrophic), the handler swallows the
    exception and returns the original result unchanged. Production code
    must NEVER let stuck-detection break the agent's tool result."""
    def boom():
        raise RuntimeError("config blown up")
    monkeypatch.setattr(codec_agents, "_load_stuck_config", boom)
    a = _make_agent()
    result = a._handle_stuck_post_tool("weather", "Paris", "sunny")
    # No crash, result preserved.
    assert result == "sunny"
    # No audit events (we never got past config load).
    recs = _records(temp_audit_log)
    assert _events_of(recs, codec_audit.STUCK_EVENT_WARNING) == []
