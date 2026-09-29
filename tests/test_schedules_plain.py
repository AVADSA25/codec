"""Schedule any skill, crew or prompt in plain language (UI phase 3, P3.3;
docs/P3.3-DESIGN.md).

Timings are read from plain language and described back; a daily job catches up
a run missed across midnight for 12 hours; one runner serves timed and manual
runs, records the full output and delivers it (notification, Today card, Google
Doc, spoken). Nothing here touches ~/.codec: every path is a tmp file and the
model, skills, crews and deliveries are fakes.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
TUE_10 = datetime(2026, 9, 29, 10, 0)  # a Tuesday


class _Now:
    """Runs a thread's target at once."""
    def __init__(self, target, args=(), **kw):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


@pytest.fixture
def sch(tmp_path, monkeypatch):
    import codec_scheduler as cs
    import codec_today
    monkeypatch.setattr(cs, "SCHEDULE_PATH", str(tmp_path / "schedules.json"))
    monkeypatch.setattr(cs, "RUNS_LOG", str(tmp_path / "schedule_runs.log"))
    monkeypatch.setattr(cs, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(cs, "VOICE_SESSION_PATH", str(tmp_path / "voice_session.json"))
    monkeypatch.setattr(codec_today, "TODAY_PATH", tmp_path / "today.json")
    ns = SimpleNamespace(cs=cs, today=codec_today, notified=[], spoken=[], llm=[], answers=[])
    monkeypatch.setattr(cs, "_notify", lambda title, body, status="success", schedule_id=None:
                        ns.notified.append((title, body, status)))
    monkeypatch.setattr(cs, "_speak_on_mac", lambda text: ns.spoken.append(text))
    monkeypatch.setattr(cs, "threading", SimpleNamespace(Thread=_Now))

    import codec_llm

    def fake_call(messages, **kw):
        ns.llm.append(messages)
        return ns.answers.pop(0) if ns.answers else "An answer."

    monkeypatch.setattr(codec_llm, "call", fake_call)
    return ns


def _job(**kw):
    base = {"id": "sched_t", "enabled": True, "created": "2026-09-01T00:00:00", "kind": "prompt", "prompt": "Summarize my day"}
    base.update(kw)
    return base


@pytest.mark.parametrize("text,when,reading", [
    ("weekdays at 7:30", {"type": "daily", "hour": 7, "minute": 30, "days": [0, 1, 2, 3, 4]}, "Weekdays at 07:30"),
    ("every 2h", {"type": "every", "minutes": 120}, "Every 2 hours"),
    ("mondays and thursdays at 9am", {"type": "daily", "hour": 9, "minute": 0, "days": [0, 3]}, "Mon and Thu at 09:00"),
    ("at 18:00", {"type": "daily", "hour": 18, "minute": 0, "days": [0, 1, 2, 3, 4, 5, 6]}, "Every day at 18:00"),
    ("every 30 minutes", {"type": "every", "minutes": 30}, "Every 30 minutes"),
    ("weekends at noon", {"type": "daily", "hour": 12, "minute": 0, "days": [5, 6]}, "Weekends at 12:00"),
    ("daily at 6pm", {"type": "daily", "hour": 18, "minute": 0, "days": [0, 1, 2, 3, 4, 5, 6]}, "Every day at 18:00"),
    ("Mon, Wed and Fri at 09:15", {"type": "daily", "hour": 9, "minute": 15, "days": [0, 2, 4]}, "Mon, Wed and Fri at 09:15"),
    ("hourly", {"type": "every", "minutes": 60}, "Every hour"),
    ("every sunday at 12am", {"type": "daily", "hour": 0, "minute": 0, "days": [6]}, "Sun at 00:00"),
])
def test_plain_language_is_read_and_described_back(text, when, reading):
    import codec_scheduler as cs
    assert cs.parse_when(text) == when
    assert cs.describe_when(when) == reading
    assert cs.parse_when(reading) == when, "the reading parses back to the same timing"


@pytest.mark.parametrize("text,hint", [
    ("", "Say when"), ("weekdays", "Add a time"), ("every 5 minutes", "between every 15 minutes"),
    ("tomorrow at 8", "Schedules repeat"), ("at 25:00", "not a time"), ("sometimes at 8", "could not read 'sometimes'"),
])
def test_unreadable_timings_explain_themselves(text, hint):
    import codec_scheduler as cs
    with pytest.raises(ValueError, match=hint):
        cs.parse_when(text)


def test_daily_jobs_catch_up_today_and_across_midnight_only():
    import codec_scheduler as cs
    eight = _job(when={"type": "daily", "hour": 8, "minute": 0, "days": list(range(7))})
    assert cs.due(eight, TUE_10) is True, "missed earlier today: runs (as before)"
    assert cs.due({**eight, "last_run": "2026-09-29T08:01:00"}, TUE_10) is False
    late = _job(when={"type": "daily", "hour": 23, "minute": 30, "days": list(range(7))}, last_run="2026-09-27T23:31:00")
    assert cs.due(late, datetime(2026, 9, 29, 1, 0)) is True, "missed at 23:30, caught up at 01:00"
    assert cs.due(late, datetime(2026, 9, 29, 12, 0)) is False, "more than 12 hours late: skipped"
    fresh = {**eight, "enabled_at": "2026-09-29T09:00:00"}
    assert cs.due(fresh, TUE_10) is False, "switched on after 08:00: no run for this morning"
    assert cs.due(fresh, datetime(2026, 9, 30, 8, 5)) is True
    week = _job(when={"type": "daily", "hour": 7, "minute": 30, "days": [0, 1, 2, 3, 4]}, last_run="2026-10-02T07:31:00")
    assert cs.due(week, datetime(2026, 10, 3, 9, 0)) is False, "Saturday"
    assert cs.due({**eight, "last_attempt": "2026-09-29T09:55:00"}, TUE_10) is False, "retried after 10 minutes"
    assert cs.due({**eight, "enabled": False}, TUE_10) is False


def test_interval_jobs_and_next_run():
    import codec_scheduler as cs
    every2 = _job(when={"type": "every", "minutes": 120})
    assert cs.due({**every2, "last_run": "2026-09-29T09:00:00"}, TUE_10) is False
    assert cs.due({**every2, "last_run": "2026-09-29T07:59:00"}, TUE_10) is True
    assert cs.due({**every2, "enabled_at": "2026-09-29T09:30:00"}, TUE_10) is False
    friday = datetime(2026, 10, 2, 8, 0)
    week = _job(when={"type": "daily", "hour": 7, "minute": 30, "days": [0, 1, 2, 3, 4]}, last_run="2026-10-02T07:31:00")
    assert cs.next_run(week, friday) == datetime(2026, 10, 5, 7, 30), "next Monday"
    assert cs.next_run({**week, "enabled": False}, friday) is None
    legacy = {"id": "old", "crew": "daily_briefing", "hour": 8, "minute": 0, "days": [0], "enabled": False}
    assert cs.job_view(legacy)["summary"] == "Mon at 08:00" and cs.job_view(legacy)["kind"] == "crew"


def test_each_kind_runs_its_own_path_and_continuity_feeds_the_last_result(sch, monkeypatch):
    cs = sch.cs
    sch.answers[:] = ["First answer.", "Second answer."]
    job = _job(continuity=True)
    assert cs.run_scheduled(job)["ok"] is True
    assert cs.run_scheduled(job)["ok"] is True
    assert "First answer." not in json.dumps(sch.llm[0]) and "First answer." in json.dumps(sch.llm[1])
    import codec_dispatch
    monkeypatch.setattr(cs, "schedulable_skill", lambda name: name == "weather")
    monkeypatch.setattr(codec_dispatch, "run_skill", lambda skill, task, app="": f"{skill['name']}:{task}")
    out = cs.run_scheduled(_job(id="s2", kind="skill", skill="weather", task="Marbella"))
    assert out["ok"] and cs.read_runs(sched_id="s2")[0]["output"] == "weather:Marbella"
    monkeypatch.setattr(cs, "_run_crew_output", lambda sched, context="": (True, f"crew {sched['crew']} {context!r}"))
    cs.run_scheduled(_job(id="s3", kind="crew", crew="deep_research"))
    assert cs.read_runs(sched_id="s3")[0]["output"] == "crew deep_research ''"


def test_destructive_and_consent_skills_never_run_on_a_schedule(sch, monkeypatch):
    cs = sch.cs
    import codec_dispatch
    monkeypatch.setattr(codec_dispatch.registry, "names", lambda: ["terminal", "clipboard", "weather", "ask_user"])
    monkeypatch.setattr(codec_dispatch.registry, "get_destructive", lambda name: False)
    assert cs.schedulable_skill("weather") is True
    assert not any(cs.schedulable_skill(n) for n in ("terminal", "clipboard", "ask_user", "not_a_skill"))
    with pytest.raises(ValueError, match="cannot run on a schedule"):
        cs.create_job({"kind": "skill", "skill": "terminal", "task": "ls", "when": "every day at 8"})
    ran = []
    monkeypatch.setattr(codec_dispatch, "run_skill", lambda *a, **k: ran.append(a))
    out = cs.run_scheduled(_job(id="s4", kind="skill", skill="terminal", task="rm -rf /tmp/x"))
    assert out["ok"] is False and ran == [] and "cannot run on a schedule" in cs.read_runs(sched_id="s4")[0]["output"]


def test_only_if_changed_holds_back_a_repeat(sch):
    cs = sch.cs
    sch.answers[:] = ["Same news.", "Same  news.", "New news."]
    job = _job(only_if_changed=True, deliver=["notification", "today"])
    cs.run_scheduled(job)
    cs.run_scheduled(job)
    assert len(sch.notified) == 1 and len(sch.today.list_cards()) == 1
    cs.run_scheduled(job)
    assert len(sch.notified) == 2 and len(sch.today.list_cards()) == 2
    assert [r["changed"] for r in cs.read_runs(sched_id="sched_t")] == [True, False, True]


def test_deliveries_notification_today_google_doc_and_speech(sch, monkeypatch):
    cs = sch.cs
    import codec_gdocs
    monkeypatch.setattr(codec_gdocs, "create_google_doc", lambda title, content: "https://docs.google.com/document/d/abc")
    sch.answers[:] = ["## Report\nAll quiet."]
    out = cs.run_scheduled(_job(label="Morning check", deliver=["notification", "today", "gdoc"], speak=True))
    assert out["doc_url"] == "https://docs.google.com/document/d/abc"
    (title, body, status), = sch.notified
    assert title == "Morning check" and body.startswith("https://docs.google.com/document/d/abc") and status == "success"
    card, = sch.today.list_cards()
    assert card["title"] == "Morning check" and card["url"] == out["doc_url"] and "All quiet." in card["body"]
    assert sch.spoken == ["## Report\nAll quiet."]
    sch.answers[:] = []
    import codec_llm
    monkeypatch.setattr(codec_llm, "call", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("model down")))
    assert cs.run_scheduled(_job(id="s5"))["ok"] is False
    assert sch.notified[-1][2] == "error" and "model down" in sch.notified[-1][1]


def test_history_keeps_full_output_and_reads_old_lines(sch):
    cs = sch.cs
    long = "x" * 5000
    cs.record_run("s1", "Long", "success", long, changed=True)
    with open(cs.RUNS_LOG, "a") as f:
        f.write(json.dumps({"timestamp": "2026-08-01T09:00:00", "schedule_id": "old", "title": "Old run",
                            "status": "success", "body_preview": "short preview"}) + "\n")
        f.write("2026-07-01 08:00 | daily_briefing | success | 42s\n")
    runs = cs.read_runs()
    assert [r["title"] for r in runs] == ["daily_briefing", "Old run", "Long"]
    assert runs[1]["output"] == "short preview" and runs[2]["output"] == long
    assert (os.stat(cs.RUNS_LOG).st_mode & 0o777) == 0o600


def test_check_and_run_claims_due_jobs_first(sch, monkeypatch):
    cs = sch.cs
    ran = []
    monkeypatch.setattr(cs, "run_scheduled", lambda s, manual=False: ran.append(s["id"]))
    cs.save_schedules([_job(id="a", when={"type": "daily", "hour": 8, "minute": 0, "days": list(range(7))}),
                       _job(id="b", when={"type": "daily", "hour": 11, "minute": 0, "days": list(range(7))}),
                       _job(id="c", enabled=False, when={"type": "every", "minutes": 60})])
    cs.check_and_run(TUE_10)
    assert ran == ["a"]
    assert {s["id"]: s.get("last_attempt") for s in cs.load_schedules()}["a"] == TUE_10.isoformat()
    cs.check_and_run(TUE_10 + timedelta(minutes=1))
    assert ran == ["a"], "not started again within the retry window"


def test_api_creates_updates_and_previews_jobs(sch, monkeypatch):
    import routes.schedules as rs
    app = FastAPI()
    app.include_router(rs.router)
    c = TestClient(app)
    r = c.post("/api/schedules", json={"kind": "prompt", "prompt": "What changed in my repo?", "when": "weekdays at 7:30",
                                       "deliver": ["today", "bogus"], "enabled": True, "evil": "x"})
    assert r.status_code == 200
    job = r.json()["schedule"]
    assert job["summary"] == "Weekdays at 07:30" and job["deliver"] == ["today"] and "evil" not in job
    assert job["enabled_at"] and job["next_run"]
    bad = c.post("/api/schedules", json={"kind": "prompt", "prompt": "x", "when": "soonish"})
    assert bad.status_code == 400 and "could not read" in bad.json()["error"]
    legacy = c.post("/api/schedules", json={"crew": "daily_briefing", "topic": "News", "hour": 8, "minute": 0, "days": [0, 1]})
    assert legacy.json()["schedule"]["summary"] == "Mon and Tue at 08:00"
    upd = c.put(f"/api/schedules/{job['id']}", json={"when": "every 2 hours", "evil": 1, "only_if_changed": True})
    assert upd.json()["schedule"]["summary"] == "Every 2 hours" and "evil" not in upd.json()["schedule"]
    assert c.put(f"/api/schedules/{job['id']}", json={"when": "at 99:00"}).status_code == 400
    assert c.put("/api/schedules/nope", json={"enabled": True}).status_code == 404
    assert c.post("/api/schedules/parse", json={"when": "every 30 minutes"}).json()["text"] == "Every 30 minutes"
    assert c.post("/api/schedules/parse", json={"when": "weekdays"}).json()["ok"] is False
    listing = c.get("/api/schedules").json()["schedules"]
    assert {s["kind"] for s in listing} == {"prompt", "crew"}
    started = []
    monkeypatch.setattr(sch.cs, "run_scheduled", lambda s, manual=False: started.append((s["id"], manual)))
    monkeypatch.setattr(rs.threading, "Thread", _Now)
    assert c.post(f"/api/schedules/{job['id']}/run").json()["status"] == "running"
    assert started == [(job["id"], True)]
    sch.cs.record_run(job["id"], "t", "success", "full output")
    assert c.get("/api/schedules/history").json()[0]["output"] == "full output"


def test_today_cards_store(sch):
    t = sch.today
    for i in range(35):
        t.add_card(f"Card {i}", "body")
    cards = t.list_cards()
    assert len(cards) == t.MAX_CARDS and cards[0]["title"] == "Card 34"
    assert t.dismiss(cards[0]["id"]) is True and t.dismiss(cards[0]["id"]) is False
    assert cards[0]["id"] not in {c["id"] for c in t.list_cards()}
    assert (os.stat(t.TODAY_PATH).st_mode & 0o777) == 0o600


def test_pages_offer_the_new_schedules():
    tasks = (REPO / "codec_tasks.html").read_text(encoding="utf-8")
    for needle in ('data-kind="prompt"', 'data-kind="skill"', 'data-kind="crew"', 'id="newWhen"', "previewWhen()",
                   "fetch('/api/schedules/parse'", 'id="dToday"', 'id="dGdoc"', 'id="oSpeak"', 'id="oChanged"',
                   'id="oContinuity"', "class=\"history-output\""):
        assert needle in tasks, needle
    chat = (REPO / "codec_chat.html").read_text(encoding="utf-8")
    assert 'data-act="schedule"' in chat and "CodecShell.schedule.open({prompt:_questionBefore(host)})" in chat
    shell = (REPO / "static" / "codec-shell.js").read_text(encoding="utf-8")
    assert "schedule: { open: schedOpen, close: schedClose, save: schedSave }" in shell
    assert "postJSON('/api/schedules', { kind: 'prompt'" in shell and "enabled: true" in shell
    home = (REPO / "codec_dashboard.html").read_text(encoding="utf-8")
    assert 'id="todayCards"' in home and "fetch('/api/today')" in home and "/dismiss', { method: 'POST' }" in home
    dash = (REPO / "codec_dashboard.py").read_text(encoding="utf-8")
    assert "app.include_router(today_router)" in dash
