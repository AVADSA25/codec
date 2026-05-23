"""Tests for PR-4A (C-1) — codec.py graceful shutdown.

The main daemon used to install no-op SIGINT/SIGTERM handlers (orphaning the sox
recording subprocess + tkinter overlays + leaking temp files on every PM2
restart). _graceful_shutdown terminates those children + unlinks the temp audio,
and exits 0 on the signal path. Reference: docs/PR4A-CODEC-GRACEFUL-SHUTDOWN-DESIGN.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import codec  # noqa: E402


class _FakeProc:
    def __init__(self, raise_on_terminate=False):
        self.terminated = False
        self.waited = False
        self._raise = raise_on_terminate

    def terminate(self):
        self.terminated = True
        if self._raise:
            raise RuntimeError("terminate boom")

    def wait(self, timeout=None):
        self.waited = True


def test_graceful_shutdown_terminates_and_unlinks(monkeypatch, tmp_path):
    audio = tmp_path / "rec.wav"
    audio.write_bytes(b"x")
    rec, ovl = _FakeProc(), _FakeProc()
    monkeypatch.setattr(codec, "state", {
        "rec_proc": rec, "overlay_proc": ovl, "audio_path": str(audio)})
    codec._graceful_shutdown()   # atexit path (signum=None) — no exit
    assert rec.terminated and rec.waited
    assert ovl.terminated
    assert not audio.exists()
    assert codec.state["rec_proc"] is None
    assert codec.state["overlay_proc"] is None
    assert codec.state["audio_path"] is None


def test_graceful_shutdown_never_raises(monkeypatch, tmp_path):
    audio = tmp_path / "rec.wav"
    audio.write_bytes(b"x")
    rec = _FakeProc(raise_on_terminate=True)
    monkeypatch.setattr(codec, "state", {
        "rec_proc": rec, "overlay_proc": None, "audio_path": str(audio)})
    codec._graceful_shutdown()   # must not raise despite terminate() throwing
    assert not audio.exists()     # cleanup continued past the failing terminate


def test_graceful_shutdown_handles_empty_state(monkeypatch):
    monkeypatch.setattr(codec, "state", {
        "rec_proc": None, "overlay_proc": None, "audio_path": None})
    codec._graceful_shutdown()   # no procs, no file — must be a clean no-op


def test_graceful_shutdown_signal_path_exits(monkeypatch):
    monkeypatch.setattr(codec, "state", {
        "rec_proc": None, "overlay_proc": None, "audio_path": None})
    with pytest.raises(SystemExit):
        codec._graceful_shutdown(15, None)   # signum set → sys.exit(0)


def test_no_noop_signal_handlers():
    src = (REPO / "codec.py").read_text()
    assert "signal.signal(signal.SIGINT, lambda *a: None)" not in src
    assert "signal.signal(signal.SIGTERM, lambda *a: None)" not in src
    assert "atexit.register(_graceful_shutdown)" in src
