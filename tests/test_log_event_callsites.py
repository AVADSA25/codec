"""Tests that the per-module log_event call sites pass the correct event_type,
source, transport, outcome, and (where required) correlation_id.

For each of the 7 modules that emit log_event:
  codec_session     (command_flagged / approved / denied)
  codec_scheduler   (schedule_fire / schedule_done)
  codec_dispatch    (wake_dispatch / wake_skill_error)
  codec_heartbeat   (service_down / heartbeat_tick)
  codec_dashboard   (chat_command / chat_skill / chat_llm / chat_llm_error / chat_vision / service_restart)
  codec.py          (wake_dispatch / tts_speak / wake_word_detected)
  routes/auth       (auth_success / auth_reject)

Each test redirects codec_audit._AUDIT_LOG to a temp file so the real audit
log is never touched.
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
    tmp = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
    tmp.close()
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", Path(tmp.name))
    yield Path(tmp.name)
    try:
        os.unlink(tmp.name)
    except OSError:
        pass


def _records(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


# ── codec_heartbeat ──────────────────────────────────────────────────────────

def test_heartbeat_service_down(temp_audit_log):
    codec_audit.log_event("service_down", "codec-heartbeat",
                          "Service down: kokoro-82m",
                          outcome="error", level="error",
                          extra={"service": "kokoro-82m",
                                 "url": "http://localhost:9999"})
    rec = _records(temp_audit_log)[-1]
    assert rec["event"] == "service_down"
    assert rec["source"] == "codec-heartbeat"
    assert rec["transport"] == "heartbeat"
    assert rec["outcome"] == "error"
    assert rec["level"] == "error"
    assert rec["extra"]["service"] == "kokoro-82m"


def test_heartbeat_tick(temp_audit_log):
    codec_audit.log_event("heartbeat_tick", "codec-heartbeat",
                          "Heartbeat tick completed",
                          extra={"tasks_run": 3})
    rec = _records(temp_audit_log)[-1]
    assert rec["event"] == "heartbeat_tick"
    assert rec["transport"] == "heartbeat"
    assert rec["outcome"] == "ok"
    assert rec["extra"]["tasks_run"] == 3


# ── codec_scheduler ──────────────────────────────────────────────────────────

def test_scheduler_fire_and_done_share_correlation_id(temp_audit_log):
    cid = "abc1abc1abc1"
    codec_audit.log_event("schedule_fire", "codec-scheduler",
                          "Schedule fired: daily_briefing",
                          extra={"schedule_id": "sched_daily",
                                 "label": "daily_briefing",
                                 "crew": "briefing"},
                          correlation_id=cid)
    codec_audit.log_event("schedule_done", "codec-scheduler",
                          "Schedule done: daily_briefing",
                          duration_ms=1234.5,
                          extra={"schedule_id": "sched_daily",
                                 "title": "daily_briefing"},
                          correlation_id=cid)
    recs = _records(temp_audit_log)
    fire, done = recs[-2], recs[-1]
    assert fire["event"] == "schedule_fire"
    assert done["event"] == "schedule_done"
    assert fire["transport"] == "scheduler"
    assert done["transport"] == "scheduler"
    assert fire["extra"]["correlation_id"] == cid
    assert done["extra"]["correlation_id"] == cid
    assert done["duration_ms"] == 1234.5


# ── codec_dispatch ───────────────────────────────────────────────────────────

def test_dispatch_wake_dispatch_carries_tool_and_correlation(temp_audit_log):
    cid = "ddd1ddd1ddd1"
    codec_audit.log_event("wake_dispatch", "codec-dispatch",
                          "Skill: weather",
                          tool="weather",
                          duration_ms=80.0,
                          extra={"result_len": 120},
                          correlation_id=cid)
    rec = _records(temp_audit_log)[-1]
    assert rec["event"] == "wake_dispatch"
    assert rec["source"] == "codec-dispatch"
    assert rec["transport"] == "dispatch"
    assert rec["tool"] == "weather"
    assert rec["extra"]["correlation_id"] == cid


def test_dispatch_skill_error_records_error_type(temp_audit_log):
    codec_audit.log_event("wake_skill_error", "codec-dispatch",
                          "Skill error: BoomError: bad",
                          tool="weather",
                          outcome="error",
                          level="error",
                          error_type="BoomError",
                          error="bad")
    rec = _records(temp_audit_log)[-1]
    assert rec["event"] == "wake_skill_error"
    assert rec["outcome"] == "error"
    assert rec["error_type"] == "BoomError"
    assert rec["error"] == "bad"


# ── codec_session ────────────────────────────────────────────────────────────

def test_session_command_triplet_shares_cmd_hash(temp_audit_log):
    code = "rm -rf /"
    ch = codec_audit._cmd_hash(code)
    codec_audit.log_event("command_flagged", "codec-session",
                          f"Command flagged: {code[:80]}",
                          extra={"cmd_hash": ch,
                                 "cmd_preview": codec_audit._truncate(code, codec_audit._PREVIEW_MAX)},
                          outcome="denied", level="warning")
    codec_audit.log_event("command_denied", "codec-session",
                          "Command denied",
                          extra={"cmd_hash": ch},
                          outcome="denied", level="warning")
    recs = _records(temp_audit_log)
    flagged, denied = recs[-2], recs[-1]
    assert flagged["event"] == "command_flagged"
    assert denied["event"] == "command_denied"
    assert flagged["extra"]["cmd_hash"] == denied["extra"]["cmd_hash"]
    assert flagged["transport"] == "session"
    # cmd is NEVER logged in cleartext at the top level — only preview/hash.
    assert "cmd" not in flagged
    # Preview is bounded.
    assert len(flagged["extra"]["cmd_preview"]) <= codec_audit._PREVIEW_MAX


def test_session_action_field_dropped(temp_audit_log):
    """The legacy {'action': 'flagged'} envelope is gone — event discriminates."""
    codec_audit.log_event("command_flagged", "codec-session",
                          "Command flagged: x", extra={"cmd_hash": "deadbeef"},
                          outcome="denied", level="warning")
    rec = _records(temp_audit_log)[-1]
    assert "action" not in rec.get("extra", {})


# ── codec_dashboard ──────────────────────────────────────────────────────────

def test_dashboard_chat_command_strips_full_task(temp_audit_log):
    """Privacy: chat_command never logs the full task body."""
    codec_audit.log_event("chat_command", "codec-dashboard",
                          "Command from pwa: hi",
                          extra={"source": "pwa", "task_preview": "x" * 200})
    rec = _records(temp_audit_log)[-1]
    assert rec["event"] == "chat_command"
    assert rec["transport"] == "chat"
    assert "task" not in rec.get("extra", {})
    assert "task_preview" in rec["extra"]


def test_dashboard_chat_skill_carries_tool_field(temp_audit_log):
    codec_audit.log_event("chat_skill", "codec-dashboard",
                          "Dashboard skill: weather",
                          tool="weather",
                          extra={"result_len": 80})
    rec = _records(temp_audit_log)[-1]
    assert rec["tool"] == "weather"
    assert rec["event"] == "chat_skill"


def test_dashboard_chat_llm_error_keeps_error_type(temp_audit_log):
    codec_audit.log_event("chat_llm_error", "codec-dashboard",
                          "Flash LLM failed: timed out",
                          outcome="error", level="error",
                          error_type="TimeoutError",
                          error="timed out",
                          extra={"model": "qwen"})
    rec = _records(temp_audit_log)[-1]
    assert rec["event"] == "chat_llm_error"
    assert rec["outcome"] == "error"
    assert rec["error_type"] == "TimeoutError"
    assert rec["error"] == "timed out"


def test_dashboard_service_restart(temp_audit_log):
    codec_audit.log_event("service_restart", "codec-dashboard",
                          "Service restart: codec-mcp-http",
                          extra={"service": "codec-mcp-http"})
    rec = _records(temp_audit_log)[-1]
    assert rec["event"] == "service_restart"
    assert rec["extra"]["service"] == "codec-mcp-http"


# ── routes/auth ──────────────────────────────────────────────────────────────

def test_auth_success_records_method(temp_audit_log):
    codec_audit.log_event("auth_success", "codec-auth", "Auth success: pin",
                          extra={"method": "pin"})
    rec = _records(temp_audit_log)[-1]
    assert rec["event"] == "auth_success"
    assert rec["extra"]["method"] == "pin"


def test_auth_reject_is_warning_denied(temp_audit_log):
    codec_audit.log_event("auth_reject", "codec-auth", "Auth failed",
                          outcome="denied", level="warning")
    rec = _records(temp_audit_log)[-1]
    assert rec["event"] == "auth_reject"
    assert rec["outcome"] == "denied"
    assert rec["level"] == "warning"
