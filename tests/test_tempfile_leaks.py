"""Tests for PR-4H (H-7 / H-8 / H-9 + bonus) — tempfile leaks fixed via
try/finally so the unlink runs on the failure/timeout path too:

  * H-7  codec_observer._screencapture_and_ocr_blocking  (PNG on OCR timeout)
  * H-8  codec_dashboard.run_code                        (Rust .out binary)
  * H-9  codec_session.Session.speak                     (afplay mp3, fire-and-forget)
  * bonus codec_session.Session.screenshot_ctx          (same H-7 pattern)

codec_observer + codec_session import cleanly → real behavioral tests;
run_code is async + invokes real compilers → source-invariant.

Reference: docs/PR4H-TEMPFILE-LEAKS-DESIGN.md, docs/audits/PHASE-1-RELIABILITY.md.
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


# ── H-7: observer OCR capture cleaned even on subprocess timeout ───────────────


def test_screencapture_unlinks_png_on_timeout(monkeypatch):
    import codec_observer
    before = set(glob.glob(os.path.join(tempfile.gettempdir(), "codec_obs_*.png")))

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="screencapture", timeout=2)
    monkeypatch.setattr(codec_observer.subprocess, "run", boom)

    out = codec_observer._screencapture_and_ocr_blocking()
    assert out == ""  # never raises, returns empty on failure
    after = set(glob.glob(os.path.join(tempfile.gettempdir(), "codec_obs_*.png")))
    assert after <= before, f"H-7: leaked capture(s) on timeout: {after - before}"


# ── bonus: codec_session.screenshot_ctx cleaned on screencapture failure ──────


def test_screenshot_ctx_unlinks_on_failure(monkeypatch):
    import codec_session
    created = []
    real_ntf = tempfile.NamedTemporaryFile

    def recording_ntf(*a, **k):
        f = real_ntf(*a, **k)
        created.append(f.name)
        return f
    monkeypatch.setattr(codec_session.tempfile, "NamedTemporaryFile", recording_ntf)

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="screencapture", timeout=5)
    monkeypatch.setattr(codec_session.subprocess, "run", boom)

    out = codec_session.Session.screenshot_ctx(SimpleNamespace())
    assert out == ""
    assert created, "screenshot_ctx should have created a tempfile"
    for p in created:
        assert not os.path.exists(p), f"bonus: screenshot_ctx leaked {p} on failure"


# ── H-9: speak() unlinks the mp3 after afplay finishes ────────────────────────


def test_speak_unlinks_mp3_after_playback(monkeypatch):
    import codec_session
    import requests

    class FakeResp:
        status_code = 200

        def iter_content(self, n):
            yield b"audio-bytes"
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResp())

    popen_paths = []

    class FakeProc:
        def wait(self):
            return 0
    def fake_popen(cmd, *a, **k):
        popen_paths.append(cmd[-1])  # afplay <path>
        return FakeProc()
    monkeypatch.setattr(codec_session.subprocess, "Popen", fake_popen)

    # Run the cleanup thread synchronously so the assertion is deterministic.
    class SyncThread:
        def __init__(self, target=None, daemon=None, **k):
            self._target = target

        def start(self):
            if self._target:
                self._target()
    monkeypatch.setattr(codec_session.threading, "Thread", SyncThread, raising=False)

    stub = SimpleNamespace(tts_engine="kokoro", tts_voice="am_adam",
                           kokoro_url="http://127.0.0.1:8085/v1/audio/speech",
                           kokoro_model="kokoro")
    codec_session.Session.speak(stub, "hello world")

    assert popen_paths, "afplay should have been spawned for the mp3"
    for p in popen_paths:
        assert not os.path.exists(p), f"H-9: TTS mp3 leaked: {p}"


# ── H-8: source invariant — run_code unlinks the Rust .out binary too ─────────


def test_run_code_unlinks_rust_out():
    src = (REPO / "codec_dashboard.py").read_text()
    body = src[src.index("async def run_code("):]
    body = body[:body.index("\n@app.", 1)]
    # Slice the cleanup block specifically — `tmp.name + ".out"` also appears in
    # the rust cmd_map, so a whole-body check would false-pass.
    fin = body[body.rindex("finally:"):]
    assert "unlink" in fin, "run_code's finally must unlink its tempfile"
    assert ".out" in fin, (
        "H-8: run_code's finally must also unlink the Rust-compiled `<tmp>.out` binary"
    )
