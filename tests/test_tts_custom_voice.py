"""Custom Kokoro voice (voice-file path + tts_speed) is honoured by every
non-live speech caller instead of a hardcoded voice."""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_tts_speed_defaults_to_server_default():
    import codec_config
    assert isinstance(codec_config.TTS_SPEED, float)
    assert codec_config.TTS_SPEED > 0


def test_speech_callers_send_configured_speed_and_voice():
    for rel in ("codec_watcher.py", "codec_core.py", "codec_session.py",
                "codec_textassist.py", "skills/timer.py", "skills/tts_say.py"):
        src = (REPO / rel).read_text()
        assert re.search(r"[\"']speed[\"']", src), f"{rel} does not send speed"
    # No caller may hardcode the old voice in a request body any more.
    assert '"voice": "am_adam"' not in (REPO / "skills/timer.py").read_text()
    assert 'TTS_VOICE      = "am_adam"\nTASK_FILE' not in (REPO / "codec_watcher.py").read_text()


def test_live_voice_reads_tts_speed_with_old_default():
    src = (REPO / "codec_voice.py").read_text()
    assert '_cfg.get("tts_speed", 1.15)' in src
