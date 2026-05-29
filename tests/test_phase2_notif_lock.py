"""Fix #9 Phase 2: the secondary notifications.json writers (codec_scheduler,
codec_heartbeat) did a load→insert→write RMW with no cross-process lock, so they
clobbered the now-locked dashboard/ask_user writers. They must hold
codec_jsonstore.file_lock across the RMW like every other notifications writer.
"""
import contextlib
import json


def test_scheduler_notify_holds_file_lock(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".codec").mkdir(parents=True, exist_ok=True)
    # don't fire a real macOS notification during the test
    monkeypatch.setattr("subprocess.run", lambda *a, **k: None)

    import codec_jsonstore
    import codec_scheduler

    calls = []
    real = codec_jsonstore.file_lock

    @contextlib.contextmanager
    def spy(path):
        calls.append(str(path))
        with real(path):
            yield

    monkeypatch.setattr(codec_jsonstore, "file_lock", spy)

    codec_scheduler._notify("Test", "body text", status="success", schedule_id="x")

    notif_path = tmp_path / ".codec" / "notifications.json"
    assert any("notifications.json" in c for c in calls), (
        "scheduler._notify must hold the cross-process flock on notifications.json (Phase 2)"
    )
    # and it actually persisted a valid, atomic notification
    data = json.loads(notif_path.read_text())
    assert data and data[0]["title"] == "Test"
