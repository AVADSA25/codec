"""Health beats (P-017, docs/HEALTH-BEATS-DESIGN.md).

A bridge PM2 shows "online" can fail every round or hang. codec-imessage, codec-telegram and
codec-autopilot write one beat a round (codec_health); the heartbeat reads the folder and alerts once
when a beat is older than 3x its cadence (never under 15 minutes) or 20 rounds in a row failed. The
observer's beat is its newest observation_tick audit line.

Every path is a tmp path inside a temp CODEC folder (never a fake HOME): the beat folder, the alert
state, the audit log, the observer pause flag and config. No test sends a message, runs pm2 or sleeps.
"""
from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import requests

# At collection, like the other daemon tests: a skill can put ~/codec-repo first on sys.path when it
# loads, and an import inside a test then got the main checkout's module instead of this worktree's.
import codec_alerts
import codec_audit
import codec_autopilot
import codec_health
import codec_heartbeat
import codec_imessage as im
import codec_jsonstore
import codec_observer
import codec_telegram

T = 1_800_000_000.0  # the fake clock, epoch seconds


@pytest.fixture
def codec_dir(tmp_path, monkeypatch):
    """A temp CODEC folder: beats, alert state and the observer's files all live in it."""
    d = tmp_path / "codec"
    d.mkdir()
    monkeypatch.setattr(codec_health, "HEALTH_DIR", str(d / "health"))
    monkeypatch.setattr(codec_health, "_rounds", {})
    monkeypatch.setattr(codec_alerts, "ALERTS_SENT_PATH", str(d / "alerts_sent.json"))
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", d / "audit.log")
    monkeypatch.setattr(codec_observer, "_PAUSE_PATH", d / "observer_paused_until")
    monkeypatch.setattr(codec_observer, "_CODEC_CONFIG_PATH", d / "config.json")
    return d


@pytest.fixture
def sent(codec_dir, monkeypatch):
    """Every alert the heartbeat sends, as (level, message); nothing reaches a real channel."""
    out = []
    monkeypatch.setattr(codec_alerts, "send_alert", lambda level, msg, subject=None: out.append((level, msg)))
    return out


def _beat_file(codec_dir, name, **fields):
    data = {"ts": T, "ok": True, "consecutive_errors": 0, "last_error_class": None,
            "cadence_s": 60.0, "stopped": False}
    data.update(fields)
    (codec_dir / "health").mkdir(exist_ok=True)
    (codec_dir / "health" / f"{name}.json").write_text(json.dumps(data))


def _read(codec_dir, name):
    return json.loads((codec_dir / "health" / f"{name}.json").read_text())


def _app(name, started=T - 7200, status="online", **env):
    return {"name": name, "pm2_env": {"status": status, "pm_uptime": int(started * 1000), **env}}


# ── the beat file ───────────────────────────────────────────────────────────


def test_a_beat_is_one_private_file_written_atomically(codec_dir, monkeypatch):
    written = []
    real = codec_jsonstore.atomic_write_json
    monkeypatch.setattr(codec_jsonstore, "atomic_write_json",
                        lambda p, d, **k: written.append(str(p)) or real(p, d, **k))
    assert codec_health.beat("codec-telegram", cadence_s=40, now=T) is True
    path = codec_dir / "health" / "codec-telegram.json"
    assert _read(codec_dir, "codec-telegram") == {
        "ts": T, "ok": True, "consecutive_errors": 0, "last_error_class": None,
        "cadence_s": 60.0, "stopped": False}, "cadence is at least the once-a-minute write"
    assert written == [str(path)], "through codec_jsonstore.atomic_write_json"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(codec_dir / "health").st_mode) == 0o700


def test_it_writes_once_a_minute_unless_what_it_says_changes(codec_dir):
    beat = codec_health.beat
    assert beat("codec-autopilot", cadence_s=30, now=T) is True
    assert beat("codec-autopilot", cadence_s=30, now=T + 30) is False
    assert beat("codec-autopilot", cadence_s=30, now=T + 60) is True, "a minute later"
    assert beat("codec-autopilot", cadence_s=30, error=ValueError("x"), now=T + 61) is True, "ok flips: at once"
    for i in range(18):  # errors 2..19, within the same minute: nothing new to say
        assert beat("codec-autopilot", cadence_s=30, error=ValueError("x"), now=T + 62 + i) is False
    assert beat("codec-autopilot", cadence_s=30, error=ValueError("x"), now=T + 80) is True, "the 20th error"
    data = _read(codec_dir, "codec-autopilot")
    assert data["consecutive_errors"] == 20 and data["last_error_class"] == "ValueError" and not data["ok"]
    assert beat("codec-autopilot", cadence_s=30, now=T + 81) is True
    data = _read(codec_dir, "codec-autopilot")
    assert data["ok"] and data["consecutive_errors"] == 0 and data["last_error_class"] == "ValueError"


def test_error_class_is_a_short_class_name_only():
    assert codec_health.error_class(None) is None
    assert codec_health.error_class(KeyError("+15550000001")) == "KeyError", "never the error text"
    assert codec_health.error_class(TimeoutError) == "TimeoutError"
    assert codec_health.error_class("Telegram Api<Error>\n") == "TelegramApiError"
    assert len(codec_health.error_class("A" * 500)) == 80


def test_a_beat_never_raises_and_never_leaves_its_folder(codec_dir, monkeypatch):
    blocker = codec_dir / "not-a-folder"
    blocker.write_text("")
    monkeypatch.setattr(codec_health, "HEALTH_DIR", str(blocker / "health"))
    assert codec_health.beat("codec-telegram", cadence_s=40) is False
    assert codec_health.stopped("codec-telegram") is False
    monkeypatch.setattr(codec_health, "HEALTH_DIR", str(codec_dir / "health"))
    assert codec_health.beat("../escape", cadence_s=40) is False
    assert codec_health.beat("codec-telegram", cadence_s="soon") is False
    assert not (codec_dir / "escape.json").exists() and not (codec_dir / "health").exists()


def test_a_stop_on_purpose_marks_the_file(codec_dir):
    codec_health.beat("codec-autopilot", cadence_s=30, error="StateFileCorrupt", now=T)
    assert codec_health.stopped("codec-autopilot", now=T + 5) is True
    data = _read(codec_dir, "codec-autopilot")
    assert data["stopped"] is True and data["ts"] == T + 5 and data["consecutive_errors"] == 1
    assert codec_health.verdict(data, now=T + 99_999) == "stopped"


def test_the_stale_rule_is_3x_cadence_never_under_15_minutes():
    v = codec_health.verdict
    assert v({"ts": T - 899, "cadence_s": 60}, now=T) == "ok"
    assert v({"ts": T - 901, "cadence_s": 60}, now=T) == "silent", "3x60 s is under the 15-minute floor"
    assert v({"ts": T - 1799, "cadence_s": 600}, now=T) == "ok"
    assert v({"ts": T - 1801, "cadence_s": 600}, now=T) == "silent", "3x600 s"
    assert v({"ts": T, "cadence_s": 60, "consecutive_errors": 19}, now=T) == "ok"
    assert v({"ts": T, "cadence_s": 60, "consecutive_errors": 20}, now=T) == "failing"


# ── the heartbeat reads the folder ──────────────────────────────────────────


def test_one_alert_for_a_stale_beat_none_for_a_fresh_one(codec_dir, sent):
    _beat_file(codec_dir, "codec-telegram", ts=T - 3600)   # an hour without a beat
    _beat_file(codec_dir, "codec-autopilot", ts=T - 30)    # fresh
    assert codec_heartbeat.check_health_beats(procs=[], now_ts=T) == ["silent:codec-telegram"]
    assert len(sent) == 1, sent
    level, msg = sent[0]
    assert level == "critical" and "codec-telegram has not reported for 60 min" in msg
    assert "codec-autopilot" not in msg
    _beat_file(codec_dir, "codec-autopilot", ts=T + 1800)
    codec_heartbeat.check_health_beats(procs=[], now_ts=T + 1800)
    assert len(sent) == 1, "the next heartbeat does not repeat it (at most every 6 h)"
    assert codec_alerts.open_problems() == ["silent:codec-telegram"]


def test_a_silent_beat_that_reports_again_sends_one_recovery(codec_dir, sent):
    _beat_file(codec_dir, "codec-telegram", ts=T - 3600)
    codec_heartbeat.check_health_beats(procs=[], now_ts=T)
    _beat_file(codec_dir, "codec-telegram", ts=T + 1790)
    assert codec_heartbeat.check_health_beats(procs=[], now_ts=T + 1800) == []
    assert [m for _, m in sent][1:] == ["CODEC RECOVERED: codec-telegram reports again."]
    codec_heartbeat.check_health_beats(procs=[], now_ts=T + 1810)
    assert len(sent) == 2 and codec_alerts.open_problems() == []


def test_twenty_failed_rounds_alert_once_nineteen_do_not(codec_dir, sent):
    _beat_file(codec_dir, "codec-imessage", consecutive_errors=19, ok=False, last_error_class="PermissionDenied")
    assert codec_heartbeat.check_health_beats(procs=[], now_ts=T) == [] and sent == []
    _beat_file(codec_dir, "codec-imessage", consecutive_errors=20, ok=False, last_error_class="PermissionDenied")
    assert codec_heartbeat.check_health_beats(procs=[], now_ts=T) == ["failing:codec-imessage"]
    assert sent == [("warning", "CODEC ALERT: codec-imessage is running but failing: its last 20 rounds "
                                "ended in an error (PermissionDenied). Check pm2 logs codec-imessage.")]
    _beat_file(codec_dir, "codec-imessage")
    codec_heartbeat.check_health_beats(procs=[], now_ts=T)
    assert sent[-1] == ("recovery", "CODEC RECOVERED: codec-imessage works again.")


def test_a_stopped_beat_or_a_stopped_pm2_app_is_not_checked(codec_dir, sent):
    _beat_file(codec_dir, "codec-autopilot", ts=T - 86400, stopped=True)
    _beat_file(codec_dir, "codec-imessage", ts=T - 86400)
    _beat_file(codec_dir, "codec-old-bridge", ts=T - 86400)
    procs = [_app("codec-imessage", status="stopped"), _app("codec-autopilot")]
    assert codec_heartbeat.check_health_beats(procs=procs, now_ts=T) == []
    assert sent == [], "stopped mark, PM2 'stopped', and an app PM2 does not run"
    # Positive control: the same stale file with the app online in PM2 does alert.
    assert codec_heartbeat.check_health_beats(procs=[_app("codec-imessage")], now_ts=T) == ["silent:codec-imessage"]


def test_an_app_in_its_start_grace_is_skipped_for_the_round(codec_dir, sent):
    _beat_file(codec_dir, "codec-telegram", ts=T - 86400)  # from before a reboot
    assert codec_heartbeat.check_health_beats(procs=[_app("codec-telegram", started=T - 60)], now_ts=T) == []
    assert sent == []
    assert codec_heartbeat.check_health_beats(procs=[_app("codec-telegram", started=T - 400)], now_ts=T) \
        == ["silent:codec-telegram"]


def test_the_daily_status_names_the_new_problems(codec_dir, sent):
    _beat_file(codec_dir, "codec-telegram", ts=T - 3600)
    _beat_file(codec_dir, "codec-autopilot", consecutive_errors=25, ok=False, last_error_class="StateFileCorrupt")
    codec_heartbeat.check_health_beats(procs=[], now_ts=T)
    text = codec_heartbeat.daily_status_text(procs=[])
    assert "codec-telegram silent" in text and "codec-autopilot failing" in text


def test_the_heartbeat_runs_the_beat_check_with_its_one_jlist(monkeypatch):
    import codec_backup
    import codec_checkin
    jlist, seen = [_app("codec-telegram")], {}
    stubs = {
        "_pm2_jlist": lambda: jlist,
        "check_system_health": lambda procs=None: None,
        "check_pm2_restart_storms": lambda procs=None: [],
        "check_errored_codec_apps": lambda procs=None: [],
        "check_health_beats": lambda procs=None, now_ts=None: seen.update(beats=procs) or [],
        "maybe_send_daily_status": lambda now=None, procs=None: seen.setdefault("order", list(seen)) and False,
        "check_memory_stats": lambda: None,
        "check_pending_tasks": lambda: [],
        "check_alerts": lambda: [],
        "backup_memory_db": lambda: None,
        "log_event": lambda *a, **k: None,
    }
    for name, fn in stubs.items():
        monkeypatch.setattr(codec_heartbeat, name, fn)
    monkeypatch.setattr(codec_heartbeat, "_last_cleanup", None)
    monkeypatch.setattr(codec_alerts, "check_services_and_alert", lambda procs=None: None)
    monkeypatch.setattr(codec_checkin, "run_checkin", lambda: "skipped")
    monkeypatch.setattr(codec_backup, "run_retention", lambda: {})
    codec_heartbeat.heartbeat()
    assert seen["beats"] is jlist and seen["order"] == ["beats"], "before the daily status"


# ── the observer: the recency of its observation_tick audit lines ───────────


def _audit(codec_dir, *records):
    with open(codec_dir / "audit.log", "w", encoding="utf-8") as f:
        for when, event, extra in records:
            ts = datetime.fromtimestamp(when, timezone.utc).isoformat(timespec="milliseconds")
            f.write(json.dumps({"event": event, "extra": extra, "source": "x", "ts": ts},
                               sort_keys=True, separators=(",", ":")) + "\n")


OBSERVER = [_app("codec-observer", OBSERVER_ENABLED="true")]


def test_an_observer_whose_last_tick_is_40_minutes_old_alerts_once(codec_dir, sent):
    _audit(codec_dir, (T - 10800, "heartbeat_tick", {}),
           (T - 2400, "observation_tick", {"cadence_used_s": 60}),
           (T - 60, "skill_call", {}))
    assert codec_heartbeat.check_health_beats(procs=OBSERVER, now_ts=T) == ["silent:codec-observer"]
    assert len(sent) == 1 and "codec-observer has not reported for 40 min" in sent[0][1]


def test_a_recent_tick_or_a_slow_tick_is_fine(codec_dir, sent):
    _audit(codec_dir, (T - 10800, "heartbeat_tick", {}), (T - 120, "observation_tick_slow", {"cadence_used_s": 60}))
    assert codec_heartbeat.check_health_beats(procs=OBSERVER, now_ts=T) == [] and sent == []


def test_no_tick_at_all_counts_only_when_the_log_covers_the_stale_window(codec_dir, sent):
    _audit(codec_dir, (T - 300, "heartbeat_tick", {}), (T - 60, "skill_call", {}))
    assert codec_heartbeat.check_health_beats(procs=OBSERVER, now_ts=T) == [], "just rotated: unknown"
    _audit(codec_dir, (T - 10800, "heartbeat_tick", {}), (T - 60, "skill_call", {}))
    assert codec_heartbeat.check_health_beats(procs=OBSERVER, now_ts=T) == ["silent:codec-observer"]


def test_the_observer_is_not_checked_when_off_paused_or_not_in_pm2(codec_dir, sent):
    _audit(codec_dir, (T - 10800, "heartbeat_tick", {}), (T - 60, "skill_call", {}))
    check = codec_heartbeat.check_health_beats
    assert check(procs=[], now_ts=T) == []
    assert check(procs=[_app("codec-observer", status="stopped")], now_ts=T) == []
    assert check(procs=[_app("codec-observer", OBSERVER_ENABLED="false")], now_ts=T) == []
    (codec_dir / "config.json").write_text(json.dumps({"observer": {"enabled": False}}))
    assert check(procs=OBSERVER, now_ts=T) == []
    (codec_dir / "config.json").unlink()
    codec_observer._PAUSE_PATH.write_text((datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S"))
    assert check(procs=OBSERVER, now_ts=T) == []
    assert sent == []
    codec_observer._PAUSE_PATH.unlink()
    assert check(procs=OBSERVER, now_ts=T) == ["silent:codec-observer"], "positive control"


def test_the_audit_tail_read_skips_a_cut_first_line(codec_dir):
    _audit(codec_dir, *[(T - 3600 + i, "skill_call", {"pad": "x" * 200}) for i in range(50)],
           (T - 30, "observation_tick", {"cadence_used_s": 300}))
    (tick, cadence), start = codec_health.last_audit_tick(str(codec_dir / "audit.log"), max_bytes=2000)
    assert tick == pytest.approx(T - 30, abs=0.01) and cadence == 300
    assert T - 3600 < start < T - 30, "the tail's first whole line"
    assert codec_health.last_audit_tick(str(codec_dir / "missing.log")) is None


# ── each bridge's loop writes its beat ──────────────────────────────────────


class _Stop(Exception):
    """Leaves a daemon loop from its fake sleep."""


class _Rounds:
    """A fake sleep that ends a daemon loop after `n` rounds."""

    def __init__(self, n):
        self.n, self.slept = n, []

    def sleep(self, seconds):
        self.slept.append(seconds)
        if len(self.slept) >= self.n:
            raise _Stop


def test_the_autopilot_loop_beats_and_a_corrupt_state_counts_as_failing(codec_dir, monkeypatch):
    import codec_lifecycle
    cleanups, rounds = [], _Rounds(20)
    cfg, state = codec_dir / "autopilot.json", codec_dir / "autopilot_state.json"
    cfg.write_text(json.dumps({"enabled": True, "triggers": []}))
    state.write_text("{not json")
    monkeypatch.setattr(codec_autopilot, "CONFIG", cfg)
    monkeypatch.setattr(codec_autopilot, "STATE", state)
    monkeypatch.setattr(codec_autopilot, "SkillRegistry", lambda *a: SimpleNamespace(scan=lambda: None))
    monkeypatch.setattr(codec_autopilot, "time", SimpleNamespace(sleep=rounds.sleep, time=lambda: T))
    monkeypatch.setattr(codec_lifecycle, "install_handlers", lambda fn, name="": cleanups.append(fn) or fn)
    with pytest.raises(_Stop):
        codec_autopilot.main()
    data = _read(codec_dir, "codec-autopilot")
    assert data["consecutive_errors"] == 20 and data["last_error_class"] == "StateFileCorrupt"
    assert data["cadence_s"] == 60.0 and codec_health.verdict(data, now=data["ts"]) == "failing"
    cleanups[-1]()
    assert _read(codec_dir, "codec-autopilot")["stopped"] is True


def test_the_telegram_loop_beats_with_the_getupdates_error(codec_dir, monkeypatch):
    import codec_lifecycle
    cleanups, calls = [], []

    def get(url, **kw):
        calls.append(url)
        if len(calls) > 3:
            raise KeyboardInterrupt
        return SimpleNamespace(json=lambda: {"ok": False, "error_code": 409})

    monkeypatch.setattr(codec_telegram, "load_config", lambda: {})
    monkeypatch.setattr(codec_telegram, "get_telegram_config", lambda cfg: {"bot_token": "t", "require_trigger": False})
    monkeypatch.setattr(codec_telegram, "get_llm_config", lambda cfg: {"model": "m"})
    monkeypatch.setattr(codec_telegram, "audit", lambda event, **kw: None)
    monkeypatch.setattr(codec_telegram.TelegramBot, "get_me", lambda self: {"username": "u", "first_name": "f"})
    monkeypatch.setattr(codec_telegram, "requests", SimpleNamespace(get=get, exceptions=requests.exceptions))
    monkeypatch.setattr(codec_lifecycle, "install_handlers", lambda fn, name="": cleanups.append(fn) or fn)
    codec_telegram.main()
    data = _read(codec_dir, "codec-telegram")
    assert data["last_error_class"] == "TelegramApiError" and data["consecutive_errors"] >= 1
    assert data["cadence_s"] == 60.0 and data["ok"] is False
    bot = codec_telegram.TelegramBot("t")
    monkeypatch.setattr(codec_telegram, "requests", SimpleNamespace(
        get=lambda url, **kw: (_ for _ in ()).throw(requests.exceptions.ConnectionError("x")),
        exceptions=requests.exceptions))
    monkeypatch.setattr(codec_telegram, "time", SimpleNamespace(sleep=lambda s: None))
    assert bot.get_updates() == [] and bot.last_error == "ConnectionError"
    monkeypatch.setattr(codec_telegram, "requests", SimpleNamespace(
        get=lambda url, **kw: (_ for _ in ()).throw(requests.exceptions.Timeout()),
        exceptions=requests.exceptions))
    assert bot.get_updates() == [] and bot.last_error is None, "a long-poll timeout is the normal case"
    cleanups[-1]()
    assert _read(codec_dir, "codec-telegram")["stopped"] is True


class _Refused:
    """Stands in for sqlite3 inside codec_imessage: every read is refused like macOS does."""
    Row = object

    def connect(self, *a, **kw):
        raise im.sqlite3.DatabaseError("authorization denied")


def test_the_imessage_loop_beats_and_a_refused_database_counts_as_failing(codec_dir, monkeypatch):
    import codec_lifecycle
    real_sqlite = im.sqlite3
    cleanups, rounds = [], _Rounds(25)
    db = codec_dir / "chat.db"
    db.write_bytes(b"")
    (codec_dir / "config.json").write_text(json.dumps({"imessage": {"allowed_senders": ["+15550000001"]}}))
    for name, path in (("MESSAGES_DB", db), ("CONFIG_PATH", codec_dir / "config.json"),
                       ("STATE_FILE", codec_dir / "imessage_state.json"),
                       ("HEALTH_FILE", codec_dir / "imessage_health.json")):
        monkeypatch.setattr(im, name, str(path))
    refused = _Refused()
    refused.DatabaseError = real_sqlite.DatabaseError
    monkeypatch.setattr(im, "sqlite3", refused)
    monkeypatch.setattr(im, "_db_health", im.DbReadHealth(clock=lambda: T))
    monkeypatch.setattr(im, "_notify_needs_access", lambda program, failures: True)
    monkeypatch.setattr(im, "time", SimpleNamespace(sleep=rounds.sleep, time=lambda: T))
    monkeypatch.setattr(im, "get_llm_config", lambda cfg: {"model": "test-model"})
    monkeypatch.setattr(im, "audit", lambda event, **kw: None)
    monkeypatch.setattr(codec_lifecycle, "install_handlers", lambda fn, name="": cleanups.append(fn) or fn)
    with pytest.raises(_Stop):
        im.main()
    data = _read(codec_dir, "codec-imessage")
    assert data["last_error_class"] == "PermissionDenied" and data["consecutive_errors"] >= 20
    assert data["cadence_s"] == 300.0, "the P-058 slow poll after 10 refusals"
    assert codec_health.verdict(data, now=data["ts"]) == "failing"
    assert rounds.slept[0] == 3 and rounds.slept[-1] == 300
    cleanups[-1]()
    assert _read(codec_dir, "codec-imessage")["stopped"] is True
