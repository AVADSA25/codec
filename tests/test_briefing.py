"""Morning briefing (UI phase 3, P3.1; docs/P3.1-DESIGN.md).

Off by default; switched on in Settings it is a P3.3 schedule job that runs
daily_kickoff, waits while an image job or low memory, leaves a briefing card
with the open Daybreak threads, sends a content-free push and says a short
script on the Mac at the first activity before noon, with ways to stop it.
Nothing here touches ~/.codec, the Mac's speakers or the model: all fakes.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
TUE_8 = datetime(2026, 9, 29, 8, 0)


class _Now:
    def __init__(self, target, args=(), kwargs=None, **kw):
        self.target, self.args, self.kwargs = target, args, kwargs or {}

    def start(self):
        self.target(*self.args, **self.kwargs)


@pytest.fixture
def br(tmp_path, monkeypatch):
    import codec_briefing as cb
    import codec_daybreak
    import codec_llm
    import codec_push
    import codec_scheduler as cs
    import codec_today
    monkeypatch.setattr(cs, "SCHEDULE_PATH", str(tmp_path / "schedules.json"))
    monkeypatch.setattr(cs, "RUNS_LOG", str(tmp_path / "runs.log"))
    monkeypatch.setattr(cs, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(codec_today, "TODAY_PATH", tmp_path / "today.json")
    monkeypatch.setattr(cb, "STATE_PATH", str(tmp_path / "briefing_state.json"))
    monkeypatch.setattr(cb, "VOICE_SESSION_PATH", str(tmp_path / "voice_session.json"))
    monkeypatch.setattr(cb, "_audit_path", lambda: str(tmp_path / "audit.log"))
    (tmp_path / "audit.log").write_text("")
    ns = SimpleNamespace(cb=cb, cs=cs, today=codec_today, tmp=tmp_path, pushed=[], closed=[], answers=[],
                         threads=[{"key": "thread:follow_up:call-the-bank", "kind": "follow_up", "text": "Call the bank"},
                                  {"key": "thread:working_on:site-redesign", "kind": "working_on", "text": "Site redesign"}])
    monkeypatch.setattr(codec_push, "notify", ns.pushed.append)
    monkeypatch.setattr(codec_daybreak, "get_open_threads", lambda user_id="default": list(ns.threads))
    monkeypatch.setattr(codec_daybreak, "close_thread", lambda key, user_id="default": ns.closed.append(key) or f"Closed thread: {key}")

    def fake_llm(messages, **kw):
        if ns.answers:
            a = ns.answers.pop(0)
            if isinstance(a, Exception):
                raise a
            return a
        return "Good morning. Two meetings today."

    monkeypatch.setattr(codec_llm, "call", fake_llm)
    monkeypatch.setattr(cb, "busy", lambda: None)
    monkeypatch.setattr(cb, "_idle_seconds", lambda: 5.0)
    return ns


def test_off_by_default_and_switched_on_in_settings(br):
    cb, cs = br.cb, br.cs
    s = cb.settings()
    assert s["enabled"] is False and s["when"] == "Weekdays at 07:30" and cs.load_schedules() == []
    assert cb.update({"enabled": False}) and cs.load_schedules() == [], "switching off creates nothing"
    on = cb.update({"enabled": True})
    job, = cs.load_schedules()
    assert on["enabled"] and job["id"] == cb.BRIEFING_ID and job["kind"] == "skill" and job["skill"] == "daily_kickoff"
    assert job["deliver"] == ["briefing"] and job["managed"] == "briefing" and job["enabled_at"]
    assert cb.update({"when": "every day at 6:45"})["when"] == "Every day at 06:45"
    assert cb.update({"enabled": False})["enabled"] is False
    with pytest.raises(ValueError):
        cb.update({"when": "whenever"})


def test_the_job_waits_while_busy(br, monkeypatch):
    cb, cs = br.cb, br.cs
    cb.update({"enabled": True})
    cs._update_schedules(lambda ss: ss[0].update(enabled_at="2026-09-01T00:00:00", created="2026-09-01T00:00:00"))
    ran = []
    monkeypatch.setattr(cs, "run_scheduled", lambda s, manual=False: ran.append(s["id"]))
    monkeypatch.setattr(cb, "busy", lambda: "an image job is running")
    cs.check_and_run(TUE_8)
    assert ran == []
    monkeypatch.setattr(cb, "busy", lambda: None)
    cs.check_and_run(TUE_8)
    assert ran == [cb.BRIEFING_ID]


def test_busy_reads_the_image_lock_and_free_memory(monkeypatch):
    import codec_briefing as cb
    import codec_image
    monkeypatch.setattr(codec_image, "busy_message", lambda: "An image is being made")
    assert cb.busy() == "an image job is running"
    monkeypatch.setattr(codec_image, "busy_message", lambda: None)
    monkeypatch.setattr(cb, "_free_gb", lambda: 2.0)
    assert cb.busy() == "the Mac is short of memory"
    monkeypatch.setattr(cb, "_free_gb", lambda: 12.0)
    assert cb.busy() is None


def test_delivery_leaves_the_card_pushes_and_waits_with_a_script(br):
    cb = br.cb
    br.answers[:] = ["Good morning. CODEC sees two meetings and a call to make."]
    card_id = cb.deliver({"id": cb.BRIEFING_ID, "speak": True}, "## Where we left off\n- **Site** redesign")
    card = br.today.get_card(card_id)
    assert card["kind"] == "briefing" and card["title"] == "Your morning briefing"
    assert [t["key"] for t in card["threads"]] == ["thread:follow_up:call-the-bank", "thread:working_on:site-redesign"]
    assert br.pushed == ["briefing"]
    state = json.loads(Path(cb.STATE_PATH).read_text())
    assert state["spoken"] is False and state["card_id"] == card_id and "CODEC" not in state["script"]
    br.answers[:] = [RuntimeError("model down")]
    cb.deliver({"id": cb.BRIEFING_ID, "speak": True}, "## Today\n- **Gym** at 7\n- Call [the bank](https://x.y)")
    script = json.loads(Path(cb.STATE_PATH).read_text())["script"]
    assert "Gym at 7" in script and "**" not in script and "http" not in script, "falls back to plain words"
    Path(cb.STATE_PATH).unlink()
    cb.deliver({"id": cb.BRIEFING_ID, "speak": False}, "Quiet day.")
    src = (REPO / "codec_briefing.py").read_text(encoding="utf-8")
    assert "codec_cloud_models.local_only(" in src and "is_local_url(base_url)" in src, "the local model only"
    assert not Path(cb.STATE_PATH).exists(), "no speaking: no script waits"


def test_it_speaks_only_at_the_first_activity_before_noon_once(br, monkeypatch):
    cb = br.cb
    cb.deliver({"id": cb.BRIEFING_ID, "speak": True}, "Briefing text.")
    started = []
    monkeypatch.setattr(cb, "threading", SimpleNamespace(Thread=lambda target, args=(), **kw: SimpleNamespace(
        start=lambda: started.append(args[0]))))
    today = datetime.now().replace(hour=8, minute=10)
    monkeypatch.setattr(cb, "_idle_seconds", lambda: 900.0)
    assert cb.tick(today) == "nobody there"
    monkeypatch.setattr(cb, "_idle_seconds", lambda: 5.0)
    assert cb.tick(today.replace(hour=12, minute=30)) == "too late"
    Path(cb.VOICE_SESSION_PATH).write_text("{}")
    assert cb.tick(today) == "voice call"
    Path(cb.VOICE_SESSION_PATH).unlink()
    monkeypatch.setattr(cb, "busy", lambda: "an image job is running")
    assert cb.tick(today) == "an image job is running"
    monkeypatch.setattr(cb, "busy", lambda: None)
    assert cb.tick(today) == "speaking" and started == ["Good morning. Two meetings today."]
    assert cb.tick(today) is None and len(started) == 1, "said once"


class _Proc:
    """A stand-in afplay: finishes after `polls` polls unless terminated."""
    played, polls = [], 3

    def __init__(self, args, **kw):
        self.args, self.n, self.terminated = args, 0, False
        _Proc.played.append(args[1])

    def poll(self):
        self.n += 1
        return 0 if self.terminated or self.n > _Proc.polls else None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


@pytest.mark.parametrize("trigger,reason", [("stop", "stopped"), ("wake", "wake word"), ("call", "voice call"), (None, "done")])
def test_the_speaker_plays_a_chime_and_stops_three_ways(br, monkeypatch, trigger, reason):
    cb = br.cb
    _Proc.played = []
    _Proc.polls = 3 if trigger is None else 10_000
    monkeypatch.setattr(cb.subprocess, "Popen", _Proc)
    monkeypatch.setattr(cb, "_tts", lambda text: b"ID3fake")
    monkeypatch.setattr(cb.os.path, "exists", lambda p: (p == cb.CHIME) or (trigger == "call" and p == cb.VOICE_SESSION_PATH and _Proc.played != []))
    audit = Path(cb._audit_path())

    def sleep(_s):
        if trigger == "stop":
            cb.stop()
        elif trigger == "wake":
            audit.write_text(audit.read_text() + json.dumps({"event": "wake_word_detected"}) + "\n")

    monkeypatch.setattr(cb.time, "sleep", sleep)
    assert cb.speak("First sentence. Second one.") == reason
    assert _Proc.played[0] == cb.CHIME
    if reason == "done":
        assert len(_Proc.played) == 3, "the chime, then one file per sentence"
    assert cb._SPEAKING["on"] is False


def test_card_threads_done_snooze_and_fetch(br):
    import routes.today as rt
    app = FastAPI()
    app.include_router(rt.router)
    c = TestClient(app)
    card = br.today.add_card("Your morning briefing", "Body", kind="briefing", threads=br.threads)
    r = c.post("/api/today/threads/done", json={"key": "thread:follow_up:call-the-bank", "card_id": card["id"]})
    assert r.json()["ok"] is True and br.closed == ["thread:follow_up:call-the-bank"]
    assert [t["key"] for t in c.get(f"/api/today/{card['id']}").json()["threads"]] == ["thread:working_on:site-redesign"]
    assert c.post("/api/today/threads/done", json={"key": "../etc", "card_id": card["id"]}).status_code == 400
    assert c.post(f"/api/today/{card['id']}/snooze", json={"minutes": 120}).json()["ok"] is True
    assert c.get("/api/today").json()["cards"] == [], "snoozed cards wait"
    assert br.today.list_cards(now=datetime.now().replace(year=2030))[0]["id"] == card["id"]


def test_briefing_routes(br, monkeypatch):
    import routes.briefing as rb
    app = FastAPI()
    app.include_router(rb.router)
    c = TestClient(app)
    assert c.get("/api/briefing").json()["enabled"] is False
    assert c.post("/api/briefing/run").status_code == 409, "not set up yet"
    on = c.put("/api/briefing", json={"enabled": True, "when": "weekdays at 7:00", "speak": False, "evil": 1}).json()
    assert on["enabled"] and on["when"] == "Weekdays at 07:00" and on["speak"] is False
    assert c.put("/api/briefing", json={"when": "someday"}).status_code == 400
    ran = []
    monkeypatch.setattr(br.cs, "run_scheduled", lambda s, manual=False: ran.append((s["id"], manual)))
    monkeypatch.setattr(rb.threading, "Thread", _Now)
    assert c.post("/api/briefing/run").json()["status"] == "running" and ran == [(br.cb.BRIEFING_ID, True)]
    assert c.post("/api/briefing/stop").json()["ok"] is True


def test_the_job_runs_the_kickoff_and_delivers_as_a_briefing(br, monkeypatch):
    cb, cs = br.cb, br.cs
    cb.update({"enabled": True})
    import codec_dispatch
    notified = []
    monkeypatch.setattr(cs, "_notify", lambda *a, **k: notified.append(a))
    monkeypatch.setattr(cs, "schedulable_skill", lambda name: name == "daily_kickoff")
    monkeypatch.setattr(codec_dispatch, "run_skill", lambda skill, task, app="": "## Good morning\nTwo meetings.")
    assert cs.run_scheduled(cs.load_schedules()[0])["ok"] is True
    card, = br.today.list_cards()
    assert card["kind"] == "briefing" and "Two meetings." in card["body"] and notified == []
    with pytest.raises(ValueError, match="set up in Settings"):
        cs.update_job(cb.BRIEFING_ID, {"when": "every 2h"})
    assert cs.update_job(cb.BRIEFING_ID, {"enabled": False})["enabled"] is False
    assert cs.job_view(cs.load_schedules()[0])["managed"] == "briefing"


def test_pages_offer_the_briefing():
    home = (REPO / "codec_dashboard.html").read_text(encoding="utf-8")
    for needle in ('id="briefingSection"', 'id="briefOn"', 'id="briefWhen"', 'id="briefSpeak"', "loadBriefing();",
                   "function _briefingCard(card, c)", "fetch('/api/today/threads/done'", "_ssSet('codec-chat-draft'",
                   "href=\"/chat#card=' + encodeURIComponent(c.id)", "fetch('/api/briefing/stop'"):
        assert needle in home, needle
    chat = (REPO / "codec_chat.html").read_text(encoding="utf-8")
    assert "h.match(/^#card=(card_[0-9a-f]{10})$/)" in chat and "_ssGet('codec-chat-draft')" in chat
    tasks = (REPO / "codec_tasks.html").read_text(encoding="utf-8")
    assert "if (s.managed) {" in tasks and "'/#settings'" in tasks and "Set up in Settings" in tasks
    daybreak = (REPO / "docs" / "DAYBREAK-DESIGN.md").read_text(encoding="utf-8")
    assert "opt-in Morning briefing" in daybreak
