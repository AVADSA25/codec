"""Tests for the audit query API — read_events() + get_stats().

Both functions read the unified schema:1 envelope (§6) from ~/.codec/audit.log
+ rotated audit.log.YYYY-MM-DD files and shape it for the /audit dashboard
(codec_audit.html), which expects per-event `cat`/`lvl`/`sum`/`ts`/`src` and
stats `total_24h`/`errors_24h`/`by_category`/`by_level`. Neither function
writes anything — the write path (audit()/log_event()) is untouched.

Tests redirect codec_audit._AUDIT_LOG and _AUDIT_DIR to a temp directory so
the real ~/.codec/audit.log is never touched.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import codec_audit


@pytest.fixture
def isolated_audit(tmp_path, monkeypatch):
    """Redirect both the live log and the rotation directory to tmp_path."""
    test_log = tmp_path / "audit.log"
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", test_log)
    monkeypatch.setattr(codec_audit, "_AUDIT_DIR", tmp_path)
    return tmp_path


def _write_raw(path: Path, records: list[dict]) -> None:
    """Write raw JSON-line records directly (bypasses audit() for
    deterministic timestamps in tests)."""
    with open(path, "a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _rec(**overrides) -> dict:
    base = {
        "ts": "2026-07-02T12:00:00.000+00:00",
        "schema": 1,
        "event": "tool_result",
        "source": "codec-mcp-http",
        "tool": "weather",
        "outcome": "ok",
        "transport": "http",
    }
    base.update(overrides)
    return base


# ── read_events: empty / basic shape ────────────────────────────────────────

def test_read_events_empty_when_no_log(isolated_audit):
    assert codec_audit.read_events() == []


def test_read_events_returns_ui_facing_fields(isolated_audit):
    codec_audit.audit("weather", event="tool_result", source="codec-mcp-http",
                       outcome="ok", message="fetched forecast")
    events = codec_audit.read_events()
    assert len(events) == 1
    ev = events[0]
    for key in ("ts", "cat", "lvl", "src", "sum"):
        assert key in ev, f"missing UI field: {key}"
    assert ev["src"] == "codec-mcp-http"
    assert ev["sum"] == "fetched forecast"
    assert ev["lvl"] == "info"


def test_read_events_newest_first(isolated_audit):
    codec_audit.audit("weather", event="tool_result", source="codec-mcp-http")
    codec_audit.audit("translate", event="tool_result", source="codec-mcp-http")
    events = codec_audit.read_events()
    assert [e["tool"] for e in events] == ["translate", "weather"]


def test_read_events_skips_malformed_lines(isolated_audit, tmp_path):
    codec_audit.audit("weather", event="tool_result", source="codec-mcp-http")
    with open(codec_audit._AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write("not json\n")
        f.write("\n")  # blank line
    events = codec_audit.read_events()
    assert len(events) == 1


def test_read_events_ignores_lock_sidecar_file(isolated_audit, tmp_path):
    (tmp_path / "audit.log.lock").write_text("irrelevant flock sidecar")
    codec_audit.audit("weather", event="tool_result", source="codec-mcp-http")
    events = codec_audit.read_events()
    assert len(events) == 1


# ── read_events: filters ────────────────────────────────────────────────────

def test_read_events_filters_by_category(isolated_audit):
    codec_audit.audit("weather", event="tool_result", source="codec-mcp-http")
    codec_audit.log_event("voice_session_start", "codec-voice", "voice up")
    events = codec_audit.read_events(categories=["voice"])
    assert len(events) == 1
    assert events[0]["cat"] == "voice"


def test_read_events_filters_by_level(isolated_audit):
    codec_audit.log_event("service_down", "codec-heartbeat", "down", level="error")
    codec_audit.log_event("heartbeat_tick", "codec-heartbeat", "tick", level="info")
    events = codec_audit.read_events(level="error")
    assert len(events) == 1
    assert events[0]["lvl"] == "error"


def test_read_events_filters_by_search_case_insensitive(isolated_audit):
    codec_audit.audit("weather", event="tool_result", source="codec-mcp-http", message="Forecast OK")
    codec_audit.audit("translate", event="tool_result", source="codec-mcp-http", message="Translated text")
    events = codec_audit.read_events(search="forecast")
    assert len(events) == 1
    assert events[0]["tool"] == "weather"


def test_read_events_respects_limit(isolated_audit):
    for i in range(5):
        codec_audit.audit(f"tool{i}", event="tool_result", source="codec-mcp-http")
    events = codec_audit.read_events(limit=2)
    assert len(events) == 2


def test_read_events_filters_by_since_until(isolated_audit):
    _write_raw(codec_audit._AUDIT_LOG, [
        _rec(ts="2026-07-01T00:00:00.000+00:00", tool="old"),
        _rec(ts="2026-07-02T12:00:00.000+00:00", tool="mid"),
        _rec(ts="2026-07-03T00:00:00.000+00:00", tool="new"),
    ])
    events = codec_audit.read_events(since="2026-07-02T00:00:00.000Z",
                                      until="2026-07-02T23:59:59.999Z")
    assert [e["tool"] for e in events] == ["mid"]


# ── read_events: spans rotated files ────────────────────────────────────────

def test_read_events_spans_rotated_files(isolated_audit, tmp_path):
    rotated = tmp_path / "audit.log.2026-07-01"
    _write_raw(rotated, [_rec(ts="2026-07-01T10:00:00.000+00:00", tool="yesterday")])
    _write_raw(codec_audit._AUDIT_LOG, [_rec(ts="2026-07-02T10:00:00.000+00:00", tool="today")])
    events = codec_audit.read_events()
    assert [e["tool"] for e in events] == ["today", "yesterday"]


# ── get_stats ────────────────────────────────────────────────────────────────

def test_get_stats_empty_when_no_log(isolated_audit):
    stats = codec_audit.get_stats(hours=24)
    assert stats == {
        "total_24h": 0,
        "errors_24h": 0,
        "by_category": {},
        "by_level": {},
    }


def test_get_stats_counts_totals_and_errors(isolated_audit):
    codec_audit.audit("weather", event="tool_result", source="codec-mcp-http", outcome="ok")
    codec_audit.audit("weather", event="tool_result", source="codec-mcp-http", outcome="error",
                       error="boom", error_type="RuntimeError")
    stats = codec_audit.get_stats(hours=24)
    assert stats["total_24h"] == 2
    assert stats["errors_24h"] == 1
    assert stats["by_level"]["error"] == 1
    assert stats["by_level"]["info"] == 1


def test_get_stats_by_category_has_expected_keys(isolated_audit):
    codec_audit.audit("weather", event="tool_result", source="codec-mcp-http")
    codec_audit.log_event("voice_session_start", "codec-voice", "voice up")
    stats = codec_audit.get_stats(hours=24)
    assert stats["by_category"]["skill"] == 1
    assert stats["by_category"]["voice"] == 1


def test_get_stats_excludes_events_outside_window(isolated_audit):
    _write_raw(codec_audit._AUDIT_LOG, [
        _rec(ts="2020-01-01T00:00:00.000+00:00", tool="ancient"),
    ])
    codec_audit.audit("weather", event="tool_result", source="codec-mcp-http")
    stats = codec_audit.get_stats(hours=24)
    assert stats["total_24h"] == 1


# ── category / level derivation ─────────────────────────────────────────────

@pytest.mark.parametrize("record,expected_cat", [
    (_rec(event="tool_result", source="codec-mcp-http", tool="weather"), "skill"),
    (_rec(event="voice_mode_changed", source="codec-voice", tool=""), "voice"),
    (_rec(event="hook_error", source="codec-hooks", tool=""), "error"),
    (_rec(event="tool_vetoed", source="codec-hooks", tool="terminal"), "security"),
    (_rec(event="skill_load_blocked", source="codec-skill-registry", tool=""), "security"),
    (_rec(event="trigger_fired", source="codec-triggers", tool=""), "scheduled"),
    (_rec(event="tool_result", source="codec-mcp", tool="tts_say"), "tts"),
    (_rec(event="ask_user_question_emit", source="codec-ask-user", tool="", agent="Writer"), "system"),
])
def test_categorize_event(record, expected_cat):
    assert codec_audit._categorize_event(record) == expected_cat


def test_level_for_prefers_explicit_level():
    assert codec_audit._level_for(_rec(level="warning", outcome="ok")) == "warning"


def test_level_for_derives_from_outcome_error():
    rec = _rec(outcome="error")
    rec.pop("level", None)
    assert codec_audit._level_for(rec) == "error"


def test_level_for_defaults_to_info():
    rec = _rec(outcome="ok")
    rec.pop("level", None)
    assert codec_audit._level_for(rec) == "info"
