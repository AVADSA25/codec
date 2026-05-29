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
