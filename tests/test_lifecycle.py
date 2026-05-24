"""Tests for PR-4A-2 (H-1) — codec_lifecycle.install_handlers + the 5 daemon
wirings.

PM2 sends SIGTERM on restart; Python's default SIGTERM disposition skips
atexit/finally. `install_handlers` registers SIGTERM+SIGINT handlers that run a
cleanup once (idempotent, never-raises) then sys.exit(0), plus an atexit hook.

Behavioral tests monkeypatch signal.signal + atexit.register (no real signals).
Source-invariant tests confirm each daemon wires the helper without importing
the daemon (several pull pynput / native deps not present in CI).

Reference: docs/PR4A2-LIFECYCLE-HELPER-DESIGN.md, docs/audits/PHASE-1-RELIABILITY.md H-1.
"""
from __future__ import annotations

import signal as _signal
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import codec_lifecycle as cl  # noqa: E402


@pytest.fixture
def captured(monkeypatch):
    """Capture signal + atexit registrations instead of installing real ones."""
    cap = {"handlers": {}, "atexit": []}
    monkeypatch.setattr(cl.signal, "signal",
                        lambda signum, handler: cap["handlers"].__setitem__(signum, handler))
    monkeypatch.setattr(cl.atexit, "register",
                        lambda fn: cap["atexit"].append(fn))
    return cap


# ── registration ──────────────────────────────────────────────────────────────


def test_registers_sigterm_sigint_and_atexit(captured):
    cl.install_handlers(lambda: None, name="t")
    assert _signal.SIGTERM in captured["handlers"], "must register a SIGTERM handler"
    assert _signal.SIGINT in captured["handlers"], "must register a SIGINT handler"
    assert len(captured["atexit"]) == 1, "must register exactly one atexit hook"


# ── idempotency ───────────────────────────────────────────────────────────────


def test_cleanup_runs_once(captured):
    n = {"v": 0}
    wrapped = cl.install_handlers(lambda: n.__setitem__("v", n["v"] + 1), name="t")
    wrapped()
    wrapped()
    wrapped()
    assert n["v"] == 1, "cleanup must run exactly once even if invoked repeatedly"


def test_atexit_and_signal_share_run_once_guard(captured):
    n = {"v": 0}
    cl.install_handlers(lambda: n.__setitem__("v", n["v"] + 1), name="t")
    atexit_fn = captured["atexit"][0]
    sigterm = captured["handlers"][_signal.SIGTERM]
    atexit_fn()                       # normal-exit path runs cleanup
    with pytest.raises(SystemExit):   # then a late SIGTERM must NOT re-run it
        sigterm(_signal.SIGTERM, None)
    assert n["v"] == 1


# ── never raises ──────────────────────────────────────────────────────────────


def test_cleanup_never_raises(captured):
    def boom():
        raise RuntimeError("teardown blew up")
    wrapped = cl.install_handlers(boom, name="t")
    # Must swallow the cleanup error.
    wrapped()
    # And must still be marked done (no retry storm).
    wrapped()


# ── signal path exits, atexit path does not ───────────────────────────────────


def test_signal_path_calls_sys_exit_zero(captured):
    n = {"v": 0}
    cl.install_handlers(lambda: n.__setitem__("v", n["v"] + 1), name="t")
    handler = captured["handlers"][_signal.SIGTERM]
    with pytest.raises(SystemExit) as ei:
        handler(_signal.SIGTERM, None)
    assert ei.value.code == 0, "signal path must sys.exit(0)"
    assert n["v"] == 1, "cleanup must run before exit"


def test_atexit_path_does_not_exit(captured):
    cl.install_handlers(lambda: None, name="t")
    atexit_fn = captured["atexit"][0]
    # Must not raise SystemExit on the normal-exit path.
    atexit_fn()


def test_exit_on_signal_false_skips_exit(captured):
    cl.install_handlers(lambda: None, name="t", exit_on_signal=False)
    handler = captured["handlers"][_signal.SIGTERM]
    handler(_signal.SIGTERM, None)  # must not raise SystemExit


# ── non-main-thread degradation ───────────────────────────────────────────────


def test_signal_install_failure_degrades_to_atexit(monkeypatch):
    cap = {"atexit": []}

    def boom_signal(signum, handler):
        raise ValueError("signal only works in main thread")
    monkeypatch.setattr(cl.signal, "signal", boom_signal)
    monkeypatch.setattr(cl.atexit, "register", lambda fn: cap["atexit"].append(fn))

    # Must not propagate the ValueError; must still register atexit.
    wrapped = cl.install_handlers(lambda: None, name="t")
    assert callable(wrapped)
    assert len(cap["atexit"]) == 1


# ── source invariants: the 5 daemons wire the helper ──────────────────────────

_DAEMONS = [
    "codec_autopilot.py",
    "codec_observer.py",
    "codec_agent_runner.py",
    "codec_imessage.py",
    "codec_telegram.py",
]


@pytest.mark.parametrize("fname", _DAEMONS)
def test_daemon_wires_lifecycle(fname):
    src = (REPO / fname).read_text()
    assert "codec_lifecycle" in src, f"{fname} must import codec_lifecycle"
    assert "install_handlers(" in src, f"{fname} must call install_handlers()"
