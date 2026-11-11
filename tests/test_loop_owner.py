"""One owner per background loop, and a memory-pressure alert (P-083, docs/LOOP-OWNER-DESIGN.md).

heartbeat() runs once at a time on the Mac (lock plus last-run stamp), the two
alert_state.json writers keep each other's counters, a draft task is claimed by
rename so two watchers run it once, and memory_check() alerts mem_pressure after
two high ticks. Every test works in tmp_path with fixture tool outputs: none
runs pm2, a memory tool or a real alert channel.
"""
from __future__ import annotations

import asyncio
import fcntl
import json
import os
import threading
import time
import types

import pytest

import codec_alerts
import codec_heartbeat
import codec_jsonstore
import codec_watcher

SWAP_HIGH = ("vm.swapusage: total = 11264.00M  used = 10218.00M  free = 1046.00M  (encrypted)\n")
SWAP_LOW = "vm.swapusage: total = 1024.00M  used = 100.00M  free = 924.00M  (encrypted)\n"
PRESSURE = ("The system has 68719476736 (4194304 pages with a page size of 16384).\n"
            "System-wide memory free percentage: 75%\n")
FOOTPRINT_BYTES = """======================================================================
Python [{pid}]: 64-bit    Footprint: {n} B (16384 bytes per page)
======================================================================

Auxiliary data:
    phys_footprint: {n} B
    phys_footprint_peak: 27931918920 B
"""
FOOTPRINT_FORMATTED = """Python [1638]: 64-bit    Footprint: 20 GB (16384 bytes per page)
  20 GB      10 MB      6720 KB       5034    TOTAL

Auxiliary data:
    phys_footprint: 20 GB
"""
FOOTPRINT_NO_PID = ("footprint: Unable to find pid for process matching '999999'\n"
                    "footprint: Unable to find any processes matching the supplied process names or pids\n")
MODEL = "mlx-community/Fake-20B-4bit"
_FP = codec_heartbeat._FOOTPRINT


# ── heartbeat(): one at a time ──────────────────────────────────────────────


@pytest.fixture
def guard(monkeypatch, tmp_path):
    """heartbeat() with its lock and stamp in tmp_path and a counting stub cycle."""
    monkeypatch.setattr(codec_heartbeat, "HEARTBEAT_LOCK_PATH", str(tmp_path / "heartbeat.lock"))
    monkeypatch.setattr(codec_heartbeat, "HEARTBEAT_STAMP_PATH", str(tmp_path / "heartbeat_last.json"))
    out = types.SimpleNamespace(runs=0, entered=threading.Event(), release=threading.Event(), block=False,
                                stamp=tmp_path / "heartbeat_last.json", lock=tmp_path / "heartbeat.lock")

    def cycle():
        out.runs += 1
        out.entered.set()
        if out.block:
            assert out.release.wait(10)
        return []
    monkeypatch.setattr(codec_heartbeat, "_heartbeat_cycle", cycle)
    return out


def test_two_heartbeats_started_together_run_the_cycle_once(guard):
    guard.block = True
    start, results, done = threading.Barrier(2), [], threading.Event()

    def call():
        start.wait(5)
        results.append(codec_heartbeat.heartbeat())
        done.set()

    threads = [threading.Thread(target=call) for _ in range(2)]
    for t in threads:
        t.start()
    assert done.wait(5) and guard.entered.wait(5), "the second call returns while the first still runs"
    assert results == [None] and guard.runs == 1, "skipped at once while the lock is held"
    guard.release.set()
    for t in threads:
        t.join(5)
    assert sorted(results, key=str) == [None, []] and guard.runs == 1
    assert codec_heartbeat.heartbeat() is None and guard.runs == 1, "and the new stamp skips a third"


def test_a_fresh_stamp_skips_and_an_old_broken_or_future_stamp_runs(guard):
    half = codec_heartbeat.HEARTBEAT_INTERVAL_S / 2
    guard.stamp.write_text(json.dumps({"started_at": time.time() - 60, "pid": 1}))
    assert codec_heartbeat.heartbeat() is None and guard.runs == 0
    for stamp in (json.dumps({"started_at": time.time() - half - 5, "pid": 1}), "not json",
                  json.dumps({"started_at": time.time() + 3600, "pid": 1})):
        guard.stamp.write_text(stamp)
        assert codec_heartbeat.heartbeat() == []
    assert guard.runs == 3
    written = json.loads(guard.stamp.read_text())
    assert abs(written["started_at"] - time.time()) < 5 and written["pid"] == os.getpid()
    assert oct(guard.stamp.stat().st_mode & 0o777) == "0o600" and oct(guard.lock.stat().st_mode & 0o777) == "0o600"
    assert codec_heartbeat.heartbeat() is None and guard.runs == 3, "its own new stamp skips the next call"


def test_min_gap_zero_ignores_the_stamp_but_not_the_lock(guard):
    guard.stamp.write_text(json.dumps({"started_at": time.time(), "pid": 1}))
    assert codec_heartbeat.heartbeat(min_gap_s=0) == [] and guard.runs == 1
    fd = os.open(str(guard.lock), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # another process's heartbeat
        assert codec_heartbeat.heartbeat(min_gap_s=0) is None and guard.runs == 1
    finally:
        os.close(fd)
    assert codec_heartbeat.heartbeat(min_gap_s=0) == [] and guard.runs == 2


def test_both_loops_still_call_heartbeat_with_no_argument():
    import inspect
    import codec_dashboard
    assert "asyncio.to_thread(heartbeat)" in inspect.getsource(codec_dashboard._bg_heartbeat)
    assert "heartbeat()" in inspect.getsource(codec_heartbeat.run_daemon)


# ── alert_state.json: two writers ───────────────────────────────────────────


def test_merge_changes_writes_only_what_this_run_changed():
    base = {"a": 1, "gone": 1, "keep": 1, "nested": {"x": 1, "y": 1}}
    new = {"a": 2, "keep": 1, "added": 1, "nested": {"x": 2, "y": 1}}
    current = {"a": 1, "gone": 1, "keep": 9, "other": 7, "nested": {"x": 1, "y": 5, "z": 3}}
    assert codec_jsonstore.merge_changes(current, base, new) == {
        "a": 2, "keep": 9, "other": 7, "added": 1, "nested": {"x": 2, "y": 5, "z": 3}}
    assert codec_jsonstore.merge_changes("broken", base, new) == new, "a broken file: this run's state"
    assert codec_jsonstore.merge_changes({"other": 7}, {}, new) == dict(new, other=7)


@pytest.fixture
def two_writers(monkeypatch, tmp_path):
    """check_services_and_alert (Whisper probe can wait) and check_alerts (mail check can wait) on one
    alert_state.json in tmp_path, with no real probe, restart, mail or alert channel."""
    import codec_dispatch
    home = tmp_path / "codec"
    home.mkdir()
    state_path = home / "alert_state.json"
    state_path.write_text(json.dumps({"consecutive_failures": {"Whisper STT": 1}, "email_unread": 0,
                                      "down_since_Whisper STT": "2026-11-03T10:00:00"}))
    real = os.path.expanduser
    monkeypatch.setattr(os.path, "expanduser",
                        lambda p: str(home / p[len("~/.codec/"):]) if str(p).startswith("~/.codec/") else real(p))
    monkeypatch.setattr(codec_alerts, "ALERT_STATE_PATH", str(state_path))
    monkeypatch.setattr(codec_alerts, "ALERTS_SENT_PATH", str(home / "alerts_sent.json"))
    monkeypatch.setattr(codec_alerts, "_load_config", lambda: {"alerts": {}})
    w = types.SimpleNamespace(path=state_path, alerts=[], probe_wait=None, mail_wait=None,
                              in_probe=threading.Event(), in_mail=threading.Event())
    monkeypatch.setattr(codec_alerts, "send_alert", lambda level, msg, subject=None: w.alerts.append(msg))
    monkeypatch.setattr(codec_alerts, "_is_listening", lambda url, timeout=2: False)
    monkeypatch.setattr(codec_alerts, "_try_restart", lambda name: pytest.fail("no restart on a 2nd failure"))

    def probe(url, timeout=5):
        if ":8084/" not in url:
            return True
        w.in_probe.set()
        if w.probe_wait is not None:
            assert w.probe_wait.wait(10)
        return False
    monkeypatch.setattr(codec_alerts, "_check_service", probe)
    cfg = home / "hb_config.json"
    cfg.write_text(json.dumps({"heartbeat_alerts": [{"type": "email_check", "name": "Mail"}]}))
    monkeypatch.setattr(codec_heartbeat, "CONFIG_PATH", str(cfg))

    def mail(name, task, feature):
        w.in_mail.set()
        if w.mail_wait is not None:
            assert w.mail_wait.wait(10)
        return "Found 3 emails:\n* Ana: lunch"
    monkeypatch.setattr(codec_dispatch, "run_feature", mail)
    monkeypatch.setattr(codec_heartbeat.subprocess, "run", lambda *a, **k: None)  # no macOS banner
    return w


def _both_counters(w):
    state = json.loads(w.path.read_text())
    return state["consecutive_failures"]["Whisper STT"], state["email_unread"]


def test_alert_state_keeps_both_counters_when_the_service_check_saves_last(two_writers):
    w = two_writers
    w.probe_wait = threading.Event()
    a = threading.Thread(target=codec_alerts.check_services_and_alert)
    a.start()
    assert w.in_probe.wait(5)          # the service check loaded the file and waits in its probe
    assert codec_heartbeat.check_alerts() == ["Mail: 3 unread emails — Ana: lunch"]
    w.probe_wait.set()
    a.join(10)
    assert _both_counters(w) == (2, 3)
    assert w.alerts and "Whisper STT is not responding" in w.alerts[0]
    assert os.path.exists(str(w.path) + ".lock"), "written through codec_jsonstore.read_modify_write"


def test_alert_state_keeps_both_counters_when_the_mail_check_saves_last(two_writers):
    w = two_writers
    w.mail_wait = threading.Event()
    b = threading.Thread(target=codec_heartbeat.check_alerts)
    b.start()
    assert w.in_mail.wait(5)           # the mail check loaded the file and waits on the mail skill
    codec_alerts.check_services_and_alert()
    w.mail_wait.set()
    b.join(10)
    assert _both_counters(w) == (2, 3)
    assert json.loads(w.path.read_text())["down_since_Whisper STT"] == "2026-11-03T10:00:00"


def test_check_alerts_saves_to_the_same_file_as_codec_alerts(monkeypatch, tmp_path):
    """One path for both writers, so they share one lock, and tests/conftest.py's redirect of
    codec_alerts.ALERT_STATE_PATH keeps a real heartbeat cycle off the owner's alert_state.json."""
    state_path, elsewhere = tmp_path / "alert_state.json", tmp_path / "home"
    real = os.path.expanduser
    monkeypatch.setattr(os.path, "expanduser",
                        lambda p: str(elsewhere / p[2:]) if str(p).startswith("~/") else real(p))
    monkeypatch.setattr(codec_alerts, "ALERT_STATE_PATH", str(state_path))
    cfg = tmp_path / "hb_config.json"
    cfg.write_text(json.dumps({"heartbeat_alerts": [{"type": "disk_usage", "name": "Disk",
                                                     "threshold_pct": 101}]}))
    monkeypatch.setattr(codec_heartbeat, "CONFIG_PATH", str(cfg))
    state_path.write_text(json.dumps({"email_unread": 2}))
    assert codec_heartbeat.check_alerts() == []
    assert json.loads(state_path.read_text()) == {"email_unread": 2}
    assert os.path.exists(str(state_path) + ".lock") and not elsewhere.exists()


# ── draft_task.json: claimed by rename ──────────────────────────────────────


@pytest.fixture
def draft(monkeypatch, tmp_path):
    path = tmp_path / "draft_task.json"
    monkeypatch.setattr(codec_watcher, "TASK_FILE", str(path))
    return path


def test_two_watchers_claim_a_pending_draft_exactly_once(draft):
    for i in range(25):
        draft.write_text(json.dumps({"task": f"reply to Ana {i}", "ctx": "", "app": "Mail"}))
        start, got = threading.Barrier(2), []

        def watch():
            start.wait(5)
            got.append(codec_watcher.claim_draft())
        threads = [threading.Thread(target=watch) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
        tasks = [g for g in got if g]
        assert len(got) == 2 and len(tasks) == 1 and tasks[0]["task"] == f"reply to Ana {i}"
    assert sorted(os.listdir(draft.parent)) == [], "no draft and no claimed copy left"


def test_a_draft_claimed_while_the_writer_is_still_writing_is_read_whole(draft):
    f = open(draft, "w")                # codec.py writes the file in place
    f.write('{"task": "reply to Ana", "ct')
    f.flush()
    got = []
    t = threading.Thread(target=lambda: got.append(codec_watcher.claim_draft()))
    t.start()
    time.sleep(0.3)
    f.write('x": "", "app": "Mail"}')
    f.close()
    t.join(5)
    assert got == [{"task": "reply to Ana", "ctx": "", "app": "Mail"}]
    assert os.listdir(draft.parent) == []


def test_a_draft_that_never_becomes_json_is_dropped(draft, monkeypatch):
    monkeypatch.setattr(codec_watcher, "_CLAIM_READ_TRIES", 2)
    draft.write_text("{not json")
    assert codec_watcher.claim_draft() is None and os.listdir(draft.parent) == []
    assert codec_watcher.claim_draft() is None, "nothing pending"


def test_the_dashboard_watcher_and_the_hotkey_watcher_draft_a_task_once(draft, monkeypatch):
    import inspect
    import codec_dashboard
    drafted = []
    monkeypatch.setattr(codec_watcher, "handle_draft", lambda task, ctx, app: drafted.append(task))
    stop = threading.Event()

    def hotkey():                       # codec_watcher.py's own loop, as PM2 codec-hotkey runs it
        while not stop.is_set():
            data = codec_watcher.claim_draft()
            if data:
                drafted.append(data["task"])
            time.sleep(0.001)

    class _Stop(Exception):
        pass
    ticks = []

    async def fake_sleep(s):
        ticks.append(s)
        if len(ticks) > 200:
            raise _Stop
        await asyncio.sleep(0.001)
    monkeypatch.setattr(codec_dashboard, "asyncio", types.SimpleNamespace(
        sleep=fake_sleep, to_thread=asyncio.to_thread, CancelledError=asyncio.CancelledError))
    t = threading.Thread(target=hotkey)
    t.start()
    try:
        draft.write_text(json.dumps({"task": "reply to Ana", "ctx": "", "app": "Mail"}))
        with pytest.raises(_Stop):
            asyncio.run(codec_dashboard._bg_watcher())
    finally:
        stop.set()
        t.join(5)
    assert drafted == ["reply to Ana"]
    main_loop = inspect.getsource(codec_watcher).split('if __name__ == "__main__":')[-1]
    assert "claim_draft()" in main_loop and "os.unlink(TASK_FILE)" not in main_loop


# ── memory_check ────────────────────────────────────────────────────────────


@pytest.fixture
def mem(monkeypatch, tmp_path):
    """memory_check with fixture tool outputs, a tmp config and state, and recorded alerts."""
    m = types.SimpleNamespace(swap=SWAP_HIGH, pressure=PRESSURE, footprint=None, calls=[], alerts=[],
                              state=tmp_path / "memory_check.json", cfg=tmp_path / "config.json")
    m.cfg.write_text(json.dumps({"llm_model": MODEL,
                                 "extra_models": [{"id": MODEL, "size_gb": 20}]}))
    monkeypatch.setattr(codec_heartbeat, "CONFIG_PATH", str(m.cfg))
    monkeypatch.setattr(codec_heartbeat, "MEMORY_STATE_PATH", str(m.state))
    monkeypatch.setattr(codec_alerts, "ALERTS_SENT_PATH", str(tmp_path / "alerts_sent.json"))
    monkeypatch.setattr(codec_alerts, "send_alert",
                        lambda level, msg, subject=None: m.alerts.append((level, msg)))

    def cmd(args):
        m.calls.append(list(args))
        return {"sysctl": m.swap, "memory_pressure": m.pressure,
                "footprint": m.footprint}[os.path.basename(args[0])]
    monkeypatch.setattr(codec_heartbeat, "_mem_cmd", cmd)
    return m


def _model_app(pid=4242, status="online"):
    return [{"name": "qwen3.6", "pid": pid, "pm2_env": {"status": status}}]


def test_memory_parsers_read_the_fixture_outputs_of_the_three_tools():
    assert codec_heartbeat.parse_swapusage(SWAP_HIGH) == (10218.0, 11264.0)
    assert codec_heartbeat.parse_swapusage("vm.swapusage: total = 2.00G  used = 512.00M  free = 1.50G") == (512.0, 2048.0)
    assert codec_heartbeat.parse_swapusage("vm.swapusage: total = 0.00M  used = 0.00M  free = 0.00M") == (0.0, 0.0)
    assert codec_heartbeat.parse_memory_pressure(PRESSURE) == 75
    assert codec_heartbeat.parse_footprint(FOOTPRINT_BYTES.format(pid=1638, n=21156282160)) == 21156282160
    assert codec_heartbeat.parse_footprint(FOOTPRINT_FORMATTED) == 20 * 1024 ** 3
    for junk in (None, "", FOOTPRINT_NO_PID, "sysctl: unknown oid 'vm.swapusage'"):
        assert codec_heartbeat.parse_footprint(junk) is None
        assert codec_heartbeat.parse_swapusage(junk) is None
        assert codec_heartbeat.parse_memory_pressure(junk) is None


def test_mem_pressure_alerts_on_the_second_tick_with_swap_above_80_percent(mem):
    first = codec_heartbeat.memory_check([], now_ts=1_800_000_000)
    assert first["level"] == "high" and first["swap"] == "high" and first["swap_pct"] == 90.7
    assert first["high_ticks"] == 1 and first["free_pct"] == 75 and mem.alerts == []
    assert first["model_footprint"] == "unknown", "no model server in the PM2 list"
    assert not any(c[0].endswith("footprint") for c in mem.calls), "no pid, no footprint call"
    codec_heartbeat.memory_check([])
    assert len(mem.alerts) == 1 and mem.alerts[0][0] == "warning"
    assert "swap 91% used (10.0 of 11.0 GB)" in mem.alerts[0][1]
    codec_heartbeat.memory_check([])
    assert len(mem.alerts) == 1, "alert_once: not again within 6 h"
    assert "mem_pressure" in codec_alerts.open_problems()
    mem.swap = SWAP_LOW
    ok = codec_heartbeat.memory_check([])
    assert ok["level"] == "ok" and ok["high_ticks"] == 0
    assert mem.alerts[-1] == ("recovery", "CODEC RECOVERED: memory is back to normal.")
    assert oct(mem.state.stat().st_mode & 0o777) == "0o600"


def test_mem_pressure_alerts_on_a_footprint_above_1_5x_the_model(mem):
    mem.swap, mem.footprint = SWAP_LOW, FOOTPRINT_BYTES.format(pid=4242, n=25_000_000_000)
    fine = codec_heartbeat.memory_check(_model_app())
    assert fine["model_footprint"] == "ok" and fine["footprint_ratio"] == 1.25 and fine["model_gb"] == 20.0
    assert [_FP, "-p", "4242", "-f", "bytes", "--noCategories"] in mem.calls
    mem.footprint = FOOTPRINT_BYTES.format(pid=4242, n=32_000_000_000)
    assert codec_heartbeat.memory_check(_model_app())["high_ticks"] == 1 and mem.alerts == []
    high = codec_heartbeat.memory_check(_model_app())
    assert high["level"] == "high" and high["swap"] == "ok" and high["model_footprint_gb"] == 32.0
    assert len(mem.alerts) == 1
    assert "the model server holds 32.0 GB, 1.6x its 20.0 GB model" in mem.alerts[0][1]
    assert "pm2 restart qwen3.6" in mem.alerts[0][1] and "swap" not in mem.alerts[0][1]
    assert "gives its memory back. 75% of memory is free." in mem.alerts[0][1]


def test_swap_alert_with_a_normal_size_model_server_does_not_offer_its_restart(mem):
    mem.footprint = FOOTPRINT_BYTES.format(pid=4242, n=21_000_000_000)   # 1.05x the 20 GB model
    codec_heartbeat.memory_check(_model_app())
    high = codec_heartbeat.memory_check(_model_app())
    assert high["swap"] == "high" and high["model_footprint"] == "ok" and len(mem.alerts) == 1
    msg = mem.alerts[0][1]
    assert "pm2 restart" not in msg and "gives its memory back" not in msg
    assert "normal size (1.05x its model), so restarting it gives back little" in msg
    assert msg.endswith("quitting apps you do not use helps more. 75% of memory is free.")


def test_swap_alert_without_a_model_reading_says_restart_helps_only_if_grown(mem):
    codec_heartbeat.memory_check([])
    codec_heartbeat.memory_check([])
    assert "restarting the model server (pm2 restart qwen3.6) helps only if it has grown" in mem.alerts[0][1]


def test_an_unreadable_footprint_reports_unknown_and_never_alerts(mem):
    mem.swap = None                                 # sysctl failed or is missing
    mem.footprint = None                            # footprint exited with an error for this pid
    for procs in (_model_app(), _model_app(pid=0), _model_app(status="stopped"), [], [{"name": "x"}]):
        for _ in range(3):
            r = codec_heartbeat.memory_check(procs)
            assert r["level"] == "unknown" and r["model_footprint"] == "unknown" and r["high_ticks"] == 0
    assert mem.alerts == []
    mem.footprint = FOOTPRINT_BYTES.format(pid=4242, n=40_000_000_000)
    mem.cfg.write_text(json.dumps({"llm_model": "/no/such/model/dir"}))   # no model size: no ratio
    r = codec_heartbeat.memory_check(_model_app())
    assert r["model_footprint"] == "unknown" and r["model_footprint_gb"] == 40.0 and r["footprint_ratio"] is None
    assert mem.alerts == []


def test_memory_limits_come_from_config_within_bounds():
    limits = codec_heartbeat._memory_limits
    assert limits({}) == (80.0, 1.5)
    assert limits({"memory_alert": {"swap_pct": 95, "footprint_ratio": 2}}) == (95.0, 2.0)
    assert limits({"memory_alert": {"swap_pct": 10, "footprint_ratio": 99}}) == (50.0, 5.0)
    assert limits({"memory_alert": {"swap_pct": True, "footprint_ratio": "2"}}) == (80.0, 1.5)
    assert limits({"memory_alert": "off"}) == (80.0, 1.5)


def test_the_memory_model_size_is_read_from_its_weights_on_disk(monkeypatch, tmp_path):
    import codec_models
    snap = tmp_path / "hub" / ("models--" + MODEL.replace("/", "--")) / "snapshots" / "abc"
    snap.mkdir(parents=True)
    (snap / "model-1.safetensors").write_bytes(b"x" * 3000)
    (snap / "model-2.safetensors").write_bytes(b"x" * 2000)
    (snap / "config.json").write_text("{}")
    monkeypatch.setattr(codec_models, "HF_CACHE", str(tmp_path / "hub"))
    assert codec_heartbeat._model_bytes({"llm_model": MODEL}) == 5000
    assert codec_heartbeat._model_bytes({"llm_model": "mlx-community/Not-There"}) is None
    assert codec_heartbeat._model_bytes({"llm_model": str(snap)}) == 5000


# ── GET /api/status ─────────────────────────────────────────────────────────


def test_api_status_shows_the_memory_figure(mem, monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routes.health as health
    monkeypatch.setattr(health, "CONFIG_PATH", str(tmp_path / "no-config.json"))
    app = FastAPI()
    app.include_router(health.router)
    client = TestClient(app)
    assert client.get("/api/status").json()["memory"] == {"level": "unknown", "checked_at": None}
    codec_heartbeat.memory_check([])
    body = client.get("/api/status").json()
    assert body["memory"]["level"] == "high" and body["memory"]["swap_pct"] == 90.7
    assert body["memory"]["swap_used_mb"] == 10218.0 and body["memory"]["model_footprint"] == "unknown"
    assert body["memory"]["checked_at"] and "alive" in body and "config" in body
