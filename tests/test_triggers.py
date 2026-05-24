"""Phase 2 Step 6 tests — codec_triggers.py (Trigger System).

35 tests organized per docs/PHASE2-STEP6-DESIGN.md §7:
  §7.1 Trigger validation        (5)
  §7.2 Match logic per type     (10)
  §7.3 Cooldown                  (5)
  §7.4 Confirmation + destructive (8)
  §7.5 Kill switches + integration (7)

CRITICAL test isolation:
  - codec_dispatch.run_skill is MOCKED in every test that touches the
    dispatch path. NEVER fire real skills — per the May 1 incident.
  - codec_ask_user.ask is MOCKED for confirmation tests. Never block
    on a real threading.Event.
  - codec_audit._AUDIT_LOG redirected to tmp_path.
  - _LAST_FIRED + _KILLED_CACHE module state reset per test.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import codec_audit
import codec_triggers


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_audit_log(tmp_path, monkeypatch):
    log = tmp_path / "audit.log"
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", log)
    return log


@pytest.fixture
def reset_state(monkeypatch, tmp_path):
    """Reset trigger state + redirect killed-keys file + mute config to tmp_path."""
    codec_triggers._reset_state_for_test()
    killed_path = tmp_path / "triggers_killed.json"
    mute_path = tmp_path / "triggers.json"
    monkeypatch.setattr(codec_triggers, "_KILLED_PATH", killed_path)
    monkeypatch.setattr(codec_triggers, "_MUTE_CONFIG_PATH", mute_path)
    codec_triggers._refresh_killed_cache()
    codec_triggers._refresh_mute_cache()
    yield
    codec_triggers._reset_state_for_test()


@pytest.fixture
def mock_dispatch(monkeypatch):
    """Mock codec_dispatch.run_skill to NEVER fire real skills.
    Returns the mock so tests can assert call args."""
    fake_dispatch = MagicMock(return_value="mocked dispatch result")
    fake_registry = MagicMock()
    fake_registry.get_meta = MagicMock(return_value={"SKILL_NAME": "fake_skill"})
    fake_registry.names = MagicMock(return_value=[])
    fake_registry.get_observation_trigger = MagicMock(return_value=None)

    fake_module = MagicMock()
    fake_module.run_skill = fake_dispatch
    fake_module.registry = fake_registry
    monkeypatch.setitem(sys.modules, "codec_dispatch", fake_module)
    return fake_dispatch


@pytest.fixture
def mock_ask_user(monkeypatch):
    """Mock codec_ask_user.ask to never block on threading.Event."""
    canned = {"answer": "Approve"}

    def _fake_ask(question, *, options=None, timeout=600, destructive=False,
                  destructive_verb=None, agent=None, crew_id=None,
                  asked_from="chat", tool_name=None):
        return canned["answer"]

    fake_module = MagicMock()
    fake_module.ask = _fake_ask
    fake_module.TIMEOUT_SENTINEL = "(no answer — timed out)"
    fake_module.DISABLED_SENTINEL = "(skill disabled)"
    fake_module._is_consenting_answer = MagicMock(return_value=(True, ""))
    monkeypatch.setitem(sys.modules, "codec_ask_user", fake_module)
    return canned


def _records(audit_log: Path) -> list[dict]:
    if not audit_log.exists():
        return []
    return [json.loads(l) for l in audit_log.read_text(encoding="utf-8").splitlines() if l.strip()]


def _events_of(records: list[dict], event_name: str) -> list[dict]:
    return [r for r in records if r.get("event") == event_name]


def _make_snapshot(active_app="Chrome", title="page",
                    clipboard_text=None, clipboard_kind="text",
                    recent_files=None) -> dict:
    snap = {
        "ts": "2026-05-02T18:00:00.000+00:00",
        "active_window": {"app": active_app, "title": title, "pid": 1},
        "screenshot_ocr": "",
        "ocr_skipped": True,
        "clipboard": {"preview": clipboard_text, "content_type": clipboard_kind}
                     if clipboard_text else None,
        "recent_files": recent_files or [],
        "idle_seconds": 5,
    }
    return snap


def _valid_trigger_dict(trigger_type="window_title_match", pattern="X",
                         cooldown=60, confirm=False, destructive=False):
    return {
        "type": trigger_type,
        "pattern": pattern,
        "cooldown_seconds": cooldown,
        "require_confirmation": confirm,
        "destructive": destructive,
    }


# ─────────────────────────────────────────────────────────────────────────────
# §7.1 — Trigger validation (5)
# ─────────────────────────────────────────────────────────────────────────────

def test_trigger_dict_with_all_required_fields_validates():
    ok, why = codec_triggers._validate_trigger_dict(_valid_trigger_dict())
    assert ok is True
    assert why == ""


def test_trigger_dict_missing_field_rejected():
    d = _valid_trigger_dict()
    del d["cooldown_seconds"]
    ok, why = codec_triggers._validate_trigger_dict(d)
    assert ok is False
    assert "cooldown_seconds" in why


def test_trigger_dict_unknown_type_rejected():
    ok, why = codec_triggers._validate_trigger_dict(
        _valid_trigger_dict(trigger_type="bad_type"))
    assert ok is False
    assert "unknown type" in why


def test_trigger_key_stable_across_reloads(reset_state):
    t1 = codec_triggers.Trigger.from_dict("skill_x", _valid_trigger_dict())
    t2 = codec_triggers.Trigger.from_dict("skill_x", _valid_trigger_dict())
    assert t1.key == t2.key


def test_trigger_key_changes_when_pattern_edited(reset_state):
    t1 = codec_triggers.Trigger.from_dict("skill_x",
                                            _valid_trigger_dict(pattern="A"))
    t2 = codec_triggers.Trigger.from_dict("skill_x",
                                            _valid_trigger_dict(pattern="B"))
    assert t1.key != t2.key


# ─────────────────────────────────────────────────────────────────────────────
# §7.2 — Match logic per type (10)
# ─────────────────────────────────────────────────────────────────────────────

def test_window_title_match_match():
    t = codec_triggers.Trigger.from_dict(
        "skill_x", _valid_trigger_dict("window_title_match", "Stripe"))
    snap = _make_snapshot(title="Stripe — Dashboard | dashboard.stripe.com")
    ok, summary = codec_triggers.matches(t, snap)
    assert ok
    assert "window:" in summary


def test_window_title_match_no_match():
    t = codec_triggers.Trigger.from_dict(
        "skill_x", _valid_trigger_dict("window_title_match", "Stripe"))
    snap = _make_snapshot(title="GitHub — open-source")
    ok, _ = codec_triggers.matches(t, snap)
    assert ok is False


def test_clipboard_pattern_match():
    t = codec_triggers.Trigger.from_dict(
        "skill_x", _valid_trigger_dict("clipboard_pattern",
                                         r"https?://github\.com/"))
    snap = _make_snapshot(clipboard_text="https://github.com/AVADSA25/codec",
                          clipboard_kind="url")
    ok, summary = codec_triggers.matches(t, snap)
    assert ok
    assert "clipboard:url" in summary


def test_clipboard_pattern_no_clipboard():
    t = codec_triggers.Trigger.from_dict(
        "skill_x", _valid_trigger_dict("clipboard_pattern", r"."))
    snap = _make_snapshot(clipboard_text=None)
    ok, _ = codec_triggers.matches(t, snap)
    assert ok is False


def test_file_change_glob_match():
    t = codec_triggers.Trigger.from_dict(
        "skill_x", _valid_trigger_dict("file_change", "~/Downloads/*.csv"))
    snap = _make_snapshot(recent_files=[
        {"path": os.path.expanduser("~/Downloads/data.csv"), "mtime": "now"},
        {"path": os.path.expanduser("~/Downloads/notes.txt"), "mtime": "now"},
    ])
    ok, summary = codec_triggers.matches(t, snap)
    assert ok
    assert "data.csv" in summary


def test_file_change_glob_no_match():
    t = codec_triggers.Trigger.from_dict(
        "skill_x", _valid_trigger_dict("file_change", "~/Downloads/*.csv"))
    snap = _make_snapshot(recent_files=[
        {"path": "/Users/x/Documents/notes.txt", "mtime": "now"},
    ])
    ok, _ = codec_triggers.matches(t, snap)
    assert ok is False


def test_time_match_within_minute(monkeypatch):
    """time pattern '* H * * *' matches when wall-clock hour matches."""
    from datetime import datetime
    fixed = datetime(2026, 5, 2, 14, 30)   # Friday May 2 14:30
    class _FakeDT:
        @staticmethod
        def now():
            return fixed
    monkeypatch.setattr("codec_triggers.datetime", _FakeDT)
    t = codec_triggers.Trigger.from_dict(
        "skill_x", _valid_trigger_dict("time", "* 14 * * *"))
    ok, _ = codec_triggers.matches(t, _make_snapshot())
    assert ok is True


def test_time_no_match():
    t = codec_triggers.Trigger.from_dict(
        "skill_x", _valid_trigger_dict("time", "* 99 * * *"))   # invalid hour
    ok, _ = codec_triggers.matches(t, _make_snapshot())
    assert ok is False


def test_compound_and_success():
    """compound AND requires all children to match."""
    pattern = {
        "op": "and",
        "children": [
            {"type": "window_title_match", "pattern": "Stripe"},
            {"type": "clipboard_pattern", "pattern": r"https?://"},
        ],
    }
    t = codec_triggers.Trigger.from_dict(
        "skill_x", _valid_trigger_dict("compound", pattern))
    snap = _make_snapshot(title="Stripe — Dashboard",
                           clipboard_text="https://example.com",
                           clipboard_kind="url")
    ok, summary = codec_triggers.matches(t, snap)
    assert ok is True
    assert "&" in summary    # combined summary


def test_compound_or_partial_match_succeeds():
    """compound OR succeeds when ANY child matches."""
    pattern = {
        "op": "or",
        "children": [
            {"type": "window_title_match", "pattern": "NotPresent"},
            {"type": "clipboard_pattern", "pattern": r"https?://"},
        ],
    }
    t = codec_triggers.Trigger.from_dict(
        "skill_x", _valid_trigger_dict("compound", pattern))
    snap = _make_snapshot(title="GitHub",
                           clipboard_text="https://example.com",
                           clipboard_kind="url")
    ok, _ = codec_triggers.matches(t, snap)
    assert ok is True


# ─────────────────────────────────────────────────────────────────────────────
# §7.3 — Cooldown (5)
# ─────────────────────────────────────────────────────────────────────────────

def test_cooldown_blocks_within_window(reset_state):
    t = codec_triggers.Trigger.from_dict(
        "skill_x", _valid_trigger_dict(cooldown=600))
    codec_triggers.mark_fired(t.key)
    remaining = codec_triggers.cooldown_remaining(t.key, t.cooldown_seconds)
    assert remaining > 599


def test_cooldown_allows_after_window(reset_state):
    t = codec_triggers.Trigger.from_dict(
        "skill_x", _valid_trigger_dict(cooldown=0))
    codec_triggers.mark_fired(t.key)
    # cooldown=0 → always ready
    assert codec_triggers.cooldown_remaining(t.key, t.cooldown_seconds) == 0.0


def test_cooldown_per_trigger_independent(reset_state):
    t1 = codec_triggers.Trigger.from_dict(
        "skill_a", _valid_trigger_dict(pattern="A", cooldown=600))
    t2 = codec_triggers.Trigger.from_dict(
        "skill_b", _valid_trigger_dict(pattern="B", cooldown=600))
    codec_triggers.mark_fired(t1.key)
    # t2 has not fired
    assert codec_triggers.cooldown_remaining(t2.key, t2.cooldown_seconds) == 0.0
    # t1 has
    assert codec_triggers.cooldown_remaining(t1.key, t1.cooldown_seconds) > 599


def test_cooldown_reset_on_pattern_edit(reset_state):
    """Editing a trigger's pattern → new key → cooldown state is fresh."""
    t1 = codec_triggers.Trigger.from_dict(
        "skill_x", _valid_trigger_dict(pattern="V1", cooldown=600))
    codec_triggers.mark_fired(t1.key)
    t2 = codec_triggers.Trigger.from_dict(
        "skill_x", _valid_trigger_dict(pattern="V2", cooldown=600))
    assert t1.key != t2.key
    assert codec_triggers.cooldown_remaining(t2.key, t2.cooldown_seconds) == 0.0


def test_cooldown_emits_trigger_blocked_with_reason(temp_audit_log,
                                                      reset_state,
                                                      mock_dispatch):
    """evaluate() → cooldown active → emits trigger_blocked w/ block_reason=cooldown."""
    t = codec_triggers.Trigger.from_dict(
        "skill_x", _valid_trigger_dict("window_title_match", "Stripe",
                                         cooldown=600))
    codec_triggers.mark_fired(t.key)
    # Inject our trigger via mocked registry
    fake_registry = MagicMock()
    fake_registry.names = MagicMock(return_value=["skill_x"])
    fake_registry.get_observation_trigger = MagicMock(
        return_value=t.raw)
    snap = _make_snapshot(title="Stripe — Dashboard")
    out = codec_triggers.evaluate(snap, registry=fake_registry, fire=True)
    assert any(r["status"] == "blocked_cooldown" for r in out)
    recs = _records(temp_audit_log)
    blocked = _events_of(recs, codec_audit.TRIGGER_BLOCKED)
    assert len(blocked) == 1
    assert blocked[0]["extra"]["block_reason"] == "cooldown"


# ─────────────────────────────────────────────────────────────────────────────
# §7.4 — Confirmation + destructive (8)
# ─────────────────────────────────────────────────────────────────────────────

def test_require_confirmation_false_destructive_false_fires_silently(
        temp_audit_log, reset_state, mock_dispatch):
    """confirm=False, destructive=False, cooldown ready → fire immediately."""
    t = codec_triggers.Trigger.from_dict(
        "skill_x", _valid_trigger_dict("window_title_match", "Stripe",
                                         cooldown=0, confirm=False,
                                         destructive=False))
    fake_reg = MagicMock()
    fake_reg.names = MagicMock(return_value=["skill_x"])
    fake_reg.get_observation_trigger = MagicMock(return_value=t.raw)
    fake_reg.get_meta = MagicMock(return_value={})
    snap = _make_snapshot(title="Stripe — Dashboard")
    out = codec_triggers.evaluate(snap, registry=fake_reg, fire=True)
    assert any(r["status"] == "matched_fired" for r in out)
    assert mock_dispatch.call_count == 1


def test_require_confirmation_true_uses_ask_user(
        temp_audit_log, reset_state, mock_dispatch, mock_ask_user):
    """confirm=True → routes through codec_ask_user.ask. Mocked ask returns
    'Approve' → fires."""
    mock_ask_user["answer"] = "Approve"
    t = codec_triggers.Trigger.from_dict(
        "skill_x", _valid_trigger_dict("window_title_match", "Stripe",
                                         cooldown=0, confirm=True))
    fake_reg = MagicMock()
    fake_reg.names = MagicMock(return_value=["skill_x"])
    fake_reg.get_observation_trigger = MagicMock(return_value=t.raw)
    fake_reg.get_meta = MagicMock(return_value={})
    snap = _make_snapshot(title="Stripe — Dashboard")
    out = codec_triggers.evaluate(snap, registry=fake_reg, fire=True)
    assert any(r["status"] == "matched_fired" for r in out)
    assert mock_dispatch.call_count == 1


def test_require_confirmation_user_skip_emits_blocked(
        temp_audit_log, reset_state, mock_dispatch, mock_ask_user):
    """User answers 'Skip' → trigger_blocked w/ user_skipped, no fire."""
    mock_ask_user["answer"] = "Skip"
    t = codec_triggers.Trigger.from_dict(
        "skill_x", _valid_trigger_dict("window_title_match", "Stripe",
                                         cooldown=0, confirm=True))
    fake_reg = MagicMock()
    fake_reg.names = MagicMock(return_value=["skill_x"])
    fake_reg.get_observation_trigger = MagicMock(return_value=t.raw)
    snap = _make_snapshot(title="Stripe — Dashboard")
    out = codec_triggers.evaluate(snap, registry=fake_reg, fire=True)
    assert any(r["status"] == "blocked_user_skipped" for r in out)
    assert mock_dispatch.call_count == 0
    blocked = _events_of(_records(temp_audit_log), codec_audit.TRIGGER_BLOCKED)
    assert blocked[0]["extra"]["block_reason"] == "user_skipped"


def test_require_confirmation_timeout_emits_blocked(
        temp_audit_log, reset_state, mock_dispatch, mock_ask_user):
    """ask returns TIMEOUT_SENTINEL → confirmation_timeout block reason."""
    mock_ask_user["answer"] = "(no answer — timed out)"
    t = codec_triggers.Trigger.from_dict(
        "skill_x", _valid_trigger_dict("window_title_match", "Stripe",
                                         cooldown=0, confirm=True))
    fake_reg = MagicMock()
    fake_reg.names = MagicMock(return_value=["skill_x"])
    fake_reg.get_observation_trigger = MagicMock(return_value=t.raw)
    snap = _make_snapshot(title="Stripe — Dashboard")
    out = codec_triggers.evaluate(snap, registry=fake_reg, fire=True)
    assert any(r["status"] == "blocked_confirmation_timeout" for r in out)
    assert mock_dispatch.call_count == 0


def test_destructive_routes_through_ask_user(
        temp_audit_log, reset_state, mock_dispatch, mock_ask_user):
    """destructive=True → ask_user.ask called with destructive=True (mocked)."""
    mock_ask_user["answer"] = "delete the row"     # contains verb (per Step 3)
    t = codec_triggers.Trigger.from_dict(
        "skill_x", _valid_trigger_dict("window_title_match", "Stripe",
                                         cooldown=0, destructive=True))
    fake_reg = MagicMock()
    fake_reg.names = MagicMock(return_value=["skill_x"])
    fake_reg.get_observation_trigger = MagicMock(return_value=t.raw)
    fake_reg.get_meta = MagicMock(return_value={})
    snap = _make_snapshot(title="Stripe")
    out = codec_triggers.evaluate(snap, registry=fake_reg, fire=True)
    assert any(r["status"] == "matched_fired" for r in out)


def test_destructive_two_strike_emits_blocked(
        temp_audit_log, reset_state, mock_dispatch, mock_ask_user):
    """destructive=True with TIMEOUT_SENTINEL answer → ambiguous_consent."""
    mock_ask_user["answer"] = "(no answer — timed out)"
    t = codec_triggers.Trigger.from_dict(
        "skill_x", _valid_trigger_dict("window_title_match", "Stripe",
                                         cooldown=0, destructive=True))
    fake_reg = MagicMock()
    fake_reg.names = MagicMock(return_value=["skill_x"])
    fake_reg.get_observation_trigger = MagicMock(return_value=t.raw)
    snap = _make_snapshot(title="Stripe")
    out = codec_triggers.evaluate(snap, registry=fake_reg, fire=True)
    assert any(r["status"] == "blocked_ambiguous_consent" for r in out)
    assert mock_dispatch.call_count == 0


def test_destructive_overrides_require_confirmation(
        temp_audit_log, reset_state, mock_dispatch, mock_ask_user):
    """destructive=True takes precedence over require_confirmation routing."""
    mock_ask_user["answer"] = "delete"
    t = codec_triggers.Trigger.from_dict(
        "skill_x", _valid_trigger_dict("window_title_match", "Stripe",
                                         cooldown=0, confirm=True,
                                         destructive=True))
    # Both flags set → destructive wins (uses strict consent)
    fake_reg = MagicMock()
    fake_reg.names = MagicMock(return_value=["skill_x"])
    fake_reg.get_observation_trigger = MagicMock(return_value=t.raw)
    fake_reg.get_meta = MagicMock(return_value={})
    snap = _make_snapshot(title="Stripe")
    out = codec_triggers.evaluate(snap, registry=fake_reg, fire=True)
    assert any(r["status"] == "matched_fired" for r in out)


def test_no_match_no_audit_emit(temp_audit_log, reset_state, mock_dispatch):
    """When no trigger matches, NO trigger_evaluated emit (avoids spam)."""
    t = codec_triggers.Trigger.from_dict(
        "skill_x", _valid_trigger_dict("window_title_match", "Stripe"))
    fake_reg = MagicMock()
    fake_reg.names = MagicMock(return_value=["skill_x"])
    fake_reg.get_observation_trigger = MagicMock(return_value=t.raw)
    snap = _make_snapshot(title="GitHub")  # NOT stripe
    codec_triggers.evaluate(snap, registry=fake_reg, fire=True)
    recs = _records(temp_audit_log)
    assert _events_of(recs, codec_audit.TRIGGER_EVALUATED) == []


# ─────────────────────────────────────────────────────────────────────────────
# §7.5 — Kill switches + integration (7)
# ─────────────────────────────────────────────────────────────────────────────

def test_per_trigger_kill_blocks_evaluation_silently(
        temp_audit_log, reset_state, mock_dispatch):
    """Killed trigger: skipped silently (NO trigger_blocked emit either)."""
    t = codec_triggers.Trigger.from_dict(
        "skill_x", _valid_trigger_dict("window_title_match", "Stripe",
                                         cooldown=0))
    codec_triggers.set_killed(t.key, True)
    fake_reg = MagicMock()
    fake_reg.names = MagicMock(return_value=["skill_x"])
    fake_reg.get_observation_trigger = MagicMock(return_value=t.raw)
    snap = _make_snapshot(title="Stripe")
    out = codec_triggers.evaluate(snap, registry=fake_reg, fire=True)
    assert any(r["status"] == "blocked_killed" for r in out)
    # No trigger_blocked emit (silent)
    assert _events_of(_records(temp_audit_log), codec_audit.TRIGGER_BLOCKED) == []
    # And no dispatch
    assert mock_dispatch.call_count == 0


def test_per_trigger_kill_state_persists_to_file(reset_state):
    t = codec_triggers.Trigger.from_dict(
        "skill_x", _valid_trigger_dict())
    codec_triggers.set_killed(t.key, True)
    # Read the file back
    assert codec_triggers._KILLED_PATH.exists()
    data = json.loads(codec_triggers._KILLED_PATH.read_text())
    assert t.key in data["killed_keys"]
    # Toggle off
    codec_triggers.set_killed(t.key, False)
    data = json.loads(codec_triggers._KILLED_PATH.read_text())
    assert t.key not in data["killed_keys"]


def test_global_TRIGGERS_ENABLED_false_skips_evaluate(
        temp_audit_log, reset_state, mock_dispatch, monkeypatch):
    monkeypatch.setenv("TRIGGERS_ENABLED", "false")
    t = codec_triggers.Trigger.from_dict(
        "skill_x", _valid_trigger_dict("window_title_match", "Stripe",
                                         cooldown=0))
    fake_reg = MagicMock()
    fake_reg.names = MagicMock(return_value=["skill_x"])
    fake_reg.get_observation_trigger = MagicMock(return_value=t.raw)
    snap = _make_snapshot(title="Stripe")
    out = codec_triggers.evaluate(snap, registry=fake_reg, fire=True)
    assert out == []   # full early-exit; no triggers evaluated
    assert mock_dispatch.call_count == 0


def test_global_TRIGGERS_ENABLED_default_true(monkeypatch):
    monkeypatch.delenv("TRIGGERS_ENABLED", raising=False)
    assert codec_triggers._enabled() is True


def test_TRIGGERS_ENABLED_off_aliases(monkeypatch):
    for v in ("false", "0", "no", "off", "FALSE"):
        monkeypatch.setenv("TRIGGERS_ENABLED", v)
        assert codec_triggers._enabled() is False


def test_observer_poll_evaluates_triggers(temp_audit_log, reset_state,
                                            monkeypatch):
    """Integration: codec_observer.poll() calls codec_triggers.evaluate()."""
    import codec_observer
    monkeypatch.setattr(codec_observer, "_idle_seconds", lambda: 0.0)
    monkeypatch.setattr(codec_observer, "_get_active_window",
                        lambda: {"app": "X", "title": "y", "pid": 1})
    monkeypatch.setattr(codec_observer, "_get_clipboard_now", lambda: "")
    monkeypatch.setattr(codec_observer, "_get_screenshot_ocr",
                        lambda t, rt: ("", True))
    monkeypatch.setattr(codec_observer, "_get_recent_files",
                        lambda window_seconds=300: [])
    # Track evaluate calls
    called = []
    monkeypatch.setattr(codec_triggers, "evaluate",
                        lambda snap, **kw: called.append(snap) or [])
    cfg = dict(codec_observer._DEFAULT_CONFIG)
    cfg["ocr_enabled"] = False
    fresh_buf = codec_observer.RingBuffer(maxlen=10)
    codec_observer.poll(buffer=fresh_buf, cfg=cfg, emit_audit=True)
    assert len(called) == 1, "observer.poll should call triggers.evaluate"


# ─────────────────────────────────────────────────────────────────────────────
# §7.6 — Runtime mute config (3)
# ─────────────────────────────────────────────────────────────────────────────

def test_muted_skill_name_skips_fire(temp_audit_log, reset_state, mock_dispatch):
    """Skill listed in muted_skills → trigger evaluation skips dispatch and
    emits trigger_muted. No real fire."""
    codec_triggers._MUTE_CONFIG_PATH.write_text(
        json.dumps({"muted_skills": ["skill_x"]}))
    codec_triggers._refresh_mute_cache()

    t = codec_triggers.Trigger.from_dict(
        "skill_x", _valid_trigger_dict("window_title_match", "Stripe",
                                         cooldown=0))
    fake_reg = MagicMock()
    fake_reg.names = MagicMock(return_value=["skill_x"])
    fake_reg.get_observation_trigger = MagicMock(return_value=t.raw)
    fake_reg.get_meta = MagicMock(return_value={})
    snap = _make_snapshot(title="Stripe — Dashboard")
    out = codec_triggers.evaluate(snap, registry=fake_reg, fire=True)

    assert any(r["status"] == "blocked_muted" for r in out)
    assert mock_dispatch.call_count == 0
    muted = _events_of(_records(temp_audit_log), codec_audit.TRIGGER_MUTED)
    assert len(muted) == 1
    assert muted[0]["extra"]["skill_name"] == "skill_x"
    assert muted[0]["extra"]["mute_source"] == "muted_skills"


def test_muted_until_past_timestamp_not_muted(reset_state):
    """muted_until[skill] in the past → _is_muted returns False (expired)."""
    past_iso = "2020-01-01T00:00:00+00:00"
    codec_triggers._MUTE_CONFIG_PATH.write_text(
        json.dumps({"muted_until": {"skill_x": past_iso}}))
    codec_triggers._refresh_mute_cache()

    assert codec_triggers._is_muted("skill_x") is False


def test_muted_until_future_timestamp_muted(reset_state):
    """muted_until[skill] in the future → _is_muted returns True."""
    future_iso = "2099-01-01T00:00:00+00:00"
    codec_triggers._MUTE_CONFIG_PATH.write_text(
        json.dumps({"muted_until": {"skill_x": future_iso}}))
    codec_triggers._refresh_mute_cache()

    assert codec_triggers._is_muted("skill_x") is True


def test_skill_registry_extracts_SKILL_OBSERVATION_TRIGGER(tmp_path):
    """Integration: a skill file with SKILL_OBSERVATION_TRIGGER is picked
    up by the registry's AST scan."""
    import codec_skill_registry
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir()
    (skill_dir / "test_skill.py").write_text(
        'SKILL_NAME = "test_skill"\n'
        'SKILL_DESCRIPTION = "test"\n'
        'SKILL_TRIGGERS = ["test"]\n'
        'SKILL_OBSERVATION_TRIGGER = {\n'
        '    "type": "window_title_match",\n'
        '    "pattern": "Stripe",\n'
        '    "cooldown_seconds": 600,\n'
        '    "require_confirmation": True,\n'
        '    "destructive": False,\n'
        '}\n'
        'def run(task, app="", ctx=""):\n'
        '    return "ok"\n'
    )
    reg = codec_skill_registry.SkillRegistry(str(skill_dir))
    reg.scan()
    trig = reg.get_observation_trigger("test_skill")
    assert trig is not None
    assert trig["type"] == "window_title_match"
    assert trig["pattern"] == "Stripe"
    assert trig["cooldown_seconds"] == 600
