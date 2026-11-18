"""codec_model_guard: the model server memory guard (docs/MODEL-MEMORY-GUARD-DESIGN.md).

Every reading is a fixture: no real footprint, PM2, launchctl or model server is touched,
and the restart is replaced in every test."""
import contextlib
import fcntl
import json
import os
import sys
import types

import pytest

import codec_model_guard as guard

GB = 1e9
MODEL = 20.4 * GB             # the Studio's Qwen3.6-35B-A3B-4bit
RAM = 68719476736             # 64 GB Mac
ALLOWED_EXTRA = {
    "model_memory_high": {"footprint_gb", "model_gb", "ram_gb", "swap_pct", "ratio", "line"},
    "model_guard_restart": {"result", "supervisor", "before_gb", "after_gb", "seconds", "waited_s"},
    "model_guard_held": {"reason", "footprint_gb"},
}


class Rig:
    """Readings in, restarts / alerts / audit lines out."""

    def __init__(self, monkeypatch, tmp_path):
        self.mp, self.tmp = monkeypatch, tmp_path
        self.cfg = {"llm_base_url": "http://127.0.0.1:8083/v1",
                    "llm_model": "mlx-community/Qwen3.6-35B-A3B-4bit"}
        self.footprint = 21 * GB
        self.procs = [{"name": "qwen3.6", "pm2_env": {"status": "online", "pm_uptime": 0}}]
        self.restarts, self.alerts, self.audits = [], [], []
        self.restart_ok = True
        self.switch_free = True
        self.now = 1_800_000_000.0
        monkeypatch.setenv("MODEL_GUARD_ENABLED", "true")
        monkeypatch.setenv("CODEC_MODEL_GUARD_DIR", str(tmp_path / "guard"))
        monkeypatch.setattr(guard, "VOICE_MARKER", str(tmp_path / "voice_session.json"))
        for name, value in (("_lock_fd", None), ("_restart_failed", False), ("_ram_bytes", None)):
            monkeypatch.setattr(guard, name, value)
        monkeypatch.setattr(guard, "_model_sizes", {})
        monkeypatch.setattr(guard, "_load_config", lambda: self.cfg)
        monkeypatch.setattr(guard, "read", self._read)
        monkeypatch.setattr(guard, "_restart", self._restart)
        monkeypatch.setattr(guard, "_switch_lock", self._lock)
        monkeypatch.setattr(guard, "_alert", lambda kind, key, level, msg: self.alerts.append((kind, key, msg)))
        monkeypatch.setattr(guard, "_audit", lambda ev, lv, msg, extra: self.audits.append((ev, extra)))
        monkeypatch.setattr(guard, "_supervisor", lambda: "pm2")
        monkeypatch.delitem(sys.modules, "codec_vibe_agent", raising=False)
        # Every figure comes from _read: never the Mac's own pm2 or model server.
        import codec_heartbeat as hb
        monkeypatch.setattr(hb, "_pm2_jlist", lambda *a, **k: pytest.fail("real pm2 jlist"))
        monkeypatch.setattr(hb, "_mem_cmd", lambda *a, **k: pytest.fail("real footprint"))

    def _read(self, cfg):
        if self.footprint is None:
            return None
        return {"footprint": self.footprint, "model": MODEL, "ram": RAM, "swap_pct": 12.0,
                "procs": self.procs, "app": "qwen3.6"}

    def _restart(self):
        self.restarts.append(self.now)
        self.footprint = 21 * GB if self.restart_ok else self.footprint
        return self.restart_ok, "detail"

    @contextlib.contextmanager
    def _lock(self):
        yield self.switch_free

    def tick(self, gb=None, advance=60):
        if gb is not None:
            self.footprint = gb * GB
        result = guard.tick(now=self.now)
        self.now += advance
        return result

    def state(self):
        with open(guard.state_path()) as f:
            return json.load(f)

    def events(self, name):
        return [extra for ev, extra in self.audits if ev == name]


@pytest.fixture
def rig(monkeypatch, tmp_path):
    return Rig(monkeypatch, tmp_path)


def test_lines_on_the_studio_and_on_a_16_gb_mac():
    s = guard.settings({})
    studio = guard.lines(MODEL, RAM, s)
    assert round(studio["alert"] / GB, 1) == 30.6
    assert round(studio["restart"] / GB, 1) == 48.1
    assert round(studio["urgent"] / GB, 1) == 55.0
    small = guard.lines(4.3 * GB, 17179869184, s)
    assert round(small["restart"] / GB, 2) == 10.75 and round(small["urgent"] / GB, 1) == 13.7


def test_normal_size_does_nothing(rig):
    assert rig.tick(21)["action"] == "ok"
    assert rig.restarts == [] and rig.alerts == [] and rig.audits == []


def test_above_the_alert_line_alerts_once(rig):
    rig.tick(31)
    rig.tick(32)
    assert [a[:2] for a in rig.alerts] == [("once", "model_memory")]
    assert len(rig.events("model_memory_high")) == 1
    assert "CODEC restarts it at 48 GB" in rig.alerts[0][2]
    assert rig.restarts == []


def test_restart_line_needs_two_readings_in_a_row(rig):
    assert rig.tick(49)["action"] == "alert"
    assert rig.restarts == []
    assert rig.tick(49)["action"] == "restarted"
    assert len(rig.restarts) == 1
    assert rig.events("model_guard_restart")[0]["result"] == "ok"
    assert rig.alerts[-1][0] == "restarted" and "It holds 21 GB again" in rig.alerts[-1][2]


def test_urgent_line_restarts_at_once(rig):
    assert rig.tick(56)["action"] == "restarted"


def test_unknown_reading_does_nothing(rig):
    rig.footprint = None
    assert rig.tick()["action"] == "no_reading"
    assert not os.path.exists(guard.state_path())


def test_back_below_the_alert_line_sends_the_recovery(rig):
    rig.tick(31)
    rig.tick(21)
    assert rig.alerts[-1][:2] == ("resolved", "model_memory")


def test_a_voice_call_holds_the_restart_for_at_most_max_defer(rig):
    open(guard.VOICE_MARKER, "w").close()
    rig.tick(49)
    assert rig.tick(49)["reason"] == "voice_call"
    assert rig.tick(49)["reason"] == "voice_call"          # 60 s held
    assert rig.tick(49)["action"] == "restarted"            # 120 s: restart with the call going
    assert "after waiting 2 minutes for the voice call" in rig.alerts[-1][2]
    assert len(rig.events("model_guard_held")) == 1
    assert rig.events("model_guard_restart")[0]["waited_s"] == 120


def test_a_live_vibe_run_holds_the_restart(rig, monkeypatch):
    vibe = types.ModuleType("codec_vibe_agent")
    vibe.live_run = lambda: {"project": "p", "run": "r1", "status": "running"}
    monkeypatch.setitem(sys.modules, "codec_vibe_agent", vibe)
    rig.tick(49)
    assert rig.tick(49)["reason"] == "vibe_run"


def test_a_call_ending_lets_the_restart_go_at_the_next_tick(rig):
    open(guard.VOICE_MARKER, "w").close()
    rig.tick(49)
    rig.tick(49)
    os.remove(guard.VOICE_MARKER)
    assert rig.tick(49)["action"] == "restarted"
    assert "for the voice call" in rig.alerts[-1][2]


def test_an_image_job_does_not_hold_the_restart(rig, monkeypatch):
    import codec_models
    monkeypatch.setattr(codec_models, "_image_busy", lambda: "an image job is running")
    rig.tick(49)
    assert rig.tick(49)["action"] == "restarted"


def test_the_start_grace_holds_below_the_urgent_line_only(rig):
    rig.procs[0]["pm2_env"]["pm_uptime"] = (rig.now - 60) * 1000   # started a minute ago
    rig.tick(49)
    assert rig.tick(49)["reason"] == "starting"
    assert rig.tick(56)["action"] == "restarted"


def test_a_model_switch_holding_the_lock_skips_the_tick(rig):
    rig.switch_free = False
    rig.tick(56)
    assert rig.restarts == []


def test_the_third_restart_in_an_hour_is_held_with_one_critical_alert(rig):
    rig.tick(56)
    rig.tick(56)
    assert len(rig.restarts) == 2
    assert rig.tick(56)["reason"] == "budget"
    assert rig.tick(56)["reason"] == "budget"
    assert len(rig.restarts) == 2
    assert [a[1] for a in rig.alerts].count("model_memory_budget") == 1
    assert len(rig.events("model_guard_held")) == 1


def test_a_failed_restart_stops_further_restarts(rig):
    rig.restart_ok = False
    assert rig.tick(56)["action"] == "restart_failed"
    assert [a[1] for a in rig.alerts if a[0] == "once"][-1] == "model_memory_restart_failed"
    assert rig.tick(56)["reason"] == "restart_failed"
    assert len(rig.restarts) == 1


def test_restart_off_alerts_only(rig):
    rig.cfg["model_guard"] = {"restart": False}
    rig.tick(56)
    assert rig.restarts == []
    assert "Automatic restart is off" in rig.alerts[0][2]


def test_while_on_a_cloud_model_it_alerts_only(rig):
    rig.cfg["llm_base_url"] = "https://api.example.com/v1"
    assert rig.tick(56)["reason"] == "cloud_active"
    assert rig.restarts == []


def test_17_november_replay(rig):
    """Minute readings estimated from 17 Nov: normal, then the in-place swaps."""
    actions = [rig.tick(gb)["action"] for gb in (21.4, 21.4, 33.0, 50.0, 52.0, 21.0)]
    assert actions == ["ok", "ok", "alert", "alert", "restarted", "ok"]
    assert [a[0] for a in rig.alerts] == ["once", "restarted"]   # no extra recovery message


def test_launchd_restarts_only_through_restart_for_switch(monkeypatch):
    import codec_models
    calls = []
    monkeypatch.setattr(codec_models, "_supervisor_kind", lambda cfg=None: "launchd")
    monkeypatch.setattr(codec_models, "restart_launchd", lambda: calls.append("launchd") or (True, ""))
    monkeypatch.setattr(codec_models, "restart_server", lambda *a, **k: pytest.fail("pm2 restart on launchd"))
    assert guard._restart() == (True, "")
    assert calls == ["launchd"]


def test_settings_defaults_clamping_and_wrong_types():
    assert {k: v for k, v in guard.settings({}).items() if k != "alert_ratio"} == guard.DEFAULTS
    s = guard.settings({"model_guard": {"interval_s": 5, "restart_ratio": 99, "restart": "no",
                                        "max_defer_s": "long", "restarts_per_hour": 3.7}})
    assert s["interval_s"] == 30 and s["restart_ratio"] == 6.0 and s["restart"] is True
    assert s["max_defer_s"] == 120 and s["restarts_per_hour"] == 3
    assert guard.settings({"memory_alert": {"footprint_ratio": 2.0}})["alert_ratio"] == 2.0


def test_the_kill_switch_reads_and_writes_nothing(monkeypatch, tmp_path):
    import subprocess
    monkeypatch.setenv("MODEL_GUARD_ENABLED", "false")
    monkeypatch.setenv("CODEC_MODEL_GUARD_DIR", str(tmp_path))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("subprocess while off"))
    monkeypatch.setattr(guard, "read", lambda cfg: pytest.fail("read while off"))
    assert guard.tick()["action"] == "off"
    assert os.listdir(tmp_path) == []


def test_config_enabled_false_reads_nothing(rig, monkeypatch):
    rig.cfg["model_guard"] = {"enabled": False}
    monkeypatch.setattr(guard, "read", lambda cfg: pytest.fail("read while disabled"))
    assert rig.tick()["action"] == "off"


def test_a_second_guard_on_the_mac_skips(rig):
    os.makedirs(os.path.dirname(guard.lock_path()), exist_ok=True)
    fd = os.open(guard.lock_path(), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert rig.tick(56)["action"] == "other_guard"
        assert rig.restarts == []
    finally:
        os.close(fd)


def test_audit_lines_alerts_and_state_hold_figures_only(rig):
    open(guard.VOICE_MARKER, "w").close()
    for gb in (31, 49, 49, 49, 49, 56, 56, 56):
        rig.tick(gb)
    assert rig.audits
    for event, extra in rig.audits:
        assert set(extra) <= ALLOWED_EXTRA[event], event
    text = json.dumps([rig.alerts, rig.audits, rig.state()])
    for leak in ("Qwen", "mlx-community", "/Users/", "voice_session"):
        assert leak not in text
    assert oct(os.stat(guard.state_path()).st_mode & 0o777) == "0o600"


def test_status_reads_the_state_file(rig):
    rig.tick(31)
    st = guard.status()
    assert st["footprint_gb"] == 31.0 and st["line"] == "alert" and "restarts" not in st


def test_the_dashboard_loop_runs_tick_in_a_worker_thread(monkeypatch):
    import asyncio
    import threading
    on_loop_thread = []
    monkeypatch.setattr(guard, "FIRST_TICK_DELAY_S", 0)
    monkeypatch.setattr(guard, "tick", lambda: on_loop_thread.append(
        threading.current_thread() is threading.main_thread()) or {"interval_s": 0.01})
    status = {"running": False, "last_tick": None, "errors": 0}

    async def main():
        task = asyncio.create_task(guard.run_loop(status))
        while len(on_loop_thread) < 2:
            await asyncio.sleep(0.01)
        assert status["running"]
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(main())
    assert on_loop_thread[:2] == [False, False]
    assert status["last_tick"] and not status["running"]
