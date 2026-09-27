"""codec_watchdog targets CODEC processes only, and never kills in dry-run.

Audit finding (2026-09): the watchdog listed processes with `ps ... comm`, which
carries the executable path only, while NEVER_KILL holds argument strings
("codec_dashboard", "uvicorn", ...) that never appear there. Any idle
Python/Terminal/iTerm process over 500 MB outside PM2 got SIGTERM then SIGKILL:
908 kills logged, most of them not CODEC's.

Pins:
  - generic python / Terminal / iTerm processes are never candidates
  - NEVER_KILL is matched against the full command line
  - default dry-run never calls os.kill; enforce needs watchdog.enforce == true
  - audit goes through codec_audit.log_event, not an HTTP POST
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import codec_audit
import codec_watchdog as wd

FAKE_REPO = "/opt/codec-repo"

GENERIC = [
    "/usr/bin/python3",
    "/usr/local/bin/python3.13 -m pytest -q tests/",
    "python3 train_model.py --epochs 10",
    "/Library/Frameworks/Python.framework/Versions/3.13/Resources/Python.app/Contents/MacOS/Python notebook.py",
    "/System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal",
    "/Applications/iTerm.app/Contents/MacOS/iTerm2",
    "-zsh",
    "/usr/local/bin/python3 -m mlx_vlm.server --model x --port 9999",  # not a CODEC port
    f"/Applications/Visual Studio Code.app/Contents/MacOS/Code Helper --folder {FAKE_REPO}/",
]

CODEC = [
    "python3 codec_session.py",
    "/usr/local/bin/python3.13 -u codec_observer.py",
    f"python3 {FAKE_REPO}/whisper_server.py",
    "python3 -m mlx_audio.server --host 0.0.0.0 --port 8085",
    "/Users/x/venv/bin/python -m mlx_vlm.server --model m --port 8083 --host 0.0.0.0",
]


@pytest.fixture(autouse=True)
def harness(monkeypatch, tmp_path):
    """Fake repo dir, config, notifications, ps/pm2 output, kills and audit."""
    monkeypatch.setattr(wd, "REPO_DIR", FAKE_REPO)
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text("{}")
    monkeypatch.setattr(wd, "CONFIG_PATH", str(cfg_path))
    notif_path = tmp_path / "notifications.json"
    monkeypatch.setattr(wd, "NOTIFICATIONS_PATH", str(notif_path))
    wd.idle_tracker.clear()

    h = SimpleNamespace(cfg=cfg_path, notif=notif_path, ps_rows=[], pm2="[]",
                        kills=[], events=[], ps_argv=None)

    def fake_run(argv, **kw):
        if argv[0] == "pm2":
            return SimpleNamespace(stdout=h.pm2, returncode=0)
        h.ps_argv = argv
        lines = ["  PID    RSS  %CPU ARGS"]
        lines += [f"{pid} {rss_kb} {cpu} {cmd}" for pid, rss_kb, cpu, cmd in h.ps_rows]
        return SimpleNamespace(stdout="\n".join(lines) + "\n", returncode=0)

    monkeypatch.setattr(wd.subprocess, "run", fake_run)
    monkeypatch.setattr(wd.os, "kill", lambda pid, sig: h.kills.append((pid, sig)))
    monkeypatch.setattr(wd.time, "sleep", lambda s: None)
    monkeypatch.setattr(codec_audit, "log_event",
                        lambda *a, **kw: h.events.append((a, kw)))
    yield h
    wd.idle_tracker.clear()


def _cycles(n=wd.IDLE_STRIKES_MAX):
    for _ in range(n):
        wd.check_cycle()


# ── targeting ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("cmd", GENERIC)
def test_generic_process_is_never_a_candidate(cmd):
    assert wd.is_codec_process(cmd) is False


@pytest.mark.parametrize("cmd", CODEC)
def test_codec_process_is_a_candidate(cmd):
    """Positive control: the same checker does flag CODEC's own processes."""
    assert wd.is_codec_process(cmd) is True


def test_ps_lists_full_args_not_comm(harness):
    wd.get_watched_processes()
    assert harness.ps_argv[0] == "ps"
    assert "pid,rss,%cpu,args" in harness.ps_argv
    assert not any("comm" in a for a in harness.ps_argv)


def test_idle_generic_processes_are_never_touched_even_when_enforced(harness):
    harness.cfg.write_text(json.dumps({"watchdog": {"enforce": True}}))
    harness.ps_rows = [(100 + i, 4_000_000, 0.0, cmd) for i, cmd in enumerate(GENERIC)]
    assert wd.get_watched_processes() == []
    _cycles(wd.IDLE_STRIKES_MAX + 2)
    assert harness.kills == []
    assert harness.events == []


def test_llm_base_url_port_counts_as_codec_model_port():
    ports = wd.model_ports({"llm_base_url": "http://127.0.0.1:8091/v1"})
    assert wd.is_codec_process("python3 -m mlx_vlm.server --port 8091", ports) is True
    assert wd.is_codec_process("python3 -m mlx_vlm.server --port 8091") is False


# ── NEVER_KILL on the full command line ─────────────────────────────────────


@pytest.mark.parametrize("cmd", [
    "python3 -m uvicorn codec_dashboard:app --host 0.0.0.0 --port 8090",
    "/usr/local/bin/python3.13 -u codec_dictate.py",
    "python3 codec_mcp.py",
    "python3 codec.py",
])
def test_never_kill_matches_on_args(cmd):
    proc = {"pid": 4242, "cmd": cmd, "rss_mb": 2000.0, "cpu": 0.0}
    assert wd.is_protected(proc, pm2_pids=set()) is True


def test_protected_codec_process_survives_enforce(harness):
    harness.cfg.write_text(json.dumps({"watchdog": {"enforce": True}}))
    harness.ps_rows = [(4242, 4_000_000, 0.0,
                        "python3 -m uvicorn codec_dashboard:app --port 8090")]
    _cycles(wd.IDLE_STRIKES_MAX + 2)
    assert harness.kills == []


# ── dry-run is the default ──────────────────────────────────────────────────


def test_dry_run_never_kills_but_reports_once(harness):
    harness.ps_rows = [(777, 4_000_000, 0.0, "python3 codec_session.py --task x")]
    _cycles(wd.IDLE_STRIKES_MAX + 5)

    assert harness.kills == [], "dry-run must never signal a process"
    names = [a[0] for a, _ in harness.events]
    assert names == ["watchdog_would_kill"], names  # reported once, not every cycle
    args, kw = harness.events[0]
    assert args[1] == "codec-watchdog"
    assert kw["extra"]["pid"] == 777
    assert "codec_session.py --task x" in kw["extra"]["cmd"]
    assert kw["extra"]["enforce"] is False

    notifs = json.loads(harness.notif.read_text())
    assert len(notifs) == 1 and "777" in notifs[0]["body"]


@pytest.mark.parametrize("cfg", [
    {}, {"watchdog": {}}, {"watchdog": {"enforce": False}},
    {"watchdog": {"enforce": "true"}}, {"watchdog": True},
])
def test_enforce_requires_exact_true(cfg):
    assert wd.enforce_enabled(cfg) is False


def test_enforce_true_kills_stuck_codec_process(harness):
    """Positive control for the dry-run test: same process, enforce on → SIGTERM."""
    harness.cfg.write_text(json.dumps({"watchdog": {"enforce": True}}))
    harness.ps_rows = [(777, 4_000_000, 0.0, "python3 codec_session.py --task x")]
    _cycles()
    assert (777, wd.signal.SIGTERM) in harness.kills
    assert "watchdog_kill" in [a[0] for a, _ in harness.events]


def test_active_process_resets_strikes(harness):
    harness.cfg.write_text(json.dumps({"watchdog": {"enforce": True}}))
    harness.ps_rows = [(777, 4_000_000, 0.0, "python3 codec_session.py")]
    _cycles(wd.IDLE_STRIKES_MAX - 1)
    harness.ps_rows = [(777, 4_000_000, 25.0, "python3 codec_session.py")]
    wd.check_cycle()
    harness.ps_rows = [(777, 4_000_000, 0.0, "python3 codec_session.py")]
    _cycles(wd.IDLE_STRIKES_MAX - 1)
    assert harness.kills == []


def test_logged_command_line_is_redacted_and_truncated():
    key = "sk-" + "a" * 40
    out = wd._short(f"python3 codec_session.py --api-key {key} " + "x" * 400)
    assert key not in out
    assert len(out) <= wd.CMD_LOG_MAX


def test_audit_is_log_event_not_http_post():
    """/api/audit is GET-only: the old POST dropped every kill record."""
    src = (REPO / "codec_watchdog.py").read_text()
    assert not hasattr(wd, "AUDIT_URL")
    assert "import requests" not in src and "requests.post" not in src
    assert "from codec_audit import log_event" in src
