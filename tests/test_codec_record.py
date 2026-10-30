"""codec_record: sox stays first, sounddevice records when sox is absent (audit P-033)."""
import subprocess
import sys
import time
import types

import numpy as np
import pytest

import codec_record

soundfile = pytest.importorskip("soundfile")
RATE = 16000
BLOCK = int(RATE * codec_record.BLOCK_SECONDS)


def _loud():
    return (np.sin(np.linspace(0, 200, BLOCK)) * 12000).astype("int16").reshape(-1, 1)


def _quiet():
    return np.zeros((BLOCK, 1), dtype="int16")


class FakeStream:
    """Hands out scripted blocks instantly; repeats the last one when the script ends."""
    instances = []

    def __init__(self, samplerate, channels, dtype, device=None):
        self.args = dict(samplerate=samplerate, channels=channels, dtype=dtype, device=device)
        self.script = FakeStream.script
        self.reads = 0
        self.started = self.stopped = self.closed = False
        FakeStream.instances.append(self)

    def start(self): self.started = True
    def stop(self): self.stopped = True
    def close(self): self.closed = True

    def read(self, frames):
        assert frames == BLOCK
        block = self.script[min(self.reads, len(self.script) - 1)]
        self.reads += 1
        time.sleep(0.0005)
        return block, False


@pytest.fixture
def no_sox(monkeypatch):
    monkeypatch.setattr(codec_record.shutil, "which", lambda name: None)
    monkeypatch.setattr(codec_record.os.path, "exists", lambda p: False if "sox" in str(p) else True)
    FakeStream.instances = []
    fake = types.ModuleType("sounddevice")
    fake.InputStream = FakeStream
    fake.query_devices = lambda: [{"name": "Built-in Output", "max_input_channels": 0},
                                  {"name": "MacBook Pro Microphone", "max_input_channels": 1}]
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    return fake


@pytest.fixture
def with_sox(monkeypatch):
    monkeypatch.setattr(codec_record.shutil, "which", lambda name: "/opt/homebrew/bin/sox")
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(("run", cmd, kw)))
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: calls.append(("popen", cmd, kw)) or "proc")
    return calls


# ── sox installed: the old command lines, unchanged ─────────────────────────
def test_sox_chunk_is_the_old_command(with_sox):
    codec_record.record_chunk("/t/a.wav", 2, 16000, "default")
    kind, cmd, kw = with_sox[0]
    assert cmd == ["/opt/homebrew/bin/sox", "-t", "coreaudio", "default", "-r", "16000", "-c", "1", "-b", "16",
                   "-e", "signed-integer", "/t/a.wav", "trim", "0", "2"]
    assert kw == {"timeout": 5, "capture_output": True}


def test_sox_follow_up_is_the_old_command(with_sox):
    codec_record.record_until_silence("/t/b.wav", 8, 16000, "MacBook Mic")
    _, cmd, kw = with_sox[0]
    assert cmd == ["/opt/homebrew/bin/sox", "-t", "coreaudio", "MacBook Mic", "-r", "16000", "-c", "1", "-b", "16",
                   "-e", "signed-integer", "/t/b.wav", "trim", "0", "8",
                   "silence", "1", "0.1", "2%", "1", "1.5", "2%"]
    assert kw == {"timeout": 12, "capture_output": True}


def test_sox_open_ended_recording_is_the_old_popen(with_sox):
    assert codec_record.start_recording("/t/c.wav", 16000) == "proc"
    _, cmd, kw = with_sox[0]
    assert cmd == ["/opt/homebrew/bin/sox", "-t", "coreaudio", "default", "-r", "16000", "-c", "1", "-b", "16",
                   "-e", "signed-integer", "/t/c.wav"]
    assert kw == {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}


def test_sox_is_found_in_the_homebrew_folder_when_path_lacks_it(monkeypatch):
    monkeypatch.setattr(codec_record.shutil, "which", lambda name: None)
    monkeypatch.setattr(codec_record.os.path, "exists", lambda p: p == "/opt/homebrew/bin/sox")
    assert codec_record.sox_command() == "/opt/homebrew/bin/sox"


# ── no sox: sounddevice ─────────────────────────────────────────────────────
def test_chunk_without_sox_records_the_asked_length(no_sox, tmp_path):
    FakeStream.script = [_loud()]
    out = tmp_path / "chunk.wav"
    codec_record.record_chunk(str(out), 1.0)
    info = soundfile.info(str(out))
    assert (info.samplerate, info.channels, info.subtype, info.frames) == (16000, 1, "PCM_16", 10 * BLOCK)
    s = FakeStream.instances[0]
    assert s.args == dict(samplerate=16000, channels=1, dtype="int16", device=None) and s.stopped and s.closed


def test_a_named_device_is_looked_up_and_unknown_falls_back_to_default(no_sox, tmp_path):
    FakeStream.script = [_quiet()]
    codec_record.record_chunk(str(tmp_path / "a.wav"), 0.1, device="macbook pro")
    codec_record.record_chunk(str(tmp_path / "b.wav"), 0.1, device="Nonexistent Mic")
    assert [s.args["device"] for s in FakeStream.instances] == [1, None]


def test_open_ended_recording_writes_until_terminated(no_sox, tmp_path):
    FakeStream.script = [_loud()]
    out = tmp_path / "ptt.wav"
    rec = codec_record.start_recording(str(out), 16000)
    assert rec.poll() is None
    time.sleep(0.05)
    rec.terminate()
    assert rec.wait(timeout=3) == 0 and rec.poll() == 0
    assert soundfile.info(str(out)).frames >= BLOCK
    assert FakeStream.instances[0].closed


def test_follow_up_waits_for_speech_then_stops_1_5_s_after_it_ends(no_sox, tmp_path):
    FakeStream.script = [_quiet()] * 5 + [_loud()] * 10 + [_quiet()] * 40
    out = tmp_path / "followup.wav"
    codec_record.record_until_silence(str(out), 20, 16000)
    # leading silence dropped; 10 loud blocks + the 15 quiet blocks that ended it
    assert soundfile.info(str(out)).frames == 25 * BLOCK
    assert FakeStream.instances[0].reads == 5 + 10 + 15


def test_follow_up_with_no_speech_runs_to_the_cap_and_writes_nothing(no_sox, tmp_path):
    FakeStream.script = [_quiet()]
    out = tmp_path / "none.wav"
    codec_record.record_until_silence(str(out), 1.0, 16000)
    assert soundfile.info(str(out)).frames == 0 and FakeStream.instances[0].reads == 10


def test_follow_up_that_never_stops_talking_is_cut_at_the_cap(no_sox, tmp_path):
    FakeStream.script = [_loud()]
    out = tmp_path / "long.wav"
    codec_record.record_until_silence(str(out), 2.0, 16000)
    assert soundfile.info(str(out)).frames == 20 * BLOCK


def test_a_short_pause_inside_a_sentence_does_not_end_it(no_sox, tmp_path):
    FakeStream.script = [_loud()] * 5 + [_quiet()] * 14 + [_loud()] * 5 + [_quiet()] * 40
    out = tmp_path / "pause.wav"
    codec_record.record_until_silence(str(out), 20, 16000)
    assert soundfile.info(str(out)).frames == (5 + 14 + 5 + 15) * BLOCK


def test_without_sox_or_sounddevice_the_caller_gets_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(codec_record.shutil, "which", lambda name: None)
    monkeypatch.setattr(codec_record.os.path, "exists", lambda p: False)
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    with pytest.raises(ImportError):
        codec_record.record_chunk(str(tmp_path / "x.wav"), 1)
