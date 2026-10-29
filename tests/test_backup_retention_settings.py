"""Settings > Memory: how long chats, clipboard history and webcam photos are kept (D-38;
docs/BACKUP-RETENTION-DESIGN.md). Paths are patched to tmp_path; HOME is never faked.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import codec_backup

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def page(tmp_path, monkeypatch):
    import codec_scheduler
    import routes.memory_page as mp
    home = tmp_path / "codec"
    home.mkdir()
    cfg = home / "config.json"
    cfg.write_text(json.dumps({"llm_model": "m", "memory": {"fact_extract": True}}))
    monkeypatch.setattr(codec_backup, "CODEC_HOME", str(home))
    monkeypatch.setattr(mp, "CONFIG_PATH", str(cfg))
    monkeypatch.setattr(codec_scheduler, "SCHEDULE_PATH", str(tmp_path / "schedules.json"))
    audits = []
    monkeypatch.setattr(mp, "_audit", lambda action, **extra: audits.append((action, extra)))
    app = FastAPI()
    app.include_router(mp.router)
    return SimpleNamespace(client=TestClient(app), cfg=cfg, home=home, audits=audits)


def _cfg(p) -> dict:
    return json.loads(p.cfg.read_text())


def test_settings_show_the_d38_defaults(page):
    d = page.client.get("/api/memory/settings").json()
    assert d["retention"] == {"chats_days": 90, "clipboard_days": 30, "photos_days": 30}
    assert d["retention_defaults"] == d["retention"] and d["retention_last"] == {}


def test_saving_a_number_writes_config_and_keeps_the_rest(page):
    r = page.client.put("/api/memory/settings", json={"retention": {"chats_days": 60, "photos_days": 0}})
    assert r.status_code == 200 and r.json()["retention"] == {"chats_days": 60, "clipboard_days": 30, "photos_days": 0}
    cfg = _cfg(page)
    assert cfg["retention"] == {"chats_days": 60, "photos_days": 0} and cfg["llm_model"] == "m"
    assert page.audits == [("retention_set", {"chats_days": 60, "photos_days": 0})]
    assert codec_backup.retention_settings() == {"chats_days": 60, "clipboard_days": 30, "photos_days": 0}


@pytest.mark.parametrize("bad", [{"chats_days": 3}, {"clipboard_days": -1}, {"photos_days": "abc"},
                                 {"chats_days": True}, {"chats_days": None}, {"unknown": 5}, {}])
def test_bad_numbers_are_refused_and_change_nothing(page, bad):
    before = page.cfg.read_text()
    r = page.client.put("/api/memory/settings", json={"retention": bad, "fact_extract": False})
    assert r.status_code == 400 and "Days" in r.json()["error"]
    assert page.cfg.read_text() == before and page.audits == []


def test_settings_show_the_counts_of_the_last_clean_up(page):
    (page.home / "photos").mkdir()
    (page.home / "photos" / "webcam_20200101_000000.jpg").write_bytes(b"x")
    codec_backup.run_retention(now=datetime(2026, 10, 30, 3, 0))
    last = page.client.get("/api/memory/settings").json()["retention_last"]
    assert last["photos"] == {"at": "2026-10-30T03:00:00", "deleted": 1, "note": None}
    assert last["chats"]["note"] == "no database"


def test_the_settings_page_has_the_three_fields_and_saves_them():
    html = (REPO / "codec_dashboard.html").read_text()
    for el in ('id="memKeepChats"', 'id="memKeepClipboard"', 'id="memKeepPhotos"', 'onclick="saveRetention(this)"',
               "function saveRetention(btn)", "function _showRetention(d)"):
        assert el in html
    assert "'chats_days', 'memKeepChats'" in html and "_memConfirm(btn, 'Keep less? Click again'" in html
