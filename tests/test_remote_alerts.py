"""Owner alerts off the Mac (27 Sep 2026 audit, item 6).

Telegram through the CODEC bot's Keychain token and alerts.telegram.chat_id;
each problem alerts once, repeats at most every 6 h, and clears with one
recovery message; errored CODEC apps, auto-pull failures and a daily status
reach the owner. No test here sends a real message or shows a real banner.
"""
import json
import os
import stat
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

import codec_alerts
import codec_config
import codec_heartbeat

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def sent(monkeypatch, tmp_path):
    """Isolated config + alert state; records every channel instead of sending."""
    out = {"telegram": [], "macos": []}
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"alerts": {"telegram": {"chat_id": "42"}}}))
    monkeypatch.setattr(codec_alerts, "CONFIG_PATH", str(cfg_path))
    monkeypatch.setattr(codec_alerts, "ALERT_STATE_PATH", str(tmp_path / "alert_state.json"))
    monkeypatch.setattr(codec_alerts, "ALERTS_SENT_PATH", str(tmp_path / "alerts_sent.json"))
    monkeypatch.setattr(codec_alerts, "_send_telegram",
                        lambda token, chat, msg: out["telegram"].append((token, chat, msg)) or True)
    monkeypatch.setattr(codec_alerts, "_send_macos_notification", lambda msg: out["macos"].append(msg))
    monkeypatch.setattr(codec_config, "get_telegram_bot_token", lambda: "kc-token")
    out["cfg_path"] = cfg_path
    return out


def test_telegram_uses_the_keychain_token_and_chat_id(sent):
    codec_alerts.send_alert("critical", "CODEC ALERT: Dashboard is not responding.")
    assert sent["telegram"] == [("kc-token", "42", "CODEC ALERT: Dashboard is not responding.")]


def test_no_chat_id_means_no_telegram(sent):
    sent["cfg_path"].write_text(json.dumps({"alerts": {}}))
    codec_alerts.send_alert("critical", "x")
    assert sent["telegram"] == [] and sent["macos"] == ["x"]


def test_older_plaintext_token_config_still_works(sent):
    sent["cfg_path"].write_text(json.dumps(
        {"alerts": {"telegram": {"enabled": True, "bot_token": "old", "chat_id": "7"}}}))
    codec_alerts.send_alert("info", "y")
    assert sent["telegram"] == [("old", "7", "y")]


def test_a_problem_alerts_once_repeats_after_6h_and_clears_once(sent):
    t0 = 1_000_000.0
    assert codec_alerts.alert_once("down:Dashboard", "critical", "down", now=t0) is True
    assert codec_alerts.alert_once("down:Dashboard", "critical", "down", now=t0 + 3600) is False
    assert codec_alerts.alert_once("down:Dashboard", "critical", "down", now=t0 + 6 * 3600) is True
    assert codec_alerts.open_problems() == ["down:Dashboard"]
    assert codec_alerts.alert_resolved("down:Dashboard", "back") is True
    assert codec_alerts.alert_resolved("down:Dashboard", "back") is False
    assert [m for *_, m in sent["telegram"]] == ["down", "down", "back"]
    assert stat.S_IMODE(os.stat(codec_alerts.ALERTS_SENT_PATH).st_mode) == 0o600


def test_service_down_alerts_once_not_every_heartbeat(sent, monkeypatch):
    monkeypatch.setattr(codec_alerts, "_SERVICES", {"Dashboard": "http://localhost:{dashboard_port}/api/health"})
    monkeypatch.setattr(codec_alerts, "_check_service", lambda url, timeout=5: False)
    monkeypatch.setattr(codec_alerts, "_is_listening", lambda url, timeout=2: False)
    monkeypatch.setattr(codec_alerts, "_try_restart", lambda name: False)
    monkeypatch.setattr(codec_alerts.time, "sleep", lambda s: None)
    monkeypatch.setattr(codec_alerts.subprocess, "check_output",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no pm2 in tests")))
    for _ in range(4):
        codec_alerts.check_services_and_alert()
    down = [m for *_, m in sent["telegram"] if "not responding" in m]
    assert len(down) == 1 and "Dashboard" in down[0]


def test_local_model_is_not_probed_while_chat_uses_a_cloud_model(sent, monkeypatch):
    sent["cfg_path"].write_text(json.dumps({"llm_base_url": "https://api.example-cloud.test/v1",
                                            "alerts": {"telegram": {"chat_id": "42"}}}))
    probed = []
    monkeypatch.setattr(codec_alerts, "_check_service", lambda url, timeout=5: probed.append(url) or True)
    monkeypatch.setattr(codec_alerts.subprocess, "check_output",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no pm2 in tests")))
    codec_alerts.check_services_and_alert()
    assert probed and not any(":8083" in u or "localhost:/" in u for u in probed)


def _proc(name, status):
    return {"name": name, "pm2_env": {"status": status, "autorestart": True, "restart_time": 0}}


def test_errored_codec_app_alerts_once_and_recovers(sent):
    procs = [_proc("codec-observer", "errored"), _proc("sentora-x", "errored"), _proc("codec-dashboard", "online")]
    assert codec_heartbeat.check_errored_codec_apps(procs) == ["codec-observer"]
    codec_heartbeat.check_errored_codec_apps(procs)
    codec_heartbeat.check_errored_codec_apps([_proc("codec-observer", "online")])
    msgs = [m for *_, m in sent["telegram"]]
    assert len(msgs) == 2 and "codec-observer" in msgs[0] and "running again" in msgs[1]
    assert not any("sentora" in m for m in msgs)  # other projects' apps are not CODEC's to report


def test_daily_status_once_per_day_after_0730(sent, monkeypatch, tmp_path):
    log = tmp_path / "auto_pull.log"
    log.write_text(f'{datetime.now().date().isoformat()}T06:00:03+0200 outcome=up_to_date old=a new=a commits=0 notify=none detail=""\n')
    monkeypatch.setattr(codec_heartbeat, "_AUTO_PULL_LOG", str(log))
    procs = [_proc("codec-dashboard", "online"), _proc("open-codec", "online"), _proc("codec-observer", "errored")]
    codec_alerts.alert_once("pm2_errored:codec-observer", "critical", "x")
    day = datetime.now().replace(hour=7, minute=29)
    assert codec_heartbeat.maybe_send_daily_status(now=day, procs=procs) is False
    assert codec_heartbeat.maybe_send_daily_status(now=day.replace(minute=31), procs=procs) is True
    assert codec_heartbeat.maybe_send_daily_status(now=day.replace(hour=12), procs=procs) is False
    status = sent["telegram"][-1][2]
    assert "2/3 apps online" in status and "auto-pull 06:00 up_to_date" in status
    assert "codec-observer errored" in status


def _fake_python(tmp_path):
    """Stands in for CODEC_PY: records every remote_alert call from auto_pull.sh."""
    record = tmp_path / "alerts.txt"
    fake = tmp_path / "fakepy"
    fake.write_text('#!/bin/bash\n[ -n "$ALERT_MODE" ] && echo "$ALERT_MODE|$ALERT_TEXT" >> "$RECORD"\nexit 0\n')
    fake.chmod(0o755)
    return fake, record


def _run_auto_pull(repo, state, fake, record):
    env = {**os.environ, "CODEC_REPO": str(repo), "CODEC_STATE": str(state),
           "CODEC_PY": str(fake), "RECORD": str(record)}
    return subprocess.run(["bash", str(REPO / "scripts" / "auto_pull.sh")],
                          env=env, capture_output=True, text=True, timeout=120)


def test_auto_pull_failure_alerts_and_a_later_success_clears_it(tmp_path):
    fake, record = _fake_python(tmp_path)
    notgit = tmp_path / "notgit"
    notgit.mkdir()
    assert _run_auto_pull(notgit, tmp_path / "state", fake, record).returncode == 1
    assert record.read_text().startswith("fail|CODEC Auto-pull failed: not a git checkout")

    origin, clone = tmp_path / "origin.git", tmp_path / "clone"
    git = lambda *a, cwd=None: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)  # noqa: E731
    git("init", "-q", "--bare", "-b", "main", str(origin))
    git("clone", "-q", str(origin), str(clone))
    (clone / "f.txt").write_text("x")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init", cwd=clone)
    git("checkout", "-q", "-B", "main", cwd=clone)
    git("push", "-q", "-u", "origin", "main", cwd=clone)
    assert _run_auto_pull(clone, tmp_path / "state", fake, record).returncode == 0
    assert record.read_text().splitlines()[-1] == "ok|CODEC auto-pull works again (up_to_date)."


def test_macos_banner_text_cannot_end_the_applescript_string():
    assert codec_alerts._applescript_text('say "hi" \\ now\nnext') == 'say \\"hi\\" \\\\ now next'
