"""A clean Mac starts with no reports (P-035).

routes/_shared._load_notifications used to replace a missing or unreadable
notifications.json with four invented March-2026 reports (one unread, so the
Inbox showed a badge) and write them to disk. Now a missing file reads as empty
and nothing is written; an unreadable file is copied aside, never dropped.
Every path here is a temp folder: nothing touches ~/.codec.
"""
from __future__ import annotations

import json
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def box(tmp_path, monkeypatch):
    import codec_ask_user
    import routes._shared as sh
    import routes.inbox as ri
    import routes.notifications as rn
    folder = tmp_path / "codec"
    folder.mkdir()
    nf = folder / "notifications.json"
    monkeypatch.setattr(sh, "NOTIFICATIONS_PATH", str(nf))
    monkeypatch.setattr(sh, "_pending_approvals", {})
    other = tmp_path / "questions"
    other.mkdir()
    monkeypatch.setattr(codec_ask_user, "PENDING_QUESTIONS_PATH", other / "pending_questions.json")
    app = FastAPI()
    app.include_router(ri.router)
    app.include_router(rn.router)
    return TestClient(app), folder, nf, sh


def test_an_empty_folder_reads_empty_and_stays_empty(box):
    client, folder, _nf, _sh = box
    assert client.get("/api/notifications").json() == {"notifications": []}
    assert client.get("/api/notifications?unread=true").json() == {"notifications": []}
    assert client.get("/api/notifications/count").json() == {"unread": 0}
    inbox = client.get("/api/inbox").json()
    assert inbox["items"] == []
    assert inbox["counts"] == {"needs_you": 0, "reports": 0, "agents": 0, "suggestions": 0, "badge": 0}
    assert os.listdir(folder) == [], "reading wrote a file"
    # Read-all takes the cross-process lock (its sidecar), but marks nothing and writes no list.
    assert client.post("/api/notifications/read-all").json() == {"status": "ok", "marked": 0}
    assert os.listdir(folder) in ([], ["notifications.json.lock"])


def test_no_invented_report_text_is_left_in_the_code():
    import inspect
    import routes._shared as sh
    src = inspect.getsource(sh)
    for invented in ("Security Scan", "142 dependencies", "Weekly Code Review Summary", "2026-03-29"):
        assert invented not in src, invented


@pytest.mark.parametrize("bad", [b"{not json", b"\xff\xfe\x00garbage", b'{"notifications": []}'])
def test_an_unreadable_file_is_copied_aside_once_and_never_dropped(box, bad):
    client, folder, nf, sh = box
    nf.write_bytes(bad)
    for _ in range(3):   # the Inbox polls: one copy, not one per poll
        assert client.get("/api/notifications").json() == {"notifications": []}
        assert client.get("/api/inbox").json()["counts"]["badge"] == 0
    aside = sorted(p for p in folder.iterdir() if p.name.startswith("notifications.json.corrupt-"))
    assert len(aside) == 1 and aside[0].read_bytes() == bad
    assert nf.read_bytes() == bad, "reading never rewrites the file"
    sh._save_notification("Real report", "done")   # the next real write replaces it
    assert [n["title"] for n in json.loads(nf.read_text())] == ["Real report"]
    assert aside[0].read_bytes() == bad, "the copy stays"


def test_a_real_file_is_read_as_it_is(box):
    client, _folder, nf, _sh = box
    nf.write_text(json.dumps([{"id": "notif_r1", "type": "task_report", "title": "Morning news",
                               "status": "success", "created": "2026-10-06T08:00:00", "read": False}]))
    before = nf.read_bytes()
    d = client.get("/api/notifications").json()
    assert [n["id"] for n in d["notifications"]] == ["notif_r1"]
    assert client.get("/api/inbox").json()["counts"]["badge"] == 1
    assert nf.read_bytes() == before
