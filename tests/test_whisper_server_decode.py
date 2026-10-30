"""whisper_server reads audio itself when ffmpeg is absent (audit P-011)."""
import importlib
import io
import sys
import types
import wave

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("soundfile")
pytest.importorskip("multipart")


@pytest.fixture
def ws(monkeypatch):
    calls = []
    fake = types.ModuleType("mlx_whisper")

    def transcribe(audio, path_or_hf_repo=None, **kw):
        calls.append((audio, kw))
        return {"text": " ok "}
    fake.transcribe = transcribe
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake)
    sys.modules.pop("whisper_server", None)
    mod = importlib.import_module("whisper_server")
    mod.calls = calls
    yield mod
    sys.modules.pop("whisper_server", None)


def _wav(samples, rate=16000, channels=1):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.asarray(samples, dtype="<i2").tobytes())
    return buf.getvalue()


def test_with_ffmpeg_the_path_goes_through_unchanged(ws, monkeypatch, tmp_path):
    monkeypatch.setattr(ws.shutil, "which", lambda name: "/opt/homebrew/bin/ffmpeg")
    assert ws._audio_input(str(tmp_path / "x.wav")) == str(tmp_path / "x.wav")


def test_without_ffmpeg_a_16k_wav_becomes_float_samples(ws, monkeypatch, tmp_path):
    monkeypatch.setattr(ws.shutil, "which", lambda name: None)
    p = tmp_path / "a.wav"
    p.write_bytes(_wav([0, 16384, -16384, 32767]))
    out = ws._audio_input(str(p))
    assert out.dtype == np.float32 and out.shape == (4,)
    assert np.allclose(out, [0, 0.5, -0.5, 32767 / 32768], atol=1e-4)


def test_without_ffmpeg_stereo_is_averaged_to_mono(ws, monkeypatch, tmp_path):
    monkeypatch.setattr(ws.shutil, "which", lambda name: None)
    p = tmp_path / "s.wav"
    p.write_bytes(_wav([16384, 0, 16384, 0], channels=2))
    assert np.allclose(ws._audio_input(str(p)), [0.25, 0.25], atol=1e-4)


def test_without_ffmpeg_other_rates_are_resampled_to_16k(ws, monkeypatch, tmp_path):
    pytest.importorskip("scipy")
    monkeypatch.setattr(ws.shutil, "which", lambda name: None)
    p = tmp_path / "r.wav"
    p.write_bytes(_wav(np.zeros(8000), rate=8000))
    assert ws._audio_input(str(p)).shape == (16000,)


def test_without_ffmpeg_unreadable_audio_is_refused(ws, monkeypatch, tmp_path):
    monkeypatch.setattr(ws.shutil, "which", lambda name: None)
    p = tmp_path / "bad.wav"
    p.write_bytes(b"not audio at all")
    with pytest.raises(ValueError):
        ws._audio_input(str(p))


def test_endpoint_passes_samples_and_the_language_pin(ws, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(ws.shutil, "which", lambda name: None)
    client = TestClient(ws.app)
    r = client.post("/v1/audio/transcriptions", files={"file": ("a.wav", _wav([0, 1000, -1000]), "audio/wav")},
                    data={"pin_language": "en"})
    assert r.status_code == 200 and r.json() == {"text": "ok"}
    audio, kw = ws.calls[-1]
    assert isinstance(audio, np.ndarray) and kw == {"language": "en"}

    client.post("/v1/audio/transcriptions", files={"file": ("a.wav", _wav([0, 1000]), "audio/wav")})
    assert ws.calls[-1][1] == {}, "clients that send no pin keep automatic language detection"


def test_endpoint_answers_415_for_audio_it_cannot_read(ws, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(ws.shutil, "which", lambda name: None)
    r = TestClient(ws.app).post("/v1/audio/transcriptions", files={"file": ("a.webm", b"\x1aE\xdf\xa3junk", "audio/webm")})
    assert r.status_code == 415 and "ffmpeg" in r.json()["error"]
