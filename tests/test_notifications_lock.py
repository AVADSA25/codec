"""Fix #9 Phase 2: _save_notification cross-process safety.

routes/_shared._save_notification does a load->insert->write RMW under the
in-process `_notif_lock` only. The notification writers run in SEPARATE PM2
daemons (dashboard, scheduler, heartbeat, autopilot), which do NOT share that
threading lock — so concurrent saves across processes clobber each other.

This test simulates separate processes by neutralizing the in-process lock,
then asserts no notification is lost. It fails before the cross-process
file_lock is added and passes after.
"""
import contextlib
import json
import threading
import time

import codec_jsonstore
import routes._shared as shared


def test_save_notification_no_clobber_across_processes(tmp_path, monkeypatch):
    notifs_path = tmp_path / "notifications.json"
    monkeypatch.setattr(shared, "NOTIFICATIONS_PATH", str(notifs_path))
    codec_jsonstore.atomic_write_json(notifs_path, [])  # start empty (no sample seed)

    # Simulate independent processes: each daemon has its OWN process, so the
    # in-process _notif_lock does not serialize them. nullcontext models that.
    monkeypatch.setattr(shared, "_notif_lock", contextlib.nullcontext())

    # Widen the read-modify-write window so an unlocked path reliably clobbers.
    real_write = shared._write_notifications

    def slow_write(notifs):
        time.sleep(0.01)
        return real_write(notifs)

    monkeypatch.setattr(shared, "_write_notifications", slow_write)

    n = 12
    barrier = threading.Barrier(n)

    def worker(i):
        barrier.wait()
        shared._save_notification(f"t{i}", "body")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    titles = {x["title"] for x in json.loads(notifs_path.read_text())}
    assert titles == {f"t{i}" for i in range(n)}, (
        f"notifications clobbered across processes: {len(titles)}/{n} survived"
    )


# P-035: mark-read held only the dashboard's thread lock, so an append from
# another process (scheduler, heartbeat, an agent) landing between its read and
# its write was written over. A real second Python process appends here, the way
# those daemons do, while a mark-read is between its read and its write.
_APPEND = r"""
import json, pathlib, sys
sys.path.insert(0, sys.argv[3])
import codec_jsonstore
path, started = sys.argv[1], sys.argv[2]
pathlib.Path(started).write_text("1")
with codec_jsonstore.file_lock(path):
    data = json.loads(pathlib.Path(path).read_text())
    data.insert(0, {"id": "notif_other", "title": "from another process", "read": False,
                    "created": "2026-11-03T10:00:00"})
    codec_jsonstore.atomic_write_json(path, data)
"""


def _interleave(tmp_path, monkeypatch, request_fn):
    import os
    import subprocess
    import sys
    from pathlib import Path

    import routes.notifications as rn
    repo = str(Path(__file__).resolve().parent.parent)
    notifs_path = tmp_path / "notifications.json"
    monkeypatch.setattr(shared, "NOTIFICATIONS_PATH", str(notifs_path))
    codec_jsonstore.atomic_write_json(notifs_path, [
        {"id": "notif_mine", "title": "mine", "read": False, "created": "2026-11-03T09:00:00"}])
    started = tmp_path / "started"
    procs = []
    real_write = rn._write_notifications

    def write_after_the_other_process_tried(notifs):
        procs.append(subprocess.Popen([sys.executable, "-c", _APPEND, str(notifs_path), str(started), repo],
                                      cwd=repo, env=dict(os.environ)))
        deadline = time.monotonic() + 20
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        time.sleep(0.5)   # unlocked, the other process appends in this window
        return real_write(notifs)

    monkeypatch.setattr(rn, "_write_notifications", write_after_the_other_process_tried)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(rn.router)
    r = request_fn(TestClient(app))
    assert procs and procs[0].wait(timeout=30) == 0
    assert r.status_code == 200, r.text
    return {n["id"]: n for n in json.loads(notifs_path.read_text())}


def test_mark_read_and_an_append_from_another_process_keep_both(tmp_path, monkeypatch):
    kept = _interleave(tmp_path, monkeypatch, lambda c: c.post("/api/notifications/notif_mine/read"))
    assert set(kept) == {"notif_mine", "notif_other"}, f"an append was written over: {sorted(kept)}"
    assert kept["notif_mine"]["read"] is True and kept["notif_other"]["read"] is False


def test_read_all_and_an_append_from_another_process_keep_both(tmp_path, monkeypatch):
    kept = _interleave(tmp_path, monkeypatch, lambda c: c.post("/api/notifications/read-all"))
    assert set(kept) == {"notif_mine", "notif_other"}, f"an append was written over: {sorted(kept)}"
    assert kept["notif_mine"]["read"] is True
