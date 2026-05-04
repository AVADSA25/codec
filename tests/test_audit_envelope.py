"""Tests for the unified audit envelope (schema:1).

Validates:
  - audit() writes the required core fields (ts, schema, event, source, outcome).
  - audit() rejects calls without `event=` (Q4: required, no default).
  - log_event() routes through audit() with correct envelope.
  - _PREVIEW_MAX truncation via _truncate.
  - Privacy: chat_command stores task_preview only (never full task).
  - correlation_id lands under extra.correlation_id per §1.4.
  - Reserved top-level fields can't be overridden via extra={}.

Tests redirect codec_audit._AUDIT_LOG to a temp file so the real
~/.codec/audit.log is never touched.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import codec_audit


@pytest.fixture
def temp_audit_log(monkeypatch):
    """Redirect codec_audit's writer to a temp file. Yields the path."""
    tmp = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
    tmp.close()
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", Path(tmp.name))
    yield Path(tmp.name)
    try:
        os.unlink(tmp.name)
    except OSError:
        pass


def _last_record(path: Path) -> dict:
    """Read the last JSON line from the audit log."""
    text = path.read_text(encoding="utf-8")
    lines = [l for l in text.splitlines() if l.strip()]
    assert lines, "audit log is empty"
    return json.loads(lines[-1])


def _all_records(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


# ── Required-field guarantees ────────────────────────────────────────────────

def test_envelope_has_required_fields(temp_audit_log):
    codec_audit.audit("weather", event="tool_result", source="codec-mcp-http",
                      outcome="ok", duration_ms=42.0)
    rec = _last_record(temp_audit_log)
    for k in ("ts", "schema", "event", "source", "outcome"):
        assert k in rec, f"missing required field: {k}"
    assert rec["schema"] == 1
    assert rec["event"] == "tool_result"
    assert rec["source"] == "codec-mcp-http"
    assert rec["outcome"] == "ok"
    assert rec["tool"] == "weather"


def test_event_kwarg_is_required(temp_audit_log):
    """Q4 (rejected): no default for event=. Calling without it raises TypeError."""
    with pytest.raises(TypeError):
        codec_audit.audit("weather", outcome="ok")  # type: ignore[call-arg]


def test_default_source_falls_back_to_env(temp_audit_log, monkeypatch):
    monkeypatch.setenv("CODEC_PROCESS", "codec-test")
    codec_audit.audit("x", event="tool_result")
    rec = _last_record(temp_audit_log)
    assert rec["source"] == "codec-test"


def test_default_source_when_env_missing(temp_audit_log, monkeypatch):
    monkeypatch.delenv("CODEC_PROCESS", raising=False)
    codec_audit.audit("x", event="tool_result")
    rec = _last_record(temp_audit_log)
    assert rec["source"] == "codec"


# ── log_event adapter ────────────────────────────────────────────────────────

def test_log_event_writes_envelope(temp_audit_log):
    codec_audit.log_event("heartbeat_tick", "codec-heartbeat",
                          "tick complete", extra={"tasks_run": 5})
    rec = _last_record(temp_audit_log)
    assert rec["event"] == "heartbeat_tick"
    assert rec["source"] == "codec-heartbeat"
    assert rec["transport"] == "heartbeat"
    assert rec["outcome"] == "ok"
    assert rec["level"] == "info"
    assert rec["message"] == "tick complete"
    assert rec["extra"]["tasks_run"] == 5


def test_log_event_error_level_sets_outcome(temp_audit_log):
    codec_audit.log_event("service_down", "codec-heartbeat",
                          "Service down: kokoro", level="error")
    rec = _last_record(temp_audit_log)
    assert rec["outcome"] == "error"
    assert rec["level"] == "error"


def test_log_event_explicit_outcome_overrides_default(temp_audit_log):
    """Caller passes outcome='denied' on a level='warning' emit."""
    codec_audit.log_event("auth_reject", "codec-auth", "Wrong PIN",
                          outcome="denied", level="warning")
    rec = _last_record(temp_audit_log)
    assert rec["outcome"] == "denied"
    assert rec["level"] == "warning"


def test_transport_lookup_table(temp_audit_log):
    """Each known source resolves to its transport without an explicit override."""
    cases = [
        ("codec-heartbeat", "heartbeat"),
        ("codec-scheduler", "scheduler"),
        ("codec-dispatch", "dispatch"),
        ("codec-session", "session"),
        ("codec-dashboard", "chat"),
        ("codec-mcp-http", "http"),
        ("codec-mcp", "stdio"),
        ("codec-voice", "voice"),
        ("codec-unknown", "local"),
    ]
    for source, expected in cases:
        codec_audit.log_event("evt", source, "msg")
        rec = _last_record(temp_audit_log)
        assert rec["transport"] == expected, (source, expected, rec["transport"])


# ── Truncation + privacy ─────────────────────────────────────────────────────

def test_message_truncates_at_500(temp_audit_log):
    codec_audit.log_event("test", "src", "x" * 1000)
    rec = _last_record(temp_audit_log)
    assert len(rec["message"]) == 500


def test_error_truncates_at_500(temp_audit_log):
    codec_audit.log_event("test", "src", "msg", error="y" * 1000, level="error")
    rec = _last_record(temp_audit_log)
    assert len(rec["error"]) == 500


def test_truncate_helper():
    assert codec_audit._truncate("abcdef", 4) == "abcd"
    assert codec_audit._truncate("ab", 10) == "ab"
    assert codec_audit._truncate("", 10) == ""
    assert codec_audit._truncate(None, 10) == ""
    assert codec_audit._truncate(123, 10) == "123"


def test_preview_max_constant():
    assert codec_audit._PREVIEW_MAX == 200


def test_chat_command_strips_full_task(temp_audit_log):
    """Privacy: chat_command must NOT store the full task body — preview only."""
    codec_audit.log_event("chat_command", "codec-dashboard",
                          "Command from voice",
                          extra={"source": "voice", "task_preview": "x" * 200})
    rec = _last_record(temp_audit_log)
    assert "task" not in rec.get("extra", {})
    assert "task_preview" in rec["extra"]
    assert len(rec["extra"]["task_preview"]) <= 200


# ── correlation_id ───────────────────────────────────────────────────────────

def test_correlation_id_lands_under_extra(temp_audit_log):
    codec_audit.audit("weather", event="tool_call",
                      correlation_id="abc123def456")
    rec = _last_record(temp_audit_log)
    assert rec["extra"]["correlation_id"] == "abc123def456"


def test_correlation_id_omitted_when_none(temp_audit_log):
    codec_audit.audit("weather", event="tool_call")
    rec = _last_record(temp_audit_log)
    assert "extra" not in rec or "correlation_id" not in rec.get("extra", {})


def test_correlation_id_via_log_event(temp_audit_log):
    codec_audit.log_event("test", "src", "msg", correlation_id="deadbeef0001")
    rec = _last_record(temp_audit_log)
    assert rec["extra"]["correlation_id"] == "deadbeef0001"


# ── Reserved-field guard ─────────────────────────────────────────────────────

def test_extra_cannot_override_reserved_top_fields(temp_audit_log):
    """A caller passing extra={'event': 'evil'} must not stomp the real top-level event."""
    codec_audit.audit("weather", event="tool_result",
                      extra={"event": "evil", "source": "evil-src",
                             "schema": 99, "outcome": "evil"})
    rec = _last_record(temp_audit_log)
    assert rec["event"] == "tool_result"
    assert rec["schema"] == 1
    assert rec["outcome"] == "ok"
    # And nothing reserved leaked into extra either.
    assert "event" not in rec.get("extra", {})


# ── _cmd_hash ────────────────────────────────────────────────────────────────

def test_cmd_hash_stable_short_form():
    h = codec_audit._cmd_hash("rm -rf /")
    assert len(h) == 8
    # Stable across calls
    assert codec_audit._cmd_hash("rm -rf /") == h
    # Empty / None / non-str
    assert len(codec_audit._cmd_hash("")) == 8
    assert len(codec_audit._cmd_hash(None)) == 8
    assert len(codec_audit._cmd_hash(42)) == 8
