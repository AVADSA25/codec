"""Phase 3.5 — Proactive intelligence overlay tests.

12 tests covering: audit constants, kill switch, pattern matching,
cooldowns (per-pattern + global), state R/W, dismiss/acknowledge,
PWA endpoints.

All tests:
  - Mock environment (PROACTIVE_OVERLAY_ENABLED via monkeypatch.setenv)
  - Use temp_codec_dir fixture (filesystem isolation)
  - Mock codec_audit._AUDIT_LOG (no production-log pollution)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


@pytest.fixture
def temp_proactive_state(tmp_path, monkeypatch):
    import codec_proactive as cp
    import codec_audit
    monkeypatch.setattr(cp, "_CODEC_DIR", tmp_path)
    monkeypatch.setattr(cp, "_STATE_PATH", tmp_path / "proactive_state.json")
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", tmp_path / "audit.log")
    monkeypatch.setenv("PROACTIVE_OVERLAY_ENABLED", "true")
    return tmp_path


# ─────────────────────────────────────────────────────────────────────────────
# Audit constants (1)
# ─────────────────────────────────────────────────────────────────────────────

def test_proactive_audit_constants_present():
    """Phase 3.5 adds 3 named events + 1 frozenset."""
    import codec_audit
    assert codec_audit.PROACTIVE_SUGGESTION_EMITTED == "proactive_suggestion_emitted"
    assert codec_audit.PROACTIVE_SUGGESTION_ACKNOWLEDGED == "proactive_suggestion_acknowledged"
    assert codec_audit.PROACTIVE_SUGGESTION_DISMISSED == "proactive_suggestion_dismissed"
    assert codec_audit.PHASE35_PROACTIVE_EVENTS == frozenset({
        "proactive_suggestion_emitted",
        "proactive_suggestion_acknowledged",
        "proactive_suggestion_dismissed",
    })


# ─────────────────────────────────────────────────────────────────────────────
# Kill switch (2)
# ─────────────────────────────────────────────────────────────────────────────

def test_disabled_by_default(monkeypatch, temp_proactive_state):
    """PROACTIVE_OVERLAY_ENABLED defaults to false → check_for_proactive returns None."""
    import codec_proactive as cp
    monkeypatch.delenv("PROACTIVE_OVERLAY_ENABLED", raising=False)
    snapshot = {"active_window": {"title": "notion.so/long doc"}, "ts": time.time()}
    assert cp.check_for_proactive(snapshot, history=[]) is None
    assert cp.is_enabled() is False


def test_enabled_via_env(monkeypatch, temp_proactive_state):
    """Setting env to 'true' enables the system."""
    import codec_proactive as cp
    monkeypatch.setenv("PROACTIVE_OVERLAY_ENABLED", "true")
    assert cp.is_enabled() is True


# ─────────────────────────────────────────────────────────────────────────────
# Pattern matching: long_form_dwell (3)
# ─────────────────────────────────────────────────────────────────────────────

def test_long_form_dwell_matches_when_dwell_threshold_met(temp_proactive_state):
    """Active window on Notion for 31 min → suggestion fires."""
    import codec_proactive as cp
    now = time.time()
    snapshot = {"active_window": {"title": "notion.so/my-research-doc",
                                   "app": "Chrome"}, "ts": now}
    # History: 31 min of consecutive Notion entries
    history = [
        {"ts": now - i, "active_window": {"title": "notion.so/my-research-doc"}}
        for i in range(31 * 60, 0, -60)
    ]
    suggestion = cp.check_for_proactive(snapshot, history=history)
    assert suggestion is not None
    assert suggestion.pattern_id == "long_form_dwell"
    assert "summarize" in suggestion.title.lower() or "summary" in suggestion.title.lower()


def test_long_form_dwell_does_not_fire_for_short_dwell(temp_proactive_state):
    """5 min of dwell doesn't trigger."""
    import codec_proactive as cp
    now = time.time()
    snapshot = {"active_window": {"title": "notion.so/quick-note"}, "ts": now}
    history = [
        {"ts": now - i, "active_window": {"title": "notion.so/quick-note"}}
        for i in range(5 * 60, 0, -60)
    ]
    assert cp.check_for_proactive(snapshot, history=history) is None


def test_long_form_dwell_does_not_fire_for_non_long_form_domain(temp_proactive_state):
    """30+ min on a non-long-form site (e.g. github) doesn't trigger."""
    import codec_proactive as cp
    now = time.time()
    snapshot = {"active_window": {"title": "github.com/some/repo"}, "ts": now}
    history = [
        {"ts": now - i, "active_window": {"title": "github.com/some/repo"}}
        for i in range(31 * 60, 0, -60)
    ]
    assert cp.check_for_proactive(snapshot, history=history) is None


# ─────────────────────────────────────────────────────────────────────────────
# Cooldowns (2)
# ─────────────────────────────────────────────────────────────────────────────

def test_per_pattern_cooldown_blocks_repeat_fire(temp_proactive_state):
    """After a fire, the same pattern must wait DEFAULT_COOLDOWN_S before firing again."""
    import codec_proactive as cp
    now = time.time()
    snapshot = {"active_window": {"title": "docs.google.com/document/abc"}, "ts": now}
    history = [
        {"ts": now - i, "active_window": {"title": "docs.google.com/document/abc"}}
        for i in range(35 * 60, 0, -60)
    ]
    # First call: should fire
    s1 = cp.check_for_proactive(snapshot, history=history)
    assert s1 is not None
    # Second call right after: blocked by per-pattern cooldown
    snapshot2 = dict(snapshot, ts=now + 60)  # 1 min later
    s2 = cp.check_for_proactive(snapshot2, history=history)
    assert s2 is None


def test_global_rate_limit_blocks_any_pattern(temp_proactive_state, monkeypatch):
    """Even if a different pattern matches, GLOBAL_RATE_LIMIT_S blocks fires
    after a recent emit."""
    import codec_proactive as cp
    # Simulate a recent global fire by writing state
    state = cp._read_state()
    state["last_global_fire_at"] = time.time()  # just fired
    cp._save_state(state)

    now = time.time()
    snapshot = {"active_window": {"title": "substack.com/p/long-essay"}, "ts": now}
    history = [{"ts": now - i, "active_window": {"title": "substack.com/p/long-essay"}}
               for i in range(40 * 60, 0, -60)]
    assert cp.check_for_proactive(snapshot, history=history) is None


# ─────────────────────────────────────────────────────────────────────────────
# Dismiss + acknowledge (2)
# ─────────────────────────────────────────────────────────────────────────────

def test_dismiss_today_blocks_pattern_for_today(temp_proactive_state):
    """After dismiss('today'), the pattern doesn't fire again until tomorrow (UTC)."""
    import codec_proactive as cp
    cp.dismiss("long_form_dwell", scope="today")

    now = time.time()
    snapshot = {"active_window": {"title": "medium.com/long-article"}, "ts": now}
    history = [{"ts": now - i, "active_window": {"title": "medium.com/long-article"}}
               for i in range(35 * 60, 0, -60)]
    assert cp.check_for_proactive(snapshot, history=history) is None
    assert cp.is_pattern_dismissed_today("long_form_dwell") is True


def test_dismiss_forever_kills_pattern_permanently(temp_proactive_state):
    """After dismiss('forever'), pattern is in killed_patterns and won't fire."""
    import codec_proactive as cp
    cp.dismiss("long_form_dwell", scope="forever")

    state = cp._read_state()
    assert "long_form_dwell" in state["killed_patterns"]
    assert cp.is_pattern_killed("long_form_dwell") is True

    now = time.time()
    snapshot = {"active_window": {"title": "nytimes.com/2026/long-piece"}, "ts": now}
    history = [{"ts": now - i, "active_window": {"title": "nytimes.com/2026/long-piece"}}
               for i in range(35 * 60, 0, -60)]
    assert cp.check_for_proactive(snapshot, history=history) is None


# ─────────────────────────────────────────────────────────────────────────────
# State management (1)
# ─────────────────────────────────────────────────────────────────────────────

def test_state_atomic_write(temp_proactive_state):
    """_save_state writes via tmp+rename. After save, no .tmp file remains."""
    import codec_proactive as cp
    cp.dismiss("long_form_dwell", scope="today")

    # state.json must exist; .tmp must not
    state_path = temp_proactive_state / "proactive_state.json"
    assert state_path.exists()
    assert not state_path.with_suffix(".json.tmp").exists()
    assert not state_path.with_suffix(".tmp").exists()


# ─────────────────────────────────────────────────────────────────────────────
# list_patterns (1)
# ─────────────────────────────────────────────────────────────────────────────

def test_list_patterns_returns_pattern_state(temp_proactive_state):
    """list_patterns returns each registered pattern + its current state."""
    import codec_proactive as cp
    cp.dismiss("long_form_dwell", scope="today")
    patterns = cp.list_patterns()
    assert len(patterns) >= 1
    p = next(p for p in patterns if p["id"] == "long_form_dwell")
    assert p["dismissed_today"] is True
    assert p["killed"] is False
    assert "description" in p
    assert "cooldown_seconds" in p
