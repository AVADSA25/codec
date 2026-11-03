"""The supervisor adapter (P-032, docs/LAUNCHD-STATUS-DESIGN.md).

A buyer's Mac runs CODEC as launchd LaunchAgents; the Studio runs it under PM2. `codec_supervisor`
says which, and gives launchd's list in `pm2 jlist`'s shape, so the heartbeat, Connections and
Cortex read either. Every launchctl, ps and pm2 answer here is a fixture: nothing is run.
"""
from __future__ import annotations

import asyncio
import json
import logging

import pytest

import codec_heartbeat
import codec_supervisor as sv

P = sv.LABEL_PREFIX
UID = 501
NOW = 1_800_000_000.0

LIST = (
    "PID\tStatus\tLabel\n"
    f"4242\t0\t{P}codec-telegram\n"
    f"-\t-9\t{P}codec-imessage\n"
    f"-\t1\t{P}codec-dashboard\n"
    f"-\t0\t{P}codec-watchdog\n"
    "424\t0\tapplication.ai.avadigital.codec.198318314.198636445\n"
    "-\t0\tcom.avadigital.codec\n"
    "77\t0\tcom.apple.Finder\n"
)


def _print(label, state, *, pid=None, runs=1, props="keepalive | runatload | inferred program",
           exit_line="last exit code = (never exited)"):
    """`launchctl print gui/501/<label>`, with nested blocks whose own `state` / `keepalive` lines
    must be ignored."""
    lines = [f"gui/{UID}/{label} = {{", "\tactive count = 1",
             f"\tpath = /Users/x/Library/LaunchAgents/{label}.plist", "\ttype = LaunchAgent",
             f"\tstate = {state}", "", "\tprogram = /Applications/CODEC.app/Contents/MacOS/python3",
             "\targuments = {", "\t\tpython3", "\t\tcodec_telegram.py", "\t}", "",
             "\tenvironment = {", "\t\tSECRET_TOKEN => do-not-copy", "\t}", "", f"\truns = {runs}"]
    if pid:
        lines.append(f"\tpid = {pid}")
    lines += [f"\t{exit_line}", "\tevent triggers = {", "\t\tx => {", "\t\t\tkeepalive = 0",
              "\t\t\tstate = active", "\t\t}", "\t}", f"\tproperties = {props}", "}"]
    return "\n".join(lines) + "\n"


PRINTS = {
    f"{P}codec-telegram": _print(f"{P}codec-telegram", "running", pid=4242, runs=3),
    f"{P}codec-imessage": _print(f"{P}codec-imessage", "spawn scheduled", runs=7,
                                 exit_line="last terminating signal = Killed: 9"),
    f"{P}codec-dashboard": _print(f"{P}codec-dashboard", "not running", runs=2, exit_line="last exit code = 1"),
    f"{P}codec-watchdog": _print(f"{P}codec-watchdog", "not running", runs=1,
                                 props="runatload | inferred program", exit_line="last exit code = 0"),
}


@pytest.fixture
def agents(tmp_path):
    """A LaunchAgents folder with one CODEC plist that launchd has not loaded, and one other plist."""
    d = tmp_path / "LaunchAgents"
    d.mkdir()
    (d / f"{P}whisper-stt.plist").write_text("<plist/>")
    (d / "com.avadigital.codec.plist").write_text("<plist/>")
    return d


def _runner(calls, listing=LIST, prints=PRINTS, ps="  4242 05:00\n"):
    def run(args):
        calls.append(list(args))
        if args == [sv.LAUNCHCTL, "list"]:
            return listing
        if args[:2] == [sv.LAUNCHCTL, "print"]:
            return prints.get(args[2].split("/", 2)[2])
        if args[0] == sv.PS:
            return ps
        raise AssertionError(f"unexpected command {args}")
    return run


def _jlist(agents, calls=None, **kw):
    return sv.launchd_jlist(run=_runner([] if calls is None else calls, **kw), uid=UID,
                            agents_dir=str(agents), now=NOW)


# ── kind ────────────────────────────────────────────────────────────────────

def test_kind_is_pm2_in_a_checkout_and_launchd_inside_the_app(tmp_path):
    app = tmp_path / "CODEC.app" / "Contents" / "Resources" / "app"
    app.mkdir(parents=True)
    assert sv.kind(cfg={}, repo=str(sv.REPO)) == "pm2", "the Studio's checkout: PM2, as always"
    assert sv.kind(cfg={}, repo=str(app)) == "launchd"
    assert sv.kind(cfg={"supervisor": "pm2"}, repo=str(app)) == "pm2", "config wins"
    assert sv.kind(cfg={"supervisor": "launchd"}, repo=str(sv.REPO)) == "launchd"
    assert sv.kind(cfg={"supervisor": "systemd"}, repo=str(app)) == "launchd", "an unknown value is ignored"
    assert sv.kind(cfg=["not", "a", "dict"], repo=str(sv.REPO)) == "pm2"


def test_kind_reads_config_json_and_survives_a_broken_one(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    monkeypatch.setattr(sv, "CONFIG_PATH", str(cfg))
    assert sv.kind(repo=str(sv.REPO)) == "pm2", "no file"
    cfg.write_text(json.dumps({"supervisor": "launchd"}))
    assert sv.kind(repo=str(sv.REPO)) == "launchd"
    cfg.write_text("{broken")
    assert sv.kind(repo=str(sv.REPO)) == "pm2"


def test_kind_logs_hint_words_each_supervisor():
    assert sv.logs_hint("codec-telegram", "pm2") == "pm2 logs codec-telegram"
    assert sv.logs_hint("codec-telegram", "launchd") == "its log ~/Library/Logs/CODEC/codec-telegram.err"


# ── the adapter ─────────────────────────────────────────────────────────────

def test_adapter_gives_each_launch_agent_in_pm2_jlist_shape(agents):
    calls = []
    procs = {p["name"]: p for p in _jlist(agents, calls)}
    assert sorted(procs) == ["codec-dashboard", "codec-imessage", "codec-telegram", "codec-watchdog",
                             "whisper-stt"], "only ai.avadigital.codec.* labels, loaded or on disk"
    assert procs["codec-telegram"] == {"name": "codec-telegram", "pid": 4242, "pm2_env": {
        "status": "online", "autorestart": True, "restart_time": 2,
        "pm_uptime": int((NOW - 300) * 1000)}}
    assert procs["codec-imessage"]["pm2_env"] == {"status": "waiting restart", "autorestart": True,
                                                   "restart_time": 6, "pm_uptime": 0, "exit_code": -9}
    assert procs["codec-dashboard"]["pm2_env"]["status"] == "errored", "KeepAlive, and launchd is not starting it"
    assert procs["codec-dashboard"]["pm2_env"]["exit_code"] == 1 and procs["codec-dashboard"]["pid"] == 0
    assert procs["codec-watchdog"]["pm2_env"] == {"status": "stopped", "autorestart": False,
                                                   "restart_time": 0, "pm_uptime": 0, "exit_code": 0}
    assert procs["whisper-stt"] == {"name": "whisper-stt", "pid": 0, "pm2_env": {
        "status": "stopped", "autorestart": False, "restart_time": 0, "pm_uptime": 0}}, "plist not loaded"
    assert "do-not-copy" not in json.dumps(list(procs.values())), "no environment is carried"
    # Read only: launchctl list and print, and one ps for the running pid.
    assert {c[1] for c in calls if c[0] == sv.LAUNCHCTL} == {"list", "print"}
    assert [c for c in calls if c[0] == sv.PS] == [[sv.PS, "-o", "pid=,etime=", "-p", "4242"]]
    assert f"gui/{UID}/{P}whisper-stt" in [c[2] for c in calls if c[1:2] == ["print"]]


def test_adapter_status_matches_the_print_state_of_each_service(agents):
    want = {"running": "online", "spawn scheduled": "waiting restart"}
    for proc in _jlist(agents):
        text = PRINTS.get(P + proc["name"])
        info = sv.parse_print(text) if text else None
        assert proc["pm2_env"]["status"] == sv.status_of(info)
        if info and info["state"] in want:
            assert proc["pm2_env"]["status"] == want[info["state"]], proc["name"]
    nested = sv.parse_print(PRINTS[f"{P}codec-watchdog"])
    assert nested["state"] == "not running" and "keepalive" not in nested["properties"], "nested lines skipped"


def test_adapter_answers_nothing_when_launchctl_cannot_be_read(agents):
    assert sv.launchd_jlist(run=lambda args: None, uid=UID, agents_dir=str(agents), now=NOW) == []
    assert _jlist(agents, listing="PID\tStatus\tLabel\n", prints={}) == [
        {"name": "whisper-stt", "pid": 0, "pm2_env": {"status": "stopped", "autorestart": False,
                                                      "restart_time": 0, "pm_uptime": 0}}]

    def boom(args):
        raise RuntimeError("never raises out")
    assert sv.launchd_jlist(run=boom, uid=UID, agents_dir=str(agents), now=NOW) == []


def test_adapter_parses_ps_elapsed_times():
    assert sv.parse_etime("05:00") == 300
    assert sv.parse_etime(" 01:02:03 ") == 3723
    assert sv.parse_etime("2-00:00:01") == 172801
    assert sv.parse_etime("soon") is None and sv.parse_etime("") is None


def test_adapter_reads_the_real_launchctl_when_it_exists(tmp_path):
    """On a Mac: the real (read-only) list answers in the same shape; elsewhere it is [] as PM2's was."""
    procs = sv.launchd_jlist(agents_dir=str(tmp_path))
    assert isinstance(procs, list)
    for p in procs:
        assert p["name"] and set(p["pm2_env"]) >= {"status", "autorestart", "restart_time", "pm_uptime"}


# ── the readers ─────────────────────────────────────────────────────────────

@pytest.fixture
def launchd(monkeypatch, agents):
    procs = _jlist(agents)
    monkeypatch.setattr(sv, "kind", lambda *a, **k: "launchd")
    monkeypatch.setattr(sv, "launchd_jlist", lambda **k: procs)
    return procs


def _no_pm2(*a, **k):
    raise AssertionError("PM2 asked on a launchd Mac")


def test_reader_heartbeat_reads_launchd_on_launchd_and_pm2_otherwise(monkeypatch, launchd):
    monkeypatch.setattr(codec_heartbeat.subprocess, "check_output", _no_pm2)
    assert codec_heartbeat._pm2_jlist() is launchd
    monkeypatch.setattr(sv, "kind", lambda *a, **k: "pm2")
    monkeypatch.setattr(sv, "launchd_jlist", lambda **k: pytest.fail("launchd asked on a PM2 Mac"))
    seen = []
    monkeypatch.setattr(codec_heartbeat.subprocess, "check_output",
                        lambda args, **k: seen.append(args) or b'[{"name": "qwen3.6"}]')
    assert codec_heartbeat._pm2_jlist() == [{"name": "qwen3.6"}]
    assert seen == [["/opt/homebrew/bin/pm2", "jlist"]], "the very command as before"


def test_reader_connections_bridges_show_the_launchd_state(monkeypatch, launchd, tmp_path):
    import routes.connections as c
    monkeypatch.setattr(c.subprocess, "run", _no_pm2)
    assert c.pm2_status() == {"codec-dashboard": "errored", "codec-imessage": "waiting restart",
                              "codec-telegram": "online", "codec-watchdog": "stopped", "whisper-stt": "stopped"}
    monkeypatch.setattr(c, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(c, "IMESSAGE_HEALTH", str(tmp_path / "imessage_health.json"))
    monkeypatch.setattr(c, "_telegram_token", lambda cfg: "")
    monkeypatch.setattr(c, "messages_db_readable", lambda: None)
    d = c.bridges()
    assert d["telegram"]["running"] == "online" and d["imessage"]["running"] == "waiting restart"


def test_reader_connections_on_pm2_runs_the_same_jlist(monkeypatch):
    import routes.connections as c
    monkeypatch.setattr(sv, "kind", lambda *a, **k: "pm2")
    seen = []

    class R:
        stdout = b'[{"name": "codec-telegram", "pm2_env": {"status": "online", "env": {"K": "v"}}}]'
    monkeypatch.setattr(c.subprocess, "run", lambda args, **k: seen.append(args) or R())
    assert c.pm2_status() == {"codec-telegram": "online"}
    assert seen == [[c.PM2, "jlist"]]


class _NoHTTP:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        raise OSError("no model server in a test")


def test_reader_cortex_lists_the_launchd_services(monkeypatch, launchd):
    import httpx
    import routes.cortex as rc
    monkeypatch.setattr(httpx, "AsyncClient", _NoHTTP)
    monkeypatch.setattr(rc.subprocess, "check_output", _no_pm2)
    out = asyncio.run(rc.cortex_health())
    assert out["telegram"] == "ok" and out["imessage"] == "err" and out["watchdog"] == "err"
    assert out["dashboard"] == "ok" and out["qwen"] == "err", "the probes are unchanged"


def test_reader_cortex_on_pm2_runs_the_same_jlist(monkeypatch):
    import httpx
    import routes.cortex as rc
    monkeypatch.setattr(httpx, "AsyncClient", _NoHTTP)
    monkeypatch.setattr(sv, "kind", lambda *a, **k: "pm2")
    seen = []
    monkeypatch.setattr(rc.subprocess, "check_output", lambda args, **k: seen.append(args) or
                        b'[{"name": "codec-telegram", "pm2_env": {"status": "online"}}]')
    assert asyncio.run(rc.cortex_health())["telegram"] == "ok"
    assert seen == [["/opt/homebrew/bin/pm2", "jlist"]]


# ── the heartbeat's reports ─────────────────────────────────────────────────

def _app(restarts, status="online", exit_code=-9):
    return [{"name": "codec-telegram", "pid": 4242, "pm2_env": {
        "status": status, "autorestart": True, "restart_time": restarts, "pm_uptime": 0, "exit_code": exit_code}}]


@pytest.fixture
def storms(monkeypatch, tmp_path, caplog):
    import codec_alerts
    sent = []
    monkeypatch.setattr(codec_heartbeat, "_RESTART_STATE_PATH", str(tmp_path / "pm2_restart_state.json"))
    monkeypatch.setattr(codec_heartbeat, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(codec_alerts, "send_alert", lambda level, msg: sent.append(msg))
    caplog.set_level(logging.INFO, logger="heartbeat")
    return sent


def test_report_launchd_restart_line_then_storm(monkeypatch, storms, caplog):
    monkeypatch.setattr(sv, "kind", lambda *a, **k: "launchd")
    assert codec_heartbeat.check_pm2_restart_storms(_app(2)) == [], "first sight: counts only"
    assert codec_heartbeat.check_pm2_restart_storms(_app(3)) == []
    assert "launchd restarted codec-telegram 1x since the last heartbeat (last exit code -9)" in caplog.text
    assert codec_heartbeat.check_pm2_restart_storms(_app(10)) == [("codec-telegram", 7)]
    assert storms == ["🔁 launchd restart storm: 'codec-telegram' restarted 7× since the last heartbeat "
                      "(10 total) — likely crash-looping."]


def test_report_pm2_texts_are_the_old_ones(monkeypatch, storms, caplog):
    monkeypatch.setattr(sv, "kind", lambda *a, **k: "pm2")
    codec_heartbeat.check_pm2_restart_storms(_app(2))
    codec_heartbeat.check_pm2_restart_storms(_app(3))
    assert "restarted codec-telegram" not in caplog.text, "PM2: no line under the storm threshold, as before"
    assert codec_heartbeat.check_pm2_restart_storms(_app(10)) == [("codec-telegram", 7)]
    assert storms == ["🔁 PM2 restart storm: 'codec-telegram' restarted 7× since the last heartbeat "
                      "(10 total) — likely crash-looping."]


@pytest.mark.parametrize("supervisor, text", [
    ("launchd", "CODEC ALERT: codec-telegram is down and launchd is not starting it again. "
                "It needs a look (its log ~/Library/Logs/CODEC/codec-telegram.err)."),
    ("pm2", "CODEC ALERT: codec-telegram crashed repeatedly and PM2 gave up restarting it. "
            "It needs a look (pm2 logs codec-telegram)."),
])
def test_report_errored_service_alert_words(monkeypatch, supervisor, text):
    import codec_alerts
    sent = []
    monkeypatch.setattr(sv, "kind", lambda *a, **k: supervisor)
    monkeypatch.setattr(codec_alerts, "alert_once", lambda key, level, msg, **k: sent.append((key, level, msg)))
    monkeypatch.setattr(codec_alerts, "alert_resolved", lambda key, msg: sent.append((key, msg)))
    assert codec_heartbeat.check_errored_codec_apps(_app(3, status="errored")) == ["codec-telegram"]
    assert sent == [("pm2_errored:codec-telegram", "critical", text)]
    sent.clear()
    codec_heartbeat.check_errored_codec_apps(_app(3))
    assert sent == [("pm2_errored:codec-telegram", "CODEC RECOVERED: codec-telegram is running again.")]
