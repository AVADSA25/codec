"""Proactive check-in (UI phase 3, P3.5; docs/P3.5-DESIGN.md).

A default-off check inside the heartbeat: gates (on, interval, active hours, at
the Mac, no image job, daily cap), a metadata-only snapshot for the LOCAL model,
NO_REPLY unless something needs attention, a check-in card on a hit (patterns
the owner turned off are dropped), one optional spoken sentence, every run
audited. Nothing here touches ~/.codec or a model: tmp paths and fakes.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
TUE_10 = datetime(2026, 9, 29, 10, 0)
HIT = json.dumps({"pattern": "Meeting Prep!", "title": "Meeting in 20 minutes 📅",
                  "message": "Your 10:20 call with the design team has no notes yet. 🚀", "spoken": "A meeting starts soon."})


@pytest.fixture
def ck(tmp_path, monkeypatch):
    import codec_checkin as c
    import codec_proactive
    import codec_today
    monkeypatch.setattr(c, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(c, "STATE_PATH", str(tmp_path / "checkin_state.json"))
    monkeypatch.setattr(c, "OBSERVER_PATH", str(tmp_path / "observer_buffer.json"))
    monkeypatch.setattr(codec_today, "TODAY_PATH", tmp_path / "today.json")
    monkeypatch.setattr(codec_proactive, "_STATE_PATH", tmp_path / "proactive_state.json")
    monkeypatch.delenv("CHECKIN_ENABLED", raising=False)
    ns = SimpleNamespace(c=c, today=codec_today, tmp=tmp_path, audits=[], asked=[], answers=[], spoken=[],
                         real_ask=c._ask_model)
    monkeypatch.setattr(c, "_audit", lambda outcome, reason="", **extra: ns.audits.append((outcome, reason, extra)))
    monkeypatch.setattr(c, "_idle_seconds", lambda: 20.0)
    monkeypatch.setattr(c, "_image_busy", lambda: False)
    monkeypatch.setattr(c, "_calendar_next_hours", lambda: "10:20 Design review")
    monkeypatch.setattr(c, "_follow_ups", lambda: ["Reply to the landlord"])
    monkeypatch.setattr(c, "_important_unread", lambda: 3)
    monkeypatch.setattr(c, "_failing_services", lambda: [])

    def ask(snap):
        ns.asked.append(snap)
        return ns.answers.pop(0) if ns.answers else "NO_REPLY"

    monkeypatch.setattr(c, "_ask_model", ask)
    import codec_scheduler
    monkeypatch.setattr(codec_scheduler, "_speak_on_mac", ns.spoken.append)
    (tmp_path / "config.json").write_text(json.dumps({"checkin": {"enabled": True}}))
    return ns


def test_off_by_default_and_the_kill_switches(ck, monkeypatch):
    c = ck.c
    Path(c.CONFIG_PATH).write_text("{}")
    assert c.settings()["enabled"] is False and c.run_checkin(TUE_10) == "off" and ck.asked == []
    Path(c.CONFIG_PATH).write_text(json.dumps({"checkin": {"enabled": True}}))
    monkeypatch.setenv("CHECKIN_ENABLED", "false")
    assert c.run_checkin(TUE_10) == "off"


def test_timing_hours_away_image_job_and_cap_gates(ck, monkeypatch):
    c = ck.c
    assert c.run_checkin(TUE_10.replace(hour=7)) == "outside hours" and ck.audits == []
    assert c.run_checkin(TUE_10) == "no_reply"
    assert c.run_checkin(TUE_10 + timedelta(minutes=20)) == "not yet", "every 45 minutes by default"
    monkeypatch.setattr(c, "_idle_seconds", lambda: 3600.0)
    assert c.run_checkin(TUE_10 + timedelta(minutes=50)) == "skipped: away"
    monkeypatch.setattr(c, "_idle_seconds", lambda: 5.0)
    monkeypatch.setattr(c, "_image_busy", lambda: True)
    assert c.run_checkin(TUE_10 + timedelta(minutes=100)) == "skipped: image job"
    monkeypatch.setattr(c, "_image_busy", lambda: False)
    Path(c.CONFIG_PATH).write_text(json.dumps({"checkin": {"enabled": True, "daily_cap": 1}}))
    ck.answers[:] = [HIT]
    assert c.run_checkin(TUE_10 + timedelta(minutes=150)) == "hit"
    assert c.run_checkin(TUE_10 + timedelta(minutes=200)) == "skipped: daily cap"
    assert [a[0] for a in ck.audits] == ["no_reply", "skipped", "skipped", "hit", "skipped"], "every run audited"


def test_the_snapshot_is_metadata_only_without_emoji(ck):
    c = ck.c
    Path(c.OBSERVER_PATH).write_text(json.dumps({"entries": [
        {"active_window": {"app": "Mail", "title": "Re: salary review 💰"}, "screenshot_ocr": "secret screen text",
         "clipboard": {"content_type": "text", "length": 40}},
        {"active_window": {"app": "Safari", "title": "Bank statement"}, "screenshot_ocr": "account 1234"}]}))
    Path(c.CONFIG_PATH).write_text(json.dumps({"checkin": {"enabled": True, "checklist": ["Send the invoice"]}}))
    c.run_checkin(TUE_10)
    snap = json.dumps(ck.asked[0], ensure_ascii=False)
    assert ck.asked[0]["observer"] == {"current_app": "Safari", "recent_apps": ["Mail", "Safari"]}
    assert "salary" not in snap and "Bank statement" not in snap and "secret screen" not in snap and "1234" not in snap
    assert ck.asked[0]["checklist"] == ["Send the invoice"] and ck.asked[0]["unread_important_email"] == 3
    assert ck.asked[0]["calendar_today"] == "10:20 Design review" and ck.asked[0]["follow_ups"] == ["Reply to the landlord"]


def test_a_hit_posts_one_card_and_can_speak(ck):
    c = ck.c
    Path(c.CONFIG_PATH).write_text(json.dumps({"checkin": {"enabled": True, "speak": True}}))
    ck.answers[:] = ["NO_REPLY"]
    assert c.run_checkin(TUE_10) == "no_reply" and ck.today.list_cards() == [] and ck.spoken == []
    ck.answers[:] = [HIT]
    assert c.run_checkin(TUE_10 + timedelta(minutes=50)) == "hit"
    card, = ck.today.list_cards()
    assert card["kind"] == "checkin" and card["source"] == "checkin:meeting-prep"
    assert card["title"] == "Meeting in 20 minutes" and "🚀" not in card["body"] and "📅" not in card["title"]
    assert ck.spoken == ["A meeting starts soon."]
    assert c.status(TUE_10)["hits_today"] == 1


def test_patterns_turned_off_are_dropped(ck):
    c = ck.c
    import codec_proactive
    codec_proactive.dismiss("checkin:meeting-prep", scope="forever")
    ck.answers[:] = [HIT]
    assert c.run_checkin(TUE_10) == "dropped: pattern off" and ck.today.list_cards() == []
    codec_proactive._save_state(codec_proactive._empty_state())
    codec_proactive.dismiss("checkin:meeting-prep", scope="today")
    ck.answers[:] = [HIT]
    assert c.run_checkin(TUE_10 + timedelta(minutes=50)) == "dropped: pattern off"


@pytest.mark.parametrize("answer,parsed", [
    ("NO_REPLY", None), ("no_reply.", None), ("", None), ("Everything looks fine.", None),
    ('{"pattern": "x", "title": "t", "message": ""}', None),
    ('Sure: {"pattern": "Bank Call", "title": "Call", "message": "Call the bank before 11.", "spoken": "Call the bank."}',
     {"pattern": "checkin:bank-call", "title": "Call", "message": "Call the bank before 11.", "spoken": "Call the bank."}),
])
def test_answers_are_read_strictly(answer, parsed):
    import codec_checkin
    assert codec_checkin.parse_answer(answer) == parsed


def test_the_local_model_only(ck, monkeypatch):
    import codec_cloud_models
    import codec_llm
    called = []
    monkeypatch.setattr(codec_llm, "call", lambda *a, **k: called.append(k) or "NO_REPLY")
    monkeypatch.setattr(codec_cloud_models, "local_only", lambda url, model, cfg=None: ("https://api.cloud.example/v1", "m"))
    with pytest.raises(RuntimeError, match="no local model"):
        ck.real_ask({"now": "Tue"})
    assert called == [], "never sent to a cloud model"
    monkeypatch.setattr(codec_cloud_models, "local_only", lambda url, model, cfg=None: ("http://localhost:8083/v1", "local"))
    assert ck.real_ask({"now": "Tue"}) == "NO_REPLY" and called[0]["base_url"] == "http://localhost:8083/v1"


def test_the_heartbeat_runs_the_checkin_and_its_text_has_no_emoji():
    hb = (REPO / "codec_heartbeat.py").read_text(encoding="utf-8")
    assert "codec_checkin.run_checkin()" in hb
    body = hb[hb.index("def _check_one_service"):hb.index("def check_system_health")]
    assert '"ok"' in body and '"DOWN"' in body and "✅" not in body and "❌" not in body


def test_settings_route_validates(ck):
    import routes.checkin as rc
    app = FastAPI()
    app.include_router(rc.router)
    client = TestClient(app)
    d = client.get("/api/checkin").json()
    assert d["enabled"] is True and d["every_minutes"] == 45 and d["hits_today"] == 0
    ok = client.put("/api/checkin", json={"every_minutes": 60, "active_from": "09:00", "active_until": "18:30",
                                          "daily_cap": 3, "speak": True, "checklist": "Invoice\n\n Plants \n"})
    assert ok.status_code == 200 and ok.json()["checklist"] == ["Invoice", "Plants"] and ok.json()["every_minutes"] == 60
    for bad in ({"every_minutes": 5}, {"daily_cap": 50}, {"active_from": "25:00"}, {"active_from": "19:00", "active_until": "08:00"},
                {"checklist": ["x"] * 21}):
        assert client.put("/api/checkin", json=bad).status_code == 400, bad
    assert json.loads(Path(ck.c.CONFIG_PATH).read_text())["checkin"]["daily_cap"] == 3


def test_home_offers_settings_and_card_actions():
    home = (REPO / "codec_dashboard.html").read_text(encoding="utf-8")
    for needle in ('id="checkinSection"', 'id="ckOn"', 'id="ckEvery"', 'id="ckFrom"', 'id="ckUntil"', 'id="ckCap"',
                   'id="ckSpeak"', 'id="ckList"', "loadCheckin();", "function _checkinCard(card, c)",
                   "post('/api/proactive/acknowledge'", "post('/api/proactive/dismiss', { pattern_id: c.source",
                   "[['today', 'today'], ['never', 'forever']]", "_ssSet('codec-chat-draft', c.body"):
        assert needle in home, needle
    dash = (REPO / "codec_dashboard.py").read_text(encoding="utf-8")
    assert "app.include_router(checkin_router)" in dash
