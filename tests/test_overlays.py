"""Tests for codec_overlays Swift-channel routing.

Redesign (2026-06): the public overlay functions emit JSON events to the
running Swift CODECOverlay NSPanel (~/.codec/overlay_events.jsonl) instead of
spawning tkinter, with tkinter kept as an automatic fallback when the Swift
renderer isn't running.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import codec_overlays  # noqa: E402


def _events(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def _swifted(monkeypatch, tmp_path):
    """Force the Swift-alive branch + a temp events file; silence the sound."""
    ev = tmp_path / "overlay_events.jsonl"
    monkeypatch.setattr(codec_overlays, "_EVENTS", str(ev))
    monkeypatch.setattr(codec_overlays, "_swift_alive", lambda: True)
    monkeypatch.setattr(codec_overlays, "_play_sound", lambda *_a, **_k: None)
    return str(ev)


def test_toggle_on_emits_toggle_on_with_shortcuts(tmp_path, monkeypatch):
    ev = _swifted(monkeypatch, tmp_path)
    codec_overlays.show_toggle_overlay(True, "F18=voice  F16=text")
    last = _events(ev)[-1]
    assert last["type"] == "toggle_on"
    assert last["shortcuts"] == "F18=voice  F16=text"


def test_toggle_off_emits_toggle_off(tmp_path, monkeypatch):
    ev = _swifted(monkeypatch, tmp_path)
    codec_overlays.show_toggle_overlay(False)
    assert _events(ev)[-1]["type"] == "toggle_off"


def test_recording_emits_recording_start_with_key(tmp_path, monkeypatch):
    ev = _swifted(monkeypatch, tmp_path)
    codec_overlays.show_recording_overlay("F18")
    last = _events(ev)[-1]
    assert last["type"] == "recording_start"
    assert "F18" in last["subtitle"]


def test_processing_emits_transcribing(tmp_path, monkeypatch):
    ev = _swifted(monkeypatch, tmp_path)
    codec_overlays.show_processing_overlay("Transcribing...")
    last = _events(ev)[-1]
    assert last["type"] == "transcribing"
    assert last["text"] == "Transcribing..."


def test_notify_emits_notify_with_color_and_seconds(tmp_path, monkeypatch):
    ev = _swifted(monkeypatch, tmp_path)
    codec_overlays.show_overlay("hello", "#00aaff", 3000)
    last = _events(ev)[-1]
    assert last["type"] == "notify"
    assert last["text"] == "hello"
    assert last["color"] == "#00aaff"
    assert abs(last["duration"] - 3.0) < 0.001  # ms -> seconds


def test_recording_stop_emits_recording_stop(tmp_path, monkeypatch):
    ev = _swifted(monkeypatch, tmp_path)
    codec_overlays.show_recording_stop()
    assert _events(ev)[-1]["type"] == "recording_stop"


def test_falls_back_to_tkinter_when_swift_down(tmp_path, monkeypatch):
    ev = tmp_path / "overlay_events.jsonl"
    monkeypatch.setattr(codec_overlays, "_EVENTS", str(ev))
    monkeypatch.setattr(codec_overlays, "_swift_alive", lambda: False)
    called = {"tk": False}
    monkeypatch.setattr(codec_overlays, "_tk_overlay",
                        lambda *a, **k: called.__setitem__("tk", True))
    codec_overlays.show_overlay("hi")
    assert called["tk"] is True
    assert not os.path.exists(str(ev))  # nothing emitted to the Swift channel
