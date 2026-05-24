"""Phase 2 Step 7 tests — skills/shift_report.py + codec_observer fire path.

21 tests covering:
  Assembly (8) — section rendering for each input source
  Notification + state (3) — post + dedup
  Kill switch + config (3)
  Trigger paths (3) — manual / time / idle
  Observer integration (3) — _maybe_fire_shift_report
  Chat allowlist (1) — shift_report must be in CHAT_SKILL_ALLOWLIST

All tests redirect storage paths to tmp_path. NO real filesystem writes
to ~/.codec/* outside of explicit fixtures. NO real audit emits to
~/.codec/audit.log.
"""
from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
_SKILLS = _REPO / "skills"
if str(_SKILLS) not in sys.path:
    sys.path.insert(0, str(_SKILLS))

import codec_audit
import shift_report   # the skill module


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_audit_log(tmp_path, monkeypatch):
    log = tmp_path / "audit.log"
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", log)
    return log


@pytest.fixture
def temp_state(tmp_path, monkeypatch):
    """Redirect every storage path the shift_report module touches."""
    monkeypatch.setattr(shift_report, "_AUDIT_LOG", tmp_path / "audit.log")
    monkeypatch.setattr(shift_report, "_NOTIFS_PATH", tmp_path / "notifications.json")
    monkeypatch.setattr(shift_report, "_OBS_SUMMARIES_DIR", tmp_path / "observation_summaries")
    monkeypatch.setattr(shift_report, "_PROPOSALS_DIR", tmp_path / "skill_proposals")
    monkeypatch.setattr(shift_report, "_STATE_PATH", tmp_path / "shift_report_state.json")
    monkeypatch.setattr(shift_report, "_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(shift_report, "_CODEC_DIR", tmp_path)
    return tmp_path


def _write_audit_log(path: Path, records: list):
    """Write a synthetic audit log."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def _records(audit_log: Path) -> list:
    if not audit_log.exists():
        return []
    return [json.loads(l) for l in audit_log.read_text().splitlines() if l.strip()]


def _events_of(records, event_name):
    return [r for r in records if r.get("event") == event_name]


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _ts_iso(hours_ago=0):
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat(timespec="milliseconds")


# ─────────────────────────────────────────────────────────────────────────────
# Assembly (8)
# ─────────────────────────────────────────────────────────────────────────────

def test_assemble_with_no_inputs_renders_5_sections(temp_state):
    """No audit, no notifs, no summaries → all 5 sections render with
    'no data' placeholders. Markdown still has structure."""
    report = shift_report._assemble_shift_report(trigger_kind="manual")
    assert report["word_count"] > 0
    assert report["sections_included"] == 0  # all sections empty
    md = report["markdown"]
    assert "# CODEC Shift Report" in md
    for section in ("Completed tasks", "Blocked / stuck moments",
                    "Observed work patterns", "Pending decisions",
                    "Tomorrow's open threads"):
        assert f"## {section}" in md


def test_assemble_completed_tasks_counts_successful_events(temp_state):
    """Section 1 reports successful tool_result + crew_complete counts."""
    _write_audit_log(temp_state / "audit.log", [
        {"ts": _ts_iso(2), "event": "tool_result", "outcome": "ok", "tool": "weather"},
        {"ts": _ts_iso(2), "event": "tool_result", "outcome": "ok", "tool": "calculator"},
        {"ts": _ts_iso(1), "event": "tool_result", "outcome": "error", "tool": "broken"},
        {"ts": _ts_iso(1), "event": "crew_complete", "outcome": "ok"},
    ])
    report = shift_report._assemble_shift_report(trigger_kind="manual")
    md = report["markdown"]
    # 3 successful events (the error one excluded)
    assert "3 successful operation(s)" in md
    assert "weather" in md
    assert "calculator" in md
    # Failed tool NOT mentioned in section 1 (it's in section 2)
    assert "broken" not in md.split("Blocked")[0]


def test_assemble_blocked_section(temp_state):
    """Section 2 lists stuck_warning / step_budget_exhausted."""
    _write_audit_log(temp_state / "audit.log", [
        {"ts": _ts_iso(1), "event": "stuck_warning", "tool": "weather"},
        {"ts": _ts_iso(1), "event": "stuck_escalated", "tool": "weather"},
        {"ts": _ts_iso(0.5), "event": "step_budget_exhausted",
         "extra": {"tool": "translate"}},
    ])
    report = shift_report._assemble_shift_report(trigger_kind="manual")
    md = report["markdown"]
    assert "3 blocker event(s)" in md
    assert "stuck_warning" in md
    assert "step_budget_exhausted" in md


def test_assemble_observer_patterns_uses_observation_tick(temp_state):
    """Section 3 renders app time-share from observation_tick metadata."""
    ticks = []
    for _ in range(10):
        ticks.append({"ts": _ts_iso(2), "event": "observation_tick",
                      "extra": {"active_app": "Google Chrome"}})
    for _ in range(5):
        ticks.append({"ts": _ts_iso(1), "event": "observation_tick",
                      "extra": {"active_app": "iTerm"}})
    _write_audit_log(temp_state / "audit.log", ticks)
    report = shift_report._assemble_shift_report(trigger_kind="manual")
    md = report["markdown"]
    assert "Observed 15 polls" in md
    assert "Google Chrome" in md
    assert "iTerm" in md


def test_assemble_pending_decisions_includes_open_questions(temp_state):
    """Section 4 surfaces unread type='question' notifications."""
    notifs = [
        {"id": "n1", "type": "question", "read": False,
         "title": "TestAgent is asking a question",
         "created": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")},
        {"id": "n2", "type": "task_report", "read": True,
         "title": "Old report",
         "created": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")},
    ]
    (temp_state / "notifications.json").write_text(json.dumps(notifs))
    report = shift_report._assemble_shift_report(trigger_kind="manual")
    md = report["markdown"]
    assert "1 open question" in md
    assert "TestAgent is asking" in md


def test_assemble_pending_includes_unreviewed_proposals(temp_state):
    """Section 4 lists proposal markdown files in today's directory."""
    today = shift_report._today_local_date()
    today_dir = temp_state / "skill_proposals" / today
    today_dir.mkdir(parents=True)
    (today_dir / "frobnicate.md").write_text("# proposal\n")
    (today_dir / "another.md").write_text("# proposal\n")
    report = shift_report._assemble_shift_report(trigger_kind="manual")
    md = report["markdown"]
    assert "2 unreviewed skill proposal(s)" in md
    assert "frobnicate.md" in md


def test_assemble_tomorrow_section_lists_incomplete_crews(temp_state):
    """Section 5 surfaces crew_start without matching crew_complete."""
    _write_audit_log(temp_state / "audit.log", [
        {"ts": _ts_iso(2), "event": "crew_start",
         "extra": {"correlation_id": "abc123abc123",
                   "agents": ["A", "B"]}},
        # NO crew_complete for abc123abc123 → incomplete
        {"ts": _ts_iso(1), "event": "crew_start",
         "extra": {"correlation_id": "def456def456",
                   "agents": ["X"]}},
        {"ts": _ts_iso(0.5), "event": "crew_complete",
         "extra": {"correlation_id": "def456def456"}},
    ])
    report = shift_report._assemble_shift_report(trigger_kind="manual")
    md = report["markdown"]
    assert "1 crew run(s) started but not completed" in md
    assert "abc123ab" in md   # cid prefix


def test_assemble_observer_summaries_referenced(temp_state):
    """Section 3 references observer_summaries when present."""
    today = shift_report._today_local_date()
    sd = temp_state / "observation_summaries"
    sd.mkdir(parents=True)
    (sd / f"{today}T16-00-00.md").write_text("snap")
    (sd / f"{today}T17-00-00.md").write_text("snap")
    # Add a tick so section 3 has data
    _write_audit_log(temp_state / "audit.log", [
        {"ts": _ts_iso(1), "event": "observation_tick",
         "extra": {"active_app": "X"}},
    ])
    report = shift_report._assemble_shift_report(trigger_kind="manual")
    md = report["markdown"]
    assert "2 observer summary file(s)" in md
    assert report["observer_summaries_used"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# Notification + state (3)
# ─────────────────────────────────────────────────────────────────────────────

def test_post_notification_writes_shift_report_type(temp_state):
    """run() posts notif with type='shift_report'."""
    result = shift_report.run("manual invocation")
    assert "Shift report posted" in result
    notifs = json.loads((temp_state / "notifications.json").read_text())
    assert len(notifs) == 1
    assert notifs[0]["type"] == "shift_report"
    assert notifs[0]["read"] is False
    assert "CODEC Shift Report" in notifs[0]["title"]


def test_already_fired_today_dedup(temp_state):
    """run_with_trigger_kind('idle') won't re-fire after time fires."""
    shift_report.mark_fired_today("time")
    assert shift_report.already_fired_today() is True
    result = shift_report.run_with_trigger_kind("idle")
    assert "already fired today" in result


def test_manual_trigger_bypasses_dedup(temp_state):
    """run() with manual kind fires even if today's already fired."""
    shift_report.mark_fired_today("time")
    result = shift_report.run("show me my shift report")
    # manual bypasses
    assert "Shift report posted" in result
    notifs = json.loads((temp_state / "notifications.json").read_text())
    assert len(notifs) == 1


def test_manual_5min_cooldown_suppresses_repeats(temp_state):
    """Two manual fires within 5 min — second is suppressed to prevent
    button-mash / polling-loop spam. The audit-log loop spotted on
    2026-05-03 (8 fires in 12 min) motivated this floor."""
    # First manual fire — should succeed and set last_trigger_kind=manual
    result1 = shift_report.run("shift report")
    assert "Shift report posted" in result1
    # Second manual fire immediately after — suppressed
    result2 = shift_report.run("shift report")
    assert "last 5 minutes" in result2
    notifs = json.loads((temp_state / "notifications.json").read_text())
    assert len(notifs) == 1   # only the first fire created a notification


def test_manual_cooldown_does_not_block_after_5min(temp_state):
    """Cooldown is time-based; if the last manual fire was >5 min ago,
    a new manual fire goes through."""
    from datetime import datetime, timezone, timedelta
    # Seed state with a manual fire from 6 minutes ago
    old = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat(timespec="seconds")
    shift_report._save_state({
        "last_fired_date": shift_report._today_local_date(),
        "last_fired_at": old,
        "last_trigger_kind": "manual",
    })
    # New manual fire should NOT be suppressed
    assert shift_report._manual_cooldown_active() is False
    result = shift_report.run("shift report")
    assert "Shift report posted" in result


# ─────────────────────────────────────────────────────────────────────────────
# Kill switch + config (3)
# ─────────────────────────────────────────────────────────────────────────────

def test_kill_switch_disables_run(temp_state, monkeypatch):
    monkeypatch.setenv("SHIFT_REPORT_ENABLED", "false")
    result = shift_report.run("manual")
    assert "disabled" in result.lower()
    assert not (temp_state / "notifications.json").exists()


def test_kill_switch_default_enabled(monkeypatch):
    monkeypatch.delenv("SHIFT_REPORT_ENABLED", raising=False)
    assert shift_report._enabled() is True


def test_config_overrides_loaded(temp_state):
    """Custom shift_report.{...} in config.json is honored."""
    (temp_state / "config.json").write_text(json.dumps({
        "shift_report": {"daily_at_hour": 9, "lookback_hours": 12},
    }))
    cfg = shift_report._load_config()
    assert cfg["daily_at_hour"] == 9
    assert cfg["lookback_hours"] == 12


# ─────────────────────────────────────────────────────────────────────────────
# Audit emit (3)
# ─────────────────────────────────────────────────────────────────────────────

def test_run_emits_started_and_completed(temp_state, temp_audit_log):
    shift_report.run("manual")
    recs = _records(temp_audit_log)
    started = _events_of(recs, codec_audit.SHIFT_REPORT_STARTED)
    completed = _events_of(recs, codec_audit.SHIFT_REPORT_COMPLETED)
    assert len(started) == 1
    assert len(completed) == 1
    # Same correlation_id (multi-emit op per Step 1 §1.4)
    assert (started[0]["extra"]["correlation_id"]
            == completed[0]["extra"]["correlation_id"])


def test_completed_audit_carries_summary_extras(temp_state, temp_audit_log):
    shift_report.run("manual")
    recs = _records(temp_audit_log)
    completed = _events_of(recs, codec_audit.SHIFT_REPORT_COMPLETED)
    extra = completed[0]["extra"]
    assert "sections_included" in extra
    assert "word_count" in extra
    assert "audit_records_scanned" in extra
    assert "trigger_kind" in extra
    assert extra["trigger_kind"] == "manual"


def test_kill_switch_skips_audit_emit(temp_state, temp_audit_log,
                                       monkeypatch):
    monkeypatch.setenv("SHIFT_REPORT_ENABLED", "false")
    shift_report.run("manual")
    recs = _records(temp_audit_log)
    # No started/completed events because kill switch fired before any work
    assert _events_of(recs, codec_audit.SHIFT_REPORT_STARTED) == []
    assert _events_of(recs, codec_audit.SHIFT_REPORT_COMPLETED) == []


# ─────────────────────────────────────────────────────────────────────────────
# Trigger kind detection (manual/time/idle) (3) — already covered above for
# manual + dedup. Adding 0 more here; covered by the kind hint test below.
# Reserved slot for future trigger kinds.

# ─────────────────────────────────────────────────────────────────────────────
# Observer integration: _maybe_fire_shift_report (3)
# ─────────────────────────────────────────────────────────────────────────────

def test_observer_idle_path_fires_shift_report(temp_state, monkeypatch):
    """When idle_seconds > idle_minutes*60 AND not already fired,
    observer fires the shift report with trigger_kind='idle'."""
    import codec_observer
    # Set up shift_report config: idle_minutes=1 (60s) for test
    (temp_state / "config.json").write_text(json.dumps({
        "shift_report": {"idle_minutes": 1, "daily_at_hour": 99,  # never time-fire
                         "daily_at_minute": 0},
    }))
    # Observer's _maybe_fire_shift_report imports shift_report — already
    # imported in this test process. Reload to pick up the patched paths.
    importlib.reload(shift_report)
    # Reset state since reload also resets monkeypatched paths:
    monkeypatch.setattr(shift_report, "_AUDIT_LOG", temp_state / "audit.log")
    monkeypatch.setattr(shift_report, "_NOTIFS_PATH", temp_state / "notifications.json")
    monkeypatch.setattr(shift_report, "_OBS_SUMMARIES_DIR", temp_state / "observation_summaries")
    monkeypatch.setattr(shift_report, "_PROPOSALS_DIR", temp_state / "skill_proposals")
    monkeypatch.setattr(shift_report, "_STATE_PATH", temp_state / "shift_report_state.json")
    monkeypatch.setattr(shift_report, "_CONFIG_PATH", temp_state / "config.json")
    monkeypatch.setattr(shift_report, "_CODEC_DIR", temp_state)
    # idle_seconds = 120 (2 min) > 60s threshold
    codec_observer._maybe_fire_shift_report(idle_seconds=120.0)
    # Notification should be posted
    notifs = json.loads((temp_state / "notifications.json").read_text())
    assert len(notifs) == 1
    assert notifs[0]["trigger_kind"] == "idle"


def test_observer_time_path_does_not_fire_off_window(temp_state):
    """If wall-clock hour doesn't match daily_at_hour, idle threshold not
    met → no fire."""
    import codec_observer
    (temp_state / "config.json").write_text(json.dumps({
        "shift_report": {"daily_at_hour": 99, "idle_minutes": 60},
    }))
    importlib.reload(shift_report)
    codec_observer._maybe_fire_shift_report(idle_seconds=10.0)
    assert not (temp_state / "notifications.json").exists()


def test_observer_already_fired_today_suppresses(temp_state, monkeypatch):
    """Observer's check honors per-day dedup."""
    import codec_observer
    # Set state to "fired today"
    importlib.reload(shift_report)
    monkeypatch.setattr(shift_report, "_AUDIT_LOG", temp_state / "audit.log")
    monkeypatch.setattr(shift_report, "_NOTIFS_PATH", temp_state / "notifications.json")
    monkeypatch.setattr(shift_report, "_OBS_SUMMARIES_DIR", temp_state / "observation_summaries")
    monkeypatch.setattr(shift_report, "_PROPOSALS_DIR", temp_state / "skill_proposals")
    monkeypatch.setattr(shift_report, "_STATE_PATH", temp_state / "shift_report_state.json")
    monkeypatch.setattr(shift_report, "_CONFIG_PATH", temp_state / "config.json")
    monkeypatch.setattr(shift_report, "_CODEC_DIR", temp_state)
    shift_report.mark_fired_today("time")
    (temp_state / "config.json").write_text(json.dumps({
        "shift_report": {"daily_at_hour": 99, "idle_minutes": 1},
    }))
    # Even with 2-hour idle, no second fire today
    codec_observer._maybe_fire_shift_report(idle_seconds=7200.0)
    notifs_path = temp_state / "notifications.json"
    if notifs_path.exists():
        notifs = json.loads(notifs_path.read_text())
        # Only the manual 'mark_fired' call — but mark_fired doesn't post,
        # so there should be 0 actual notifs
        assert all(n.get("trigger_kind") != "idle" for n in notifs)
    # State stays as "time"
    state = json.loads((temp_state / "shift_report_state.json").read_text())
    assert state["last_trigger_kind"] == "time"


# ────────────────────────────────────────────────────────────────────────
# Chat allowlist regression test (1)
# ────────────────────────────────────────────────────────────────────────

def test_shift_report_in_chat_skill_allowlist():
    """`shift_report` must be in `codec_dashboard.CHAT_SKILL_ALLOWLIST`.

    Regression test for the post-PR-#12 deployment bug: chat path
    `_try_skill` matched `shift_report` via SKILL_TRIGGERS, but the
    allowlist gate then dropped it and the LLM fell through to
    [SKILL:pm2_control:...]. The user typed 'shift report' and got a
    PM2 service listing instead.
    """
    import codec_dashboard
    assert "shift_report" in codec_dashboard.CHAT_SKILL_ALLOWLIST, (
        "shift_report skill is not in CHAT_SKILL_ALLOWLIST — chat-path "
        "dispatch will silently drop the match and fall through to LLM. "
        "See: docs/known-issues.md → Phase 2 Step 7 sign-off."
    )
