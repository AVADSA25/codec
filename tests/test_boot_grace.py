"""Boot grace (P-059, docs/BOOT-GRACE-DESIGN.md).

After a boot or deploy the heartbeat must not count, restart or report a
service whose PM2 app started under 5 minutes ago: the model server binds its
port only once its model is loaded, and a restart there began a second load.
After the grace the old rule holds: one failed probe restarts, with the same
5-minute cooldown. Every test uses a fake clock and a fake `pm2 jlist`; no
test runs pm2, probes a real service or sleeps.
"""
from __future__ import annotations

import asyncio
import json
import types

import pytest
import requests

import codec_alerts
# At collection, like the other dashboard tests: some skills put ~/codec-repo
# first on sys.path when they load, and an import inside the test then got the
# main checkout's codec_dashboard instead of this one.
import codec_dashboard
import codec_heartbeat
import codec_models

T = 1_800_000_000.0  # the fake clock, epoch seconds
APPS = ("qwen3.6", "whisper-stt", "kokoro-82m", "codec-dashboard")


def _jlist(started_at, *names, status="online"):
    """A `pm2 jlist` list: every app named here last started at `started_at`."""
    return [{"name": n, "pm2_env": {"status": status, "pm_uptime": int(started_at * 1000),
                                    "autorestart": True, "restart_time": 0}}
            for n in names or APPS]


@pytest.fixture
def svc(monkeypatch, tmp_path):
    """check_services_and_alert with fake config, state, probes, restarts and alerts."""
    out = {"restarts": [], "alerts": [], "state": {}, "down": set()}
    monkeypatch.setattr(codec_alerts, "_load_config", lambda: {"alerts": {}})
    monkeypatch.setattr(codec_alerts, "_load_state", lambda: dict(out["state"]))
    monkeypatch.setattr(codec_alerts, "_save_state", lambda s: out.update(state=s))
    monkeypatch.setattr(codec_alerts, "ALERTS_SENT_PATH", str(tmp_path / "alerts_sent.json"))
    monkeypatch.setattr(codec_alerts, "send_alert",
                        lambda level, msg, subject=None: out["alerts"].append((level, msg)))
    monkeypatch.setattr(codec_alerts, "_try_restart", lambda name: out["restarts"].append(name) or True)
    monkeypatch.setattr(codec_alerts, "_check_service",
                        lambda url, timeout=5: not any(f":{p}/" in url for p in out["down"]))
    monkeypatch.setattr(codec_alerts, "_is_listening", lambda url, timeout=2: False)
    monkeypatch.setattr(codec_alerts.time, "sleep", lambda s: None)
    return out


# ── the rule ────────────────────────────────────────────────────────────────


def test_an_online_app_is_loading_for_its_first_300_seconds_only():
    assert codec_models.pm2_app_loading(_jlist(T - 10), "qwen3.6", T) is True
    assert codec_models.pm2_app_loading(_jlist(T - 299), "qwen3.6", T) is True
    assert codec_models.pm2_app_loading(_jlist(T - 300), "qwen3.6", T) is False
    assert codec_models.pm2_app_loading(_jlist(T - 10, status="errored"), "qwen3.6", T) is False
    assert codec_models.pm2_app_loading(_jlist(T - 10), "not-there", T) is False
    for junk in (None, [], {"qwen3.6": 1}, ["qwen3.6"], 7,
                 [{"name": "qwen3.6", "pm2_env": {"status": "online", "pm_uptime": "soon"}}]):
        assert codec_models.pm2_app_loading(junk, "qwen3.6", T) is False


def test_the_cloud_fallback_check_uses_the_same_rule(monkeypatch):
    import time
    monkeypatch.setattr(codec_models.shutil, "which", lambda name: "/fake/pm2")
    jlist = _jlist(time.time() - 10, "my-llm")
    monkeypatch.setattr(codec_models.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(stdout=json.dumps(jlist)))
    assert codec_models._server_loading({"model_pm2_process": "my-llm"}) is True
    assert codec_models._server_loading({}) is False  # qwen3.6 is not in that list


# ── check_services_and_alert ────────────────────────────────────────────────


def test_every_service_silent_10s_after_a_boot_restarts_and_counts_nothing(svc):
    svc["down"] = {"8083", "8084", "8085", "8090"}
    procs = _jlist(T - 10)
    for dt in (0, 60, 120):  # still inside the grace on every pass
        codec_alerts.check_services_and_alert(procs, now_ts=T + dt)
    assert svc["restarts"] == []
    assert svc["alerts"] == []
    assert not any(svc["state"]["consecutive_failures"].values())
    assert not [k for k in svc["state"] if k.startswith("down_since_")]


def test_one_failed_probe_400s_after_start_restarts_once_then_alerts(svc):
    svc["down"] = {"8083"}
    procs = _jlist(T - 400)
    codec_alerts.check_services_and_alert(procs, now_ts=T)
    assert svc["restarts"] == ["qwen3.6"]  # LLM and Vision share it: restarted once
    assert svc["state"]["consecutive_failures"]["LLM (Qwen)"] == 1
    assert svc["alerts"] == []
    codec_alerts.check_services_and_alert(procs, now_ts=T + 1800)
    assert svc["restarts"] == ["qwen3.6"]
    assert any(level == "critical" and "LLM (Qwen) is not responding" in msg
               for level, msg in svc["alerts"])


def test_the_restart_cooldown_is_unchanged(svc):
    procs = _jlist(T - 400)
    for dt, down in ((0, {"8083"}), (60, set()), (120, {"8083"}), (400, set()), (420, {"8083"})):
        svc["down"] = down
        codec_alerts.check_services_and_alert(procs, now_ts=T + dt)
        if dt == 120:
            assert svc["restarts"] == ["qwen3.6"], "120 s after a restart the cooldown holds"
    assert svc["restarts"] == ["qwen3.6", "qwen3.6"]


def test_no_grace_without_a_list_or_for_an_errored_app(svc):
    svc["down"] = {"8083"}
    codec_alerts.check_services_and_alert(now_ts=T)  # no jlist: every failure counts, as before
    assert svc["restarts"] == ["qwen3.6"]
    svc["state"] = {}
    codec_alerts.check_services_and_alert(_jlist(T - 10, status="errored"), now_ts=T)
    assert svc["restarts"] == ["qwen3.6", "qwen3.6"]


# ── check_system_health: the service_down audit event ───────────────────────


def _health(monkeypatch, procs):
    events = []

    def no_answer(url, timeout=5):
        raise requests.ConnectionError("refused")
    monkeypatch.setattr(codec_heartbeat.requests, "get", no_answer)
    monkeypatch.setattr(codec_heartbeat, "log_event", lambda event, *a, **k: events.append((event, k)))
    codec_heartbeat.check_system_health(procs, now_ts=T)
    return [k["extra"]["service"] for event, k in events if event == "service_down"]


def test_no_service_down_event_10s_after_start_but_one_per_service_at_400s(monkeypatch):
    assert _health(monkeypatch, _jlist(T - 10)) == []
    assert sorted(_health(monkeypatch, _jlist(T - 400))) == ["Dashboard", "Kokoro", "LLM/Vision", "Whisper"]
    assert codec_heartbeat._check_one_service("x", "http://localhost:1/", starting=True) == ("x", "starting")


def test_the_heartbeat_hands_its_one_jlist_to_both_checks(monkeypatch):
    import codec_backup
    import codec_checkin
    jlist, seen = _jlist(T - 10), {}
    stubs = {
        "_pm2_jlist": lambda: seen.setdefault("jlist_calls", []).append(1) or jlist,
        "check_system_health": lambda procs=None: seen.update(health=procs),
        "check_pm2_restart_storms": lambda procs=None: [],
        "check_errored_codec_apps": lambda procs=None: [],
        "maybe_send_daily_status": lambda now=None, procs=None: False,
        "check_memory_stats": lambda: None,
        "check_pending_tasks": lambda: [],
        "check_alerts": lambda: [],
        "backup_memory_db": lambda: None,
        "log_event": lambda *a, **k: None,
    }
    for name, fn in stubs.items():
        monkeypatch.setattr(codec_heartbeat, name, fn)
    monkeypatch.setattr(codec_heartbeat, "_last_cleanup", None)
    monkeypatch.setattr(codec_alerts, "check_services_and_alert", lambda procs=None: seen.update(alerts=procs))
    monkeypatch.setattr(codec_checkin, "run_checkin", lambda: "skipped")
    monkeypatch.setattr(codec_backup, "run_retention", lambda: {})
    codec_heartbeat.heartbeat()
    assert seen["jlist_calls"] == [1]
    assert seen["health"] is jlist and seen["alerts"] is jlist


# ── the first tick waits 120 s ──────────────────────────────────────────────


class _Stop(Exception):
    """Leaves a heartbeat loop at its second sleep."""


def test_the_heartbeat_daemon_waits_120s_before_its_first_tick(monkeypatch):
    ticks, slept = [], []
    monkeypatch.setattr(codec_heartbeat, "heartbeat", lambda: ticks.append(1))

    def fake_sleep(s):
        slept.append((s, len(ticks)))
        if len(slept) == 2:
            raise _Stop
    monkeypatch.setattr(codec_heartbeat.time, "sleep", fake_sleep)
    with pytest.raises(_Stop):
        codec_heartbeat.run_daemon(20)
    assert slept == [(120, 0), (1200, 1)]


def test_the_dashboard_heartbeat_waits_120s_before_its_first_tick(monkeypatch):
    ticks, slept = [], []
    monkeypatch.setattr(codec_heartbeat, "heartbeat", lambda: ticks.append(1))
    monkeypatch.setitem(codec_dashboard._bg_status, "heartbeat",
                        {"running": False, "last_tick": None, "errors": 0})

    async def fake_sleep(s):
        slept.append((s, len(ticks)))
        if len(slept) == 2:
            raise _Stop
    monkeypatch.setattr(codec_dashboard, "asyncio", types.SimpleNamespace(
        sleep=fake_sleep, to_thread=asyncio.to_thread, CancelledError=asyncio.CancelledError))
    with pytest.raises(_Stop):
        asyncio.run(codec_dashboard._bg_heartbeat())
    assert slept == [(120, 0), (1800, 1)]
