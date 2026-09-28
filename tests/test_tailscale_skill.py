"""skills/tailscale.py: on runs `tailscale up`, off runs `tailscale down`,
questions only read status. The Tailscale CLI is faked; nothing touches the
network."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

RUNNING = {"BackendState": "Running", "Peer": {"a": {"Online": True}, "b": {"Online": False}}}
STOPPED = {"BackendState": "Stopped", "Peer": {}}


def _load():
    spec = importlib.util.spec_from_file_location("_skill_tailscale", REPO / "skills" / "tailscale.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _R:
    def __init__(self, stdout="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, "", returncode


def _fake(monkeypatch, mod, states):
    """Fake CLI: each `status` call answers the next state (None = app not
    running); `up`/`down`/`open` succeed. Returns the list of commands run."""
    calls, queue = [], list(states)
    monkeypatch.setattr(mod, "_cli", lambda: "/fake/tailscale")
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)

    def _run(cmd, **kw):
        calls.append(cmd[1] if cmd[0] == "/fake/tailscale" else cmd[0])
        if cmd[0] == "/fake/tailscale" and cmd[1] == "status":
            st = queue.pop(0)
            return _R("" if st is None else json.dumps(st))
        return _R()
    monkeypatch.setattr(mod.subprocess, "run", _run)
    return calls


def test_turn_on_runs_up(monkeypatch):
    mod = _load()
    calls = _fake(monkeypatch, mod, [STOPPED, RUNNING])
    assert mod.run("turn on tailscale") == "Tailscale is on. 1 of 2 other devices online."
    assert calls == ["status", "up", "status"]


def test_turn_on_waits_for_the_device_list(monkeypatch):
    mod = _load()
    calls = _fake(monkeypatch, mod, [STOPPED, {"BackendState": "Running", "Peer": {}}, RUNNING])
    assert mod.run("turn on tailscale") == "Tailscale is on. 1 of 2 other devices online."
    assert calls == ["status", "up", "status", "status"]


def test_turn_off_runs_down(monkeypatch):
    mod = _load()
    calls = _fake(monkeypatch, mod, [RUNNING, STOPPED])
    assert mod.run("turn off tail scale") == "Tailscale is off."
    assert calls == ["status", "down", "status"]


def test_question_only_reads_status(monkeypatch):
    mod = _load()
    calls = _fake(monkeypatch, mod, [RUNNING])
    assert mod.run("is tailscale on") == "Tailscale is on. 1 of 2 other devices online."
    assert calls == ["status"]


def test_needs_login_never_runs_up(monkeypatch):
    mod = _load()
    calls = _fake(monkeypatch, mod, [{"BackendState": "NeedsLogin"}])
    assert "needs a login" in mod.run("turn on tailscale")
    assert calls == ["status"]


def test_app_not_running_is_launched_first(monkeypatch):
    mod = _load()
    calls = _fake(monkeypatch, mod, [None, STOPPED, RUNNING])
    assert mod.run("start tailscale").startswith("Tailscale is on.")
    assert calls == ["status", "open", "status", "up", "status"]


def test_missing_cli_says_not_installed(monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "_cli", lambda: None)
    assert mod.run("turn on tailscale") == "Tailscale is not installed on this Mac."


def test_tailscale_fires_from_chat():
    from routes.chat import CHAT_SKILL_ALLOWLIST
    assert "tailscale" in CHAT_SKILL_ALLOWLIST
