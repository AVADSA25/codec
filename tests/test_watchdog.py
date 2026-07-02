"""2026-07 log review — watchdog additions.

Covers:
- codec_alerts._check_service tcp:// probes
- codec_alerts.check_services_and_alert: URL dedupe + extra_services
  (monitored but NEVER auto-restarted)
- codec_heartbeat.check_pm2_restart_storms: crash-loop detection with
  cron-job exclusion and first-run baseline
"""
from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import codec_alerts
import codec_heartbeat


# ── _check_service tcp:// ─────────────────────────────────────────────────────


def test_check_service_tcp_up():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert codec_alerts._check_service(f"tcp://127.0.0.1:{port}") is True
    finally:
        srv.close()


def test_check_service_tcp_down():
    # Grab a free port, then close it so nothing listens there.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert codec_alerts._check_service(f"tcp://127.0.0.1:{port}", timeout=1) is False


# ── check_services_and_alert: dedupe + extras ────────────────────────────────


@pytest.fixture
def alert_harness(monkeypatch, tmp_path):
    """Isolate check_services_and_alert: fake config/state, capture probes,
    restarts, and alerts; neutralize sleeps and the pm2 cwd check."""
    calls = {"probes": [], "restarts": [], "alerts": []}
    cfg = {"alerts": {}}
    state_holder = {"state": {}}

    monkeypatch.setattr(codec_alerts, "_load_config", lambda: cfg)
    monkeypatch.setattr(codec_alerts, "_load_state",
                        lambda: dict(state_holder["state"]))
    monkeypatch.setattr(codec_alerts, "_save_state",
                        lambda s: state_holder.update(state=s))
    monkeypatch.setattr(codec_alerts, "send_alert",
                        lambda level, msg, subject=None: calls["alerts"].append((level, msg)))
    monkeypatch.setattr(codec_alerts, "_try_restart",
                        lambda pm2_name: calls["restarts"].append(pm2_name) or True)
    monkeypatch.setattr(codec_alerts.time, "sleep", lambda s: None)
    # Neutralize the disk + pm2-cwd tail checks (subprocess)
    monkeypatch.setattr(codec_alerts.subprocess, "check_output",
                        lambda *a, **kw: b"")
    return cfg, state_holder, calls


def test_shared_url_probed_once(alert_harness, monkeypatch):
    """LLM and Vision resolve to the same URL — exactly one probe fires."""
    cfg, state_holder, calls = alert_harness

    def probe(url, timeout=5):
        calls["probes"].append(url)
        return True

    monkeypatch.setattr(codec_alerts, "_check_service", probe)
    codec_alerts.check_services_and_alert()
    # default ports: llm and vision both 8083 → one probe for that URL
    assert len(calls["probes"]) == len(set(calls["probes"])), calls["probes"]


def test_extra_services_monitored_but_never_restarted(alert_harness, monkeypatch):
    """A down extra service alerts after 2 consecutive failures and is
    NEVER pm2-restarted."""
    cfg, state_holder, calls = alert_harness
    cfg["alerts"]["extra_services"] = {"AVA Gateway": "http://127.0.0.1:1/x"}

    def probe(url, timeout=5):
        return "127.0.0.1:1" not in url  # only the extra is down

    monkeypatch.setattr(codec_alerts, "_check_service", probe)
    codec_alerts.check_services_and_alert()  # failure #1
    codec_alerts.check_services_and_alert()  # failure #2 → alert
    assert calls["restarts"] == [], "extra services must never be auto-restarted"
    assert any("AVA Gateway" in msg for _, msg in calls["alerts"]), calls["alerts"]


def test_builtin_down_triggers_single_restart(alert_harness, monkeypatch):
    """LLM+Vision share qwen3.6 — one down pass must restart it at most once."""
    cfg, state_holder, calls = alert_harness

    def probe(url, timeout=5):
        return "8083" not in url  # qwen URL down, everything else up

    monkeypatch.setattr(codec_alerts, "_check_service", probe)
    codec_alerts.check_services_and_alert()
    assert calls["restarts"].count("qwen3.6") <= 1, calls["restarts"]


# ── check_pm2_restart_storms ─────────────────────────────────────────────────


def _proc(name: str, restarts: int, autorestart: bool = True) -> dict:
    return {"name": name,
            "pm2_env": {"autorestart": autorestart, "restart_time": restarts}}


@pytest.fixture
def storm_state(monkeypatch, tmp_path):
    state_path = tmp_path / "pm2_restart_state.json"
    monkeypatch.setattr(codec_heartbeat, "_RESTART_STATE_PATH", str(state_path))
    events = []
    monkeypatch.setattr(codec_heartbeat, "log_event",
                        lambda *a, **kw: events.append((a, kw)))
    return state_path, events


def test_storm_first_run_sets_baseline_no_alert(storm_state):
    state_path, events = storm_state
    storms = codec_heartbeat.check_pm2_restart_storms(
        procs=[_proc("ava-litellm", 34000)])
    assert storms == []
    assert events == []
    saved = json.loads(state_path.read_text())
    assert saved["counts"]["ava-litellm"] == 34000


def test_storm_detected_on_delta(storm_state):
    state_path, events = storm_state
    codec_heartbeat.check_pm2_restart_storms(procs=[_proc("ava-litellm", 100)])
    storms = codec_heartbeat.check_pm2_restart_storms(
        procs=[_proc("ava-litellm", 100 + codec_heartbeat._RESTART_STORM_DELTA)])
    assert storms == [("ava-litellm", codec_heartbeat._RESTART_STORM_DELTA)]
    assert any(a[0][0] == "pm2_restart_storm" for a in events)


def test_storm_below_delta_silent(storm_state):
    state_path, events = storm_state
    codec_heartbeat.check_pm2_restart_storms(procs=[_proc("svc", 10)])
    storms = codec_heartbeat.check_pm2_restart_storms(procs=[_proc("svc", 12)])
    assert storms == []
    assert events == []


def test_storm_cron_jobs_excluded(storm_state):
    """autorestart=false processes (cron jobs) restart by design — never
    counted as storms no matter the delta."""
    state_path, events = storm_state
    codec_heartbeat.check_pm2_restart_storms(
        procs=[_proc("intake-outbound-retry", 100, autorestart=False)])
    storms = codec_heartbeat.check_pm2_restart_storms(
        procs=[_proc("intake-outbound-retry", 700, autorestart=False)])
    assert storms == []
    assert events == []


def test_storm_realert_cooldown(storm_state):
    """A persisting storm re-alerts only after the 6h cooldown."""
    state_path, events = storm_state
    codec_heartbeat.check_pm2_restart_storms(procs=[_proc("svc", 0)])
    codec_heartbeat.check_pm2_restart_storms(procs=[_proc("svc", 10)])   # alert 1
    n_after_first = len(events)
    codec_heartbeat.check_pm2_restart_storms(procs=[_proc("svc", 20)])   # within cooldown
    assert len(events) == n_after_first, "second alert must be suppressed by cooldown"
