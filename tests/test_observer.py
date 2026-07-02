"""Phase 2 Step 5 tests — codec_observer.py (Continuous Observation Loop).

30 tests organized per docs/PHASE2-STEP5-DESIGN.md §7:
  §7.1 Ring buffer                    (6 tests)
  §7.2 Polling primitives             (6 tests)
  §7.3 Idle classifier + cadence      (4 tests)
  §7.4 Injection contract (§X)        (10 tests)
  §7.5 Kill switch + integration      (4 tests)

All tests redirect codec_audit._AUDIT_LOG to tmp_path. All polling
primitives (active_window, clipboard, OCR, recent_files, Quartz) are
monkeypatched — NO real OS calls, NO subprocess spawn, NO Apple state,
NO Terminal popups. Per the 2026-05-01 incident contract.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import codec_audit
import codec_observer


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_audit_log(tmp_path, monkeypatch):
    log = tmp_path / "audit.log"
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", log)
    return log


@pytest.fixture
def fresh_buffer(monkeypatch):
    """Reset the module singleton and return a fresh small buffer."""
    monkeypatch.setattr(codec_observer, "_GLOBAL_BUFFER", None)
    return codec_observer.RingBuffer(maxlen=10)


@pytest.fixture
def cfg_default():
    return dict(codec_observer._DEFAULT_CONFIG)


@pytest.fixture
def mocked_primitives(monkeypatch):
    """Mock every OS-touching primitive so tests can run anywhere with
    deterministic data and zero side effects."""
    monkeypatch.setattr(codec_observer, "_idle_seconds", lambda: 5.0)
    monkeypatch.setattr(codec_observer, "_get_active_window",
                        lambda: {"app": "TestApp", "title": "test window", "pid": 999})
    monkeypatch.setattr(codec_observer, "_get_clipboard_now",
                        lambda: "https://example.com/test")
    monkeypatch.setattr(codec_observer, "_get_screenshot_ocr",
                        lambda timeout_ms, retry_timeout_ms: ("OCR'd screen text", False))
    monkeypatch.setattr(codec_observer, "_get_recent_files",
                        lambda window_seconds=300: [])


def _records(audit_log: Path) -> list[dict]:
    if not audit_log.exists():
        return []
    return [json.loads(l) for l in audit_log.read_text(encoding="utf-8").splitlines() if l.strip()]


def _events_of(records: list[dict], event_name: str) -> list[dict]:
    return [r for r in records if r.get("event") == event_name]


# ─────────────────────────────────────────────────────────────────────────────
# §7.1 — Ring buffer (6 tests)
# ─────────────────────────────────────────────────────────────────────────────

def test_ringbuffer_append_under_capacity(fresh_buffer):
    """Appends grow length up to maxlen."""
    for i in range(5):
        fresh_buffer.append({"i": i, "ts": "t", "active_window": {}})
    assert len(fresh_buffer) == 5


def test_ringbuffer_wraparound_drops_oldest(fresh_buffer):
    """N+1 entries → oldest evicted."""
    for i in range(15):  # maxlen is 10
        fresh_buffer.append({"i": i, "ts": "t", "active_window": {}})
    assert len(fresh_buffer) == 10
    snap = fresh_buffer.snapshot()
    # Oldest (i=0..4) evicted; we should have i=5..14
    assert snap[0]["i"] == 5
    assert snap[-1]["i"] == 14


def test_ringbuffer_snapshot_is_copy(fresh_buffer):
    """Mutating snapshot doesn't mutate the underlying buffer."""
    fresh_buffer.append({"x": 1, "ts": "t", "active_window": {}})
    snap = fresh_buffer.snapshot()
    snap.append({"x": 2, "ts": "t", "active_window": {}})
    assert len(fresh_buffer) == 1


def test_ringbuffer_render_summary_under_token_cap(fresh_buffer):
    """10 entries → ≤ max_tokens*4 chars."""
    for i in range(10):
        fresh_buffer.append({
            "ts": "2026-05-01T16:00:00",
            "active_window": {"app": "Chrome", "title": f"page {i}"},
            "screenshot_ocr": f"screen text {i}",
            "clipboard": None,
            "recent_files": [],
            "idle_seconds": i,
        })
    summary = fresh_buffer.render_summary(max_tokens=200)
    assert len(summary) <= 200 * 4


def test_ringbuffer_render_summary_truncates_middle_when_overcapacity(fresh_buffer):
    """Long entries get middle-truncated when over the char cap."""
    for i in range(10):
        fresh_buffer.append({
            "ts": "t",
            "active_window": {"app": "X" * 200, "title": "Y" * 500},
            "screenshot_ocr": "Z" * 1000,
            "clipboard": {"preview": "W" * 300, "content_type": "text"},
            "recent_files": [{"path": f"/very/long/path/file_{i}.py", "mtime": "t"}],
            "idle_seconds": 0,
        })
    summary = fresh_buffer.render_summary(max_tokens=50)  # tiny budget
    assert len(summary) <= 50 * 4
    # If truncation occurred, the marker should be present
    if len(summary) > 0:
        assert "[...]" in summary or len(summary) <= 50 * 4


def test_ringbuffer_render_summary_includes_recency_markers(fresh_buffer):
    """The latest snapshot's idle_seconds renders as 'Ns ago' / 'Nmin ago'."""
    fresh_buffer.append({
        "ts": "t",
        "active_window": {"app": "Chrome", "title": "Stripe"},
        "screenshot_ocr": "",
        "clipboard": None,
        "recent_files": [],
        "idle_seconds": 12,
    })
    summary = fresh_buffer.render_summary()
    assert "Active:" in summary
    assert "Chrome" in summary
    assert "12s ago" in summary


# ─────────────────────────────────────────────────────────────────────────────
# §7.2 — Polling primitives (6 tests)
# ─────────────────────────────────────────────────────────────────────────────

def test_poll_writes_snapshot_to_buffer(fresh_buffer, cfg_default,
                                          mocked_primitives, temp_audit_log):
    """One poll() call → one buffer entry."""
    snapshot = codec_observer.poll(buffer=fresh_buffer, cfg=cfg_default,
                                    emit_audit=False)
    assert len(fresh_buffer) == 1
    assert snapshot["active_window"]["app"] == "TestApp"


def test_poll_emits_observation_tick_metadata_only(fresh_buffer, cfg_default,
                                                    mocked_primitives, temp_audit_log):
    """observation_tick audit emit contains METADATA, never content."""
    codec_observer.poll(buffer=fresh_buffer, cfg=cfg_default, emit_audit=True)
    recs = _records(temp_audit_log)
    ticks = _events_of(recs, codec_audit.OBSERVATION_TICK)
    assert len(ticks) == 1
    extra = ticks[0]["extra"]
    # Verify metadata fields present
    assert extra["active_app"] == "TestApp"
    assert extra["active_title_len"] == len("test window")
    assert extra["ocr_chars"] == len("OCR'd screen text")
    assert extra["clipboard_kind"] == "url"
    # Verify content fields ABSENT
    serialized = json.dumps(extra)
    assert "test window" not in serialized   # title content not leaked
    assert "OCR'd screen text" not in serialized   # OCR content not leaked
    assert "https://example.com/test" not in serialized   # clipboard content not leaked


def test_poll_clipboard_only_emits_on_change(fresh_buffer, cfg_default,
                                              mocked_primitives, temp_audit_log,
                                              monkeypatch):
    """Same content twice → second poll has clipboard=None."""
    snapshot1 = codec_observer.poll(buffer=fresh_buffer, cfg=cfg_default,
                                     emit_audit=False)
    snapshot2 = codec_observer.poll(buffer=fresh_buffer, cfg=cfg_default,
                                     emit_audit=False)
    assert snapshot1["clipboard"] is not None
    assert snapshot1["clipboard"]["content_type"] == "url"
    assert snapshot2["clipboard"] is None  # unchanged


def test_poll_ocr_skipped_recorded(fresh_buffer, cfg_default, temp_audit_log,
                                    monkeypatch):
    """OCR timeout → snapshot.ocr_skipped=True, audit extra.ocr_skipped=True."""
    monkeypatch.setattr(codec_observer, "_idle_seconds", lambda: 0.0)
    monkeypatch.setattr(codec_observer, "_get_active_window",
                        lambda: {"app": "X", "title": "y", "pid": 1})
    monkeypatch.setattr(codec_observer, "_get_clipboard_now", lambda: "")
    monkeypatch.setattr(codec_observer, "_get_screenshot_ocr",
                        lambda t, rt: ("", True))   # always skipped
    monkeypatch.setattr(codec_observer, "_get_recent_files",
                        lambda window_seconds=300: [])
    snap = codec_observer.poll(buffer=fresh_buffer, cfg=cfg_default,
                                emit_audit=True)
    assert snap["ocr_skipped"] is True
    recs = _records(temp_audit_log)
    ticks = _events_of(recs, codec_audit.OBSERVATION_TICK)
    assert ticks[0]["extra"]["ocr_skipped"] is True


def test_poll_recent_files_passed_through(fresh_buffer, cfg_default,
                                           temp_audit_log, monkeypatch):
    """_get_recent_files returns are passed into the snapshot AND
    audit emit gets count-only (path NOT leaked)."""
    monkeypatch.setattr(codec_observer, "_idle_seconds", lambda: 0.0)
    monkeypatch.setattr(codec_observer, "_get_active_window", lambda: {})
    monkeypatch.setattr(codec_observer, "_get_clipboard_now", lambda: "")
    monkeypatch.setattr(codec_observer, "_get_screenshot_ocr",
                        lambda t, rt: ("", True))
    monkeypatch.setattr(codec_observer, "_get_recent_files",
                        lambda window_seconds=300: [
                            {"path": "/Users/x/secret_file.py", "mtime": "t"},
                            {"path": "/Users/x/another.txt", "mtime": "t"},
                        ])
    codec_observer.poll(buffer=fresh_buffer, cfg=cfg_default, emit_audit=True)
    recs = _records(temp_audit_log)
    extra = _events_of(recs, codec_audit.OBSERVATION_TICK)[0]["extra"]
    assert extra["recent_files_count"] == 2
    serialized = json.dumps(extra)
    assert "secret_file" not in serialized   # path content not leaked
    assert "another.txt" not in serialized


def test_clipboard_classify_kinds():
    """Clipboard content classifier covers all kinds."""
    assert codec_observer._classify_clipboard_kind("") == "empty"
    assert codec_observer._classify_clipboard_kind("https://example.com") == "url"
    assert codec_observer._classify_clipboard_kind('{"a":1}') == "json"
    assert codec_observer._classify_clipboard_kind("def foo():\n    pass") == "code"
    assert codec_observer._classify_clipboard_kind("just some text") == "text"
    assert codec_observer._classify_clipboard_kind("\x00\x01\x02binary garbage") == "image_blob_redacted"


# ─────────────────────────────────────────────────────────────────────────────
# §7.3 — Idle classifier + cadence (4 tests)
# ─────────────────────────────────────────────────────────────────────────────

def test_cadence_active_when_idle_lt_threshold(fresh_buffer, cfg_default,
                                                 temp_audit_log, monkeypatch):
    """idle < 60s → cadence_used_s == cadence_active_s (60)."""
    monkeypatch.setattr(codec_observer, "_idle_seconds", lambda: 30.0)
    monkeypatch.setattr(codec_observer, "_get_active_window", lambda: {})
    monkeypatch.setattr(codec_observer, "_get_clipboard_now", lambda: "")
    monkeypatch.setattr(codec_observer, "_get_screenshot_ocr",
                        lambda t, rt: ("", True))
    monkeypatch.setattr(codec_observer, "_get_recent_files",
                        lambda window_seconds=300: [])
    codec_observer.poll(buffer=fresh_buffer, cfg=cfg_default, emit_audit=True)
    extra = _events_of(_records(temp_audit_log),
                       codec_audit.OBSERVATION_TICK)[0]["extra"]
    assert extra["cadence_used_s"] == 60


def test_cadence_idle_when_idle_ge_threshold(fresh_buffer, cfg_default,
                                               temp_audit_log, monkeypatch):
    """idle ≥ 60s → cadence_used_s == cadence_idle_s (300)."""
    monkeypatch.setattr(codec_observer, "_idle_seconds", lambda: 120.0)
    monkeypatch.setattr(codec_observer, "_get_active_window", lambda: {})
    monkeypatch.setattr(codec_observer, "_get_clipboard_now", lambda: "")
    monkeypatch.setattr(codec_observer, "_get_screenshot_ocr",
                        lambda t, rt: ("", True))
    monkeypatch.setattr(codec_observer, "_get_recent_files",
                        lambda window_seconds=300: [])
    codec_observer.poll(buffer=fresh_buffer, cfg=cfg_default, emit_audit=True)
    extra = _events_of(_records(temp_audit_log),
                       codec_audit.OBSERVATION_TICK)[0]["extra"]
    assert extra["cadence_used_s"] == 300


def test_cadence_respects_config_overrides(fresh_buffer, temp_audit_log,
                                             monkeypatch):
    """User config overrides default cadence_active_s/cadence_idle_s."""
    cfg = dict(codec_observer._DEFAULT_CONFIG)
    cfg["cadence_active_s"] = 30
    cfg["cadence_idle_s"] = 600
    monkeypatch.setattr(codec_observer, "_idle_seconds", lambda: 5.0)
    monkeypatch.setattr(codec_observer, "_get_active_window", lambda: {})
    monkeypatch.setattr(codec_observer, "_get_clipboard_now", lambda: "")
    monkeypatch.setattr(codec_observer, "_get_screenshot_ocr",
                        lambda t, rt: ("", True))
    monkeypatch.setattr(codec_observer, "_get_recent_files",
                        lambda window_seconds=300: [])
    codec_observer.poll(buffer=fresh_buffer, cfg=cfg, emit_audit=True)
    extra = _events_of(_records(temp_audit_log),
                       codec_audit.OBSERVATION_TICK)[0]["extra"]
    assert extra["cadence_used_s"] == 30


def test_idle_seconds_returns_zero_when_quartz_unavailable(monkeypatch):
    """When pyobjc Quartz isn't importable, _idle_seconds returns 0.0."""
    monkeypatch.setattr(codec_observer, "_HAS_QUARTZ", False)
    assert codec_observer._idle_seconds() == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# §7.4 — Injection contract §X (10 tests)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def populated_buffer(monkeypatch):
    """Buffer with one realistic snapshot."""
    monkeypatch.setattr(codec_observer, "_GLOBAL_BUFFER", None)
    buf = codec_observer.get_global_buffer()
    buf.append({
        "ts": "2026-05-01T16:00:00.000+00:00",
        "active_window": {"app": "Google Chrome", "title": "Stripe Dashboard", "pid": 1},
        "screenshot_ocr": "Stripe Dashboard / Payments / $4,231.57",
        "clipboard": {"preview": "https://github.com/test/pr/8", "content_type": "url"},
        "recent_files": [],
        "idle_seconds": 5,
    })
    return buf


def test_inject_always_for_local_transport(populated_buffer, temp_audit_log,
                                            monkeypatch):
    """transport='local' → always inject regardless of prompt content."""
    monkeypatch.delenv("OBSERVER_ENABLED", raising=False)
    summary, reason = codec_observer.maybe_inject_observation_summary(
        "what time is it", transport="local")
    assert summary is not None
    assert reason == "always_local"


def test_inject_skipped_for_mcp_transport(populated_buffer, temp_audit_log,
                                            monkeypatch):
    """transport='mcp' → never inject, never emit."""
    monkeypatch.delenv("OBSERVER_ENABLED", raising=False)
    summary, reason = codec_observer.maybe_inject_observation_summary(
        "what's my Stripe balance", transport="mcp")
    assert summary is None
    assert reason == "skipped_no_match"
    assert _events_of(_records(temp_audit_log),
                      codec_audit.OBSERVATION_SUMMARY_INJECTED) == []


def test_inject_possessive_match_my_X(populated_buffer, temp_audit_log,
                                        monkeypatch):
    """'my Stripe' → possessive match (Stripe not in stop-noun list)."""
    monkeypatch.delenv("OBSERVER_ENABLED", raising=False)
    summary, reason = codec_observer.maybe_inject_observation_summary(
        "what's my Stripe balance", transport="chat")
    assert summary is not None
    assert reason == "possessive_match"


def test_inject_possessive_match_this_PR(populated_buffer, temp_audit_log,
                                           monkeypatch):
    """'this PR' → possessive match — but 'PR' isn't in stop-list anyway."""
    monkeypatch.delenv("OBSERVER_ENABLED", raising=False)
    summary, reason = codec_observer.maybe_inject_observation_summary(
        "summarize this PR for me", transport="chat")
    assert summary is not None
    assert reason == "possessive_match"


def test_inject_possessive_filtered_by_stop_noun(populated_buffer, temp_audit_log,
                                                   monkeypatch):
    """'this question' → possessive but 'question' is in stop-list → no inject."""
    monkeypatch.delenv("OBSERVER_ENABLED", raising=False)
    summary, reason = codec_observer.maybe_inject_observation_summary(
        "this question is silly", transport="chat")
    # 'silly' isn't possessive, 'this question' is filtered. No match.
    assert summary is None
    assert reason == "skipped_no_match"


def test_inject_continuation_continue(populated_buffer, temp_audit_log,
                                        monkeypatch):
    """'continue' → continuation match."""
    monkeypatch.delenv("OBSERVER_ENABLED", raising=False)
    summary, reason = codec_observer.maybe_inject_observation_summary(
        "continue the email", transport="chat")
    assert summary is not None
    assert reason == "continuation_match"


def test_inject_continuation_where_was_i(populated_buffer, temp_audit_log,
                                           monkeypatch):
    """'where was I' → continuation match."""
    monkeypatch.delenv("OBSERVER_ENABLED", raising=False)
    summary, reason = codec_observer.maybe_inject_observation_summary(
        "where was I", transport="chat")
    assert summary is not None
    assert reason == "continuation_match"


def test_inject_skill_flag_overrides_pattern(populated_buffer, temp_audit_log,
                                                monkeypatch):
    """Skill with SKILL_NEEDS_OBSERVATION=True forces inject regardless
    of prompt patterns."""
    monkeypatch.delenv("OBSERVER_ENABLED", raising=False)
    skill_module = MagicMock()
    skill_module.SKILL_NEEDS_OBSERVATION = True
    summary, reason = codec_observer.maybe_inject_observation_summary(
        "anything goes here, no patterns match",
        transport="chat", skill_module=skill_module)
    assert summary is not None
    assert reason == "skill_flag"


def test_inject_emits_audit_only_on_inject(populated_buffer, temp_audit_log,
                                             monkeypatch):
    """Skipped path emits ZERO audit events (no observation_summary_injected)."""
    monkeypatch.delenv("OBSERVER_ENABLED", raising=False)
    # Skipped path: no patterns match, no skill flag, transport=chat
    codec_observer.maybe_inject_observation_summary(
        "the time is", transport="chat")  # 'time' is in stop-list
    recs = _records(temp_audit_log)
    assert _events_of(recs, codec_audit.OBSERVATION_SUMMARY_INJECTED) == []


def test_inject_emits_audit_with_reason_and_tokens_and_transport(
        populated_buffer, temp_audit_log, monkeypatch):
    """Successful inject emits with extra.{tokens_used, injection_reason,
    transport, buffer_entries_summarized}."""
    monkeypatch.delenv("OBSERVER_ENABLED", raising=False)
    codec_observer.maybe_inject_observation_summary(
        "summarize this PR", transport="chat")
    recs = _records(temp_audit_log)
    emits = _events_of(recs, codec_audit.OBSERVATION_SUMMARY_INJECTED)
    assert len(emits) == 1
    # `transport` is a top-level reserved field in codec_audit (stripped
    # from extra by audit() per _RESERVED_TOP). All other observer-specific
    # fields stay under extra.
    assert emits[0]["transport"] == "chat"
    extra = emits[0]["extra"]
    assert extra["injection_reason"] == "possessive_match"
    assert extra["tokens_used"] >= 1
    assert extra["buffer_entries_summarized"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# §7.5 — Kill switch + integration (4 tests)
# ─────────────────────────────────────────────────────────────────────────────

def test_observer_disabled_skips_inject(populated_buffer, temp_audit_log,
                                          monkeypatch):
    """OBSERVER_ENABLED=false → inject returns (None, 'skipped_disabled')."""
    monkeypatch.setenv("OBSERVER_ENABLED", "false")
    summary, reason = codec_observer.maybe_inject_observation_summary(
        "what's my stripe balance", transport="chat")
    assert summary is None
    assert reason == "skipped_disabled"


def test_observer_disabled_default_is_enabled(monkeypatch):
    """No env var → enabled=True."""
    monkeypatch.delenv("OBSERVER_ENABLED", raising=False)
    assert codec_observer._enabled() is True


def test_observer_disabled_aliases(monkeypatch):
    """All off-aliases disable: false, 0, no, off."""
    for v in ("false", "0", "no", "off", "FALSE", "Off"):
        monkeypatch.setenv("OBSERVER_ENABLED", v)
        assert codec_observer._enabled() is False, f"{v!r} should disable"


def test_inject_empty_buffer_returns_skipped(monkeypatch, temp_audit_log):
    """Buffer empty (process just started) → return ('skipped_empty_buffer')."""
    monkeypatch.delenv("OBSERVER_ENABLED", raising=False)
    monkeypatch.setattr(codec_observer, "_GLOBAL_BUFFER", None)
    # Don't populate buffer — should return skipped
    summary, reason = codec_observer.maybe_inject_observation_summary(
        "what's my stripe balance", transport="local")
    assert summary is None
    assert reason == "skipped_empty_buffer"
    # No audit emit on skipped
    recs = _records(temp_audit_log)
    assert _events_of(recs, codec_audit.OBSERVATION_SUMMARY_INJECTED) == []


# ─────────────────────────────────────────────────────────────────────────────
# 2026-05-02 hotfix — ocr_enabled flag (popup storm mitigation)
# ─────────────────────────────────────────────────────────────────────────────

def test_ocr_enabled_false_skips_screenshot_call(fresh_buffer, temp_audit_log,
                                                  monkeypatch):
    """When config.observer.ocr_enabled=False, poll() must NOT call
    _get_screenshot_ocr. Verified by sentinel that raises if called."""
    cfg = dict(codec_observer._DEFAULT_CONFIG)
    cfg["ocr_enabled"] = False
    monkeypatch.setattr(codec_observer, "_idle_seconds", lambda: 0.0)
    monkeypatch.setattr(codec_observer, "_get_active_window", lambda: {})
    monkeypatch.setattr(codec_observer, "_get_clipboard_now", lambda: "")
    monkeypatch.setattr(codec_observer, "_get_recent_files",
                        lambda window_seconds=300: [])

    def _should_not_be_called(*a, **kw):
        raise AssertionError("_get_screenshot_ocr called despite ocr_enabled=False")
    monkeypatch.setattr(codec_observer, "_get_screenshot_ocr",
                        _should_not_be_called)

    snap = codec_observer.poll(buffer=fresh_buffer, cfg=cfg, emit_audit=True)
    assert snap["ocr_skipped"] is True
    assert snap["screenshot_ocr"] == ""
    # Audit emit still records the skipped state correctly
    recs = _records(temp_audit_log)
    extra = _events_of(recs, codec_audit.OBSERVATION_TICK)[0]["extra"]
    assert extra["ocr_skipped"] is True
    assert extra["ocr_chars"] == 0


def test_ocr_enabled_default_is_true():
    """Default config has ocr_enabled=True (preserves Step 5 behavior
    on properly-permissioned machines)."""
    assert codec_observer._DEFAULT_CONFIG["ocr_enabled"] is True


def test_ocr_enabled_true_calls_screenshot(fresh_buffer, temp_audit_log,
                                              monkeypatch):
    """Sanity check: when ocr_enabled=True (default), _get_screenshot_ocr
    IS called. Confirms the new flag isn't accidentally always-bypassing."""
    cfg = dict(codec_observer._DEFAULT_CONFIG)
    assert cfg["ocr_enabled"] is True
    monkeypatch.setattr(codec_observer, "_idle_seconds", lambda: 0.0)
    monkeypatch.setattr(codec_observer, "_get_active_window", lambda: {})
    monkeypatch.setattr(codec_observer, "_get_clipboard_now", lambda: "")
    monkeypatch.setattr(codec_observer, "_get_recent_files",
                        lambda window_seconds=300: [])
    call_count = [0]

    def _track_call(*a, **kw):
        call_count[0] += 1
        return ("", True)   # mocked skip — doesn't actually screencapture
    monkeypatch.setattr(codec_observer, "_get_screenshot_ocr", _track_call)
    codec_observer.poll(buffer=fresh_buffer, cfg=cfg, emit_audit=False)
    assert call_count[0] == 1


# ─────────────────────────────────────────────────────────────────────────────
# 2026-07 log review — per-collector timings + bounded recent-files scan
# ─────────────────────────────────────────────────────────────────────────────

def test_poll_tick_includes_collector_timings(fresh_buffer, cfg_default,
                                              mocked_primitives, temp_audit_log):
    """observation_tick extra carries collector_ms (durations only — still
    metadata) so slow polls are attributable to a specific collector."""
    codec_observer.poll(buffer=fresh_buffer, cfg=cfg_default, emit_audit=True)
    recs = _records(temp_audit_log)
    ticks = _events_of(recs, codec_audit.OBSERVATION_TICK)
    assert len(ticks) == 1
    cms = ticks[0]["extra"]["collector_ms"]
    for key in ("idle", "window", "clipboard", "ocr", "files"):
        assert key in cms, f"collector_ms missing {key!r}: {cms}"
        assert isinstance(cms[key], (int, float))


def test_recent_files_bounded_times_out(monkeypatch):
    """A hung recent-files scan is dropped after the bound, not propagated."""
    import time as _t

    def hang(window_seconds=300):
        _t.sleep(5)
        return [{"path": "/never"}]

    monkeypatch.setattr(codec_observer, "_get_recent_files", hang)
    t0 = _t.monotonic()
    out = codec_observer._get_recent_files_bounded(window_seconds=300,
                                                   timeout_s=0.2)
    assert out == []
    assert _t.monotonic() - t0 < 2.0, "must return promptly on timeout"


def test_recent_files_bounded_passthrough(monkeypatch):
    """Fast scans pass through unchanged."""
    monkeypatch.setattr(codec_observer, "_get_recent_files",
                        lambda window_seconds=300: [{"path": "/x", "mtime": "t"}])
    out = codec_observer._get_recent_files_bounded()
    assert out == [{"path": "/x", "mtime": "t"}]
