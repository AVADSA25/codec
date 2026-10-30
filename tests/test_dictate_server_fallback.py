"""codec_dictate without faster-whisper uses the whisper-stt server (audit P-011)."""
import importlib.util
import sys
import types
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent


def _load_dictate(monkeypatch, faster_whisper):
    """Import a fresh codec_dictate with its desktop-only imports replaced."""
    for name in ("pynput", "pynput.keyboard", "pyperclip", "codec_overlays", "codec_paste"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    sys.modules["pynput"].keyboard = sys.modules["pynput.keyboard"]
    monkeypatch.setitem(sys.modules, "faster_whisper", faster_whisper)
    spec = importlib.util.spec_from_file_location("codec_dictate_under_test", REPO / "codec_dictate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_without_faster_whisper_the_model_is_the_server(monkeypatch, tmp_path):
    mod = _load_dictate(monkeypatch, None)       # None in sys.modules makes the import fail
    assert mod.WhisperModel is None
    mod.load_model()
    assert isinstance(mod.model, mod._ServerModel) and mod.model_loaded.is_set()

    wav = tmp_path / "rec.wav"
    wav.write_bytes(b"RIFF")
    sent = {}

    class Reply:
        def raise_for_status(self): pass
        def json(self): return {"text": " hello world "}

    def fake_post(url, files, data, timeout):
        sent.update(url=url, name=files["file"][0], data=data)
        return Reply()
    monkeypatch.setattr("requests.post", fake_post)

    segments, info = mod.model.transcribe(str(wav), language="en", task="transcribe", beam_size=5, vad_filter=True)
    assert " ".join(s.text.strip() for s in segments) == "hello world"
    assert info.language == "en"
    assert sent["data"] == {"pin_language": "en"}      # the English pin is kept
    assert sent["url"] == mod.WHISPER_SERVER


def test_silence_gives_no_segments(monkeypatch, tmp_path):
    mod = _load_dictate(monkeypatch, None)
    wav = tmp_path / "rec.wav"
    wav.write_bytes(b"RIFF")

    class Reply:
        def raise_for_status(self): pass
        def json(self): return {"text": ""}
    monkeypatch.setattr("requests.post", lambda *a, **k: Reply())
    segments, _info = mod._ServerModel().transcribe(str(wav))
    assert segments == []


def test_with_faster_whisper_installed_the_local_model_is_unchanged(monkeypatch):
    made = []

    class FakeModel:
        def __init__(self, size, device, compute_type):
            made.append((size, device, compute_type))
    fw = types.ModuleType("faster_whisper")
    fw.WhisperModel = FakeModel
    mod = _load_dictate(monkeypatch, fw)
    mod.load_model()
    assert isinstance(mod.model, FakeModel)
    assert made == [(mod.WHISPER_MODEL_SIZE, mod.WHISPER_DEVICE, mod.WHISPER_COMPUTE)]
