"""Tests for CODEC Daybreak — morning kickoff + working-threads live memory.

Design: docs/DAYBREAK-DESIGN.md. Threads are temporal facts
(key = "thread:{kind}:{slug}") in the existing facts table; the briefing
assembles from mockable seams and never raises; trigger phrases are
collision-checked against the live registry; the chat conversational-guard
behavior ("?" trap) is pinned.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skills"))

import codec_memory_upgrade  # noqa: E402
import codec_daybreak  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────
def _tmp_db(tmp_path, monkeypatch):
    db = str(tmp_path / "daybreak_test.db")
    monkeypatch.setattr(codec_memory_upgrade, "DB_PATH", db)
    return db


# ── threads: save / supersede / close round-trips (temp DB) ─────────────────
def test_save_thread_roundtrip(tmp_path, monkeypatch):
    _tmp_db(tmp_path, monkeypatch)
    codec_daybreak.save_thread("working_on", "ship Email-v2")
    threads = codec_daybreak.get_open_threads()
    assert len(threads) == 1
    t = threads[0]
    assert t["key"] == "thread:working_on:ship-email-v2"
    assert t["kind"] == "working_on"
    assert "ship Email-v2" in t["text"]


def test_same_thread_resave_supersedes(tmp_path, monkeypatch):
    _tmp_db(tmp_path, monkeypatch)
    codec_daybreak.save_thread("working_on", "ship Email-v2")
    codec_daybreak.save_thread("working_on", "ship Email-v2")
    key = "thread:working_on:ship-email-v2"
    history = codec_memory_upgrade.get_fact_history(key)
    assert len(history) == 2
    assert len(codec_memory_upgrade.query_valid_facts(key=key)) == 1
    assert len(codec_daybreak.get_open_threads()) == 1


def test_close_thread_expires(tmp_path, monkeypatch):
    _tmp_db(tmp_path, monkeypatch)
    codec_daybreak.save_thread("working_on", "ship Email-v2")
    out = codec_daybreak.close_thread("email-v2")
    assert "closed" in out.lower()
    assert codec_daybreak.get_open_threads() == []
    hist = codec_memory_upgrade.get_fact_history("thread:working_on:ship-email-v2")
    assert hist[0]["valid_until"] is not None
    assert hist[0]["superseded_by"] is None  # closed, not replaced


def test_close_thread_no_match_and_ambiguous(tmp_path, monkeypatch):
    _tmp_db(tmp_path, monkeypatch)
    out = codec_daybreak.close_thread("nonexistent")
    assert "no open thread" in out.lower()
    codec_daybreak.save_thread("working_on", "hue overlay polish")
    codec_daybreak.save_thread("follow_up", "hue PR review")
    out = codec_daybreak.close_thread("hue")
    assert len(codec_daybreak.get_open_threads()) == 2  # nothing expired
    assert "which" in out.lower() or "specific" in out.lower() or "match" in out.lower()


# ── working context block (prompt injection) ────────────────────────────────
def test_working_context_caps_and_priority_first(tmp_path, monkeypatch):
    _tmp_db(tmp_path, monkeypatch)
    for i in range(50):
        codec_daybreak.save_thread("working_on", f"task number {i} with some words")
    codec_daybreak.save_thread("priority", "THE BIG ONE")
    ctx = codec_daybreak.get_working_context()
    assert len(ctx) <= codec_daybreak.WORKING_CONTEXT_CHAR_CAP
    bullets = [ln for ln in ctx.splitlines() if ln.strip().startswith("-")]
    assert len(bullets) <= 7
    assert "priority" in bullets[0]  # priority kind sorts first
    assert "THE BIG ONE" in bullets[0]


def test_working_context_empty_and_killswitch(tmp_path, monkeypatch):
    _tmp_db(tmp_path, monkeypatch)
    assert codec_daybreak.get_working_context() == ""
    codec_daybreak.save_thread("working_on", "anything")
    monkeypatch.setenv("DAYBREAK_ENABLED", "false")
    assert codec_daybreak.get_working_context() == ""


# ── briefing assembly (all seams mocked) ────────────────────────────────────
def _mock_local_seams(monkeypatch, tmp_path):
    _tmp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(codec_daybreak, "_read_notifications", lambda: [])
    monkeypatch.setattr(codec_daybreak, "_yesterday_topics", lambda: ["hue lights fix"])
    monkeypatch.setattr(codec_daybreak, "_recent_audit_records", lambda hours=24: [])
    monkeypatch.setattr(codec_daybreak, "_blocking_agents", lambda: [])
    monkeypatch.setattr(codec_daybreak, "_pending_questions", lambda: [])


def test_briefing_all_sources_happy(tmp_path, monkeypatch):
    _mock_local_seams(monkeypatch, tmp_path)
    codec_daybreak.save_thread("priority", "record the demo video")

    def fake_source(skill, task):
        return {
            "google_calendar": "Today's schedule — 2 event(s):\n- 10:00 standup\n- 15:00 demo",
            "weather": "Weather in Marbella: 27°C, sunny.",
            "google_gmail": "Found 2 emails:\n* Bob: Invoice\n* Ana: Lunch",
            "reminders": "Open reminders:\n- buy cables",
            "notification_reader": "You have 3 unread notifications.",
        }[skill]

    monkeypatch.setattr(codec_daybreak, "_run_source", fake_source)
    emits = []
    import codec_audit
    monkeypatch.setattr(codec_audit, "log_event",
                        lambda *a, **k: emits.append((a, k)))

    out = codec_daybreak.assemble_briefing("good morning")
    assert "record the demo video" in out          # threads
    assert "standup" in out                        # calendar
    assert "Marbella" in out                       # weather
    assert "Bob" in out                            # email
    assert "buy cables" in out                     # reminders
    daybreak_emits = [e for e in emits if e[0][0] == "daybreak_completed"]
    assert len(daybreak_emits) == 1


def test_briefing_each_source_fails_gracefully(tmp_path, monkeypatch):
    _mock_local_seams(monkeypatch, tmp_path)

    def fail_source(skill, task):
        return {
            "google_calendar": "Calendar error: token expired",
            "weather": "Couldn't fetch weather right now.",
            "google_gmail": "Gmail error: 401",
            "reminders": None,  # reminders.py can return None
            "notification_reader": "Error reading notifications: down",
        }[skill]

    monkeypatch.setattr(codec_daybreak, "_run_source", fail_source)
    out = codec_daybreak.assemble_briefing()
    assert isinstance(out, str) and len(out) > 0   # never raises, still renders
    assert "Calendar error" not in out             # raw error strings filtered


def test_briefing_total_failure_still_greets(tmp_path, monkeypatch):
    _mock_local_seams(monkeypatch, tmp_path)
    codec_daybreak.save_thread("working_on", "the only data point")

    def boom(skill, task):
        raise RuntimeError("everything is down")

    monkeypatch.setattr(codec_daybreak, "_run_source", boom)
    out = codec_daybreak.assemble_briefing()
    assert "the only data point" in out            # threads always render


def test_briefing_time_budget(tmp_path, monkeypatch):
    _mock_local_seams(monkeypatch, tmp_path)

    def slow(skill, task):
        time.sleep(5)
        return "late"

    monkeypatch.setattr(codec_daybreak, "_run_source", slow)
    monkeypatch.setattr(codec_daybreak, "DEFAULT_TIME_BUDGET_S", 0.3)
    t0 = time.monotonic()
    out = codec_daybreak.assemble_briefing()
    assert time.monotonic() - t0 < 2.0             # reaped, not waited out
    assert isinstance(out, str)


def test_briefing_killswitch(tmp_path, monkeypatch):
    _tmp_db(tmp_path, monkeypatch)
    monkeypatch.setenv("DAYBREAK_ENABLED", "0")
    assert "disabled" in codec_daybreak.assemble_briefing().lower()


# ── triggers: real registry, collisions pinned ──────────────────────────────
def _dispatch():
    """Registry mirror of production startup: every entry path calls
    load_skills() (scan) before check_skill."""
    import codec_dispatch
    codec_dispatch.load_skills()
    return codec_dispatch


def test_triggers_route_to_daily_kickoff():
    codec_dispatch = _dispatch()
    for phrase in ("good morning codec",
                   "where did we left off yesterday",
                   "start my day"):
        m = codec_dispatch.check_skill(phrase)
        assert m is not None and m["name"] == "daily_kickoff", phrase


def test_triggers_do_not_steal_existing_skills():
    codec_dispatch = _dispatch()
    m = codec_dispatch.check_skill("morning briefing please")
    assert m is None or m["name"] != "daily_kickoff"   # crew phrase stays clear
    m = codec_dispatch.check_skill("what do i have today")
    assert m is not None and m["name"] == "google_calendar"
    # no daily_kickoff trigger may contain "briefing"
    import daily_kickoff
    assert all("briefing" not in t for t in daily_kickoff.SKILL_TRIGGERS)


def test_thread_note_triggers_are_namespaced():
    import thread_note
    assert all("thread" in t for t in thread_note.SKILL_TRIGGERS)
    codec_dispatch = _dispatch()
    m = codec_dispatch.check_skill("note a thread im waiting on the IB broker")
    assert m is not None and m["name"] == "thread_note"


# ── chat conversational-guard pins (the "?" trap) ───────────────────────────
def test_conversational_guard_behavior():
    from codec_chat_pipeline import _is_conversational
    assert _is_conversational("good morning codec") is False
    assert _is_conversational("where did we left off yesterday") is False
    assert _is_conversational("where did we leave off?") is True  # documented trap


def test_chat_allowlist_membership():
    from routes.chat import CHAT_SKILL_ALLOWLIST
    assert "daily_kickoff" in CHAT_SKILL_ALLOWLIST
    assert "thread_note" in CHAT_SKILL_ALLOWLIST
