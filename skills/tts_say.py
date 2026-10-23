"""CODEC Skill: Speak text aloud via Kokoro TTS"""
SKILL_NAME = "tts_say"
SKILL_DESCRIPTION = "Speak a message out loud via Kokoro TTS (voice confirmation)"
SKILL_TRIGGERS = [
    "speak", "say out loud", "say aloud", "read aloud",
    "tts", "voice say", "announce", "speak this",
    "tell me out loud", "say it",
]
# Hidden from MCP (2026-09-24): Claude MCP Directory policy bars tools that
# use AI-generated audio (Kokoro). Still works from voice and chat.
SKILL_MCP_EXPOSE = False

import os
import re
import json
import tempfile
import subprocess
import requests

import codec_tts

_CFG_PATH = os.path.expanduser("~/.codec/config.json")
try:
    _cfg = json.load(open(_CFG_PATH))
except Exception:
    _cfg = {}

KOKORO_URL   = _cfg.get("tts_url", "http://localhost:8085/v1/audio/speech")
# Read "tts_model" (canonical key in config.json), fall back to legacy "kokoro_model"
KOKORO_MODEL = _cfg.get("tts_model", _cfg.get("kokoro_model", "mlx-community/Kokoro-82M-bf16"))
TTS_VOICE    = _cfg.get("tts_voice", "af_bella")
TTS_SPEED    = float(_cfg.get("tts_speed", 1.0))

_WRITE_VERBS = (
    "speak", "say out loud", "say aloud", "read aloud",
    "tts", "voice say", "announce", "speak this",
    "tell me out loud", "say it", "say",
)


def _extract_text(task: str) -> str:
    """Pull the payload out of 'say X' / 'speak: X' / 'announce "X"'."""
    t = task.strip()
    low = t.lower()
    for v in sorted(_WRITE_VERBS, key=len, reverse=True):
        m = re.match(r'^\s*' + re.escape(v) + r'\b', low)
        if m:
            t = t[m.end():].strip()
            break
    t = re.sub(r'^\s*[:,\-]+\s*', '', t).strip()
    return t.strip('"\'').strip()


def run(task, app="", ctx=""):
    text = _extract_text(task)
    if not text or len(text) < 1:
        return "What should I say? (e.g. 'say All done captain')"

    # Clean for TTS
    clean = text[:500]
    clean = re.sub(r'\*+', '', clean)
    clean = re.sub(r'#+\s*', '', clean)
    clean = clean.replace('"', '').strip()

    try:
        spoken = codec_tts.speak(
            KOKORO_URL, clean, "speak",
            {"model": KOKORO_MODEL, "voice": TTS_VOICE, "speed": TTS_SPEED, "response_format": "wav"},
            cfg=_cfg, timeout=30)
        if spoken.audio is None:
            # Fallback to macOS say
            subprocess.Popen(["say", clean])
            if spoken.unreachable:
                return f"🔊 (macOS say fallback — Kokoro offline) {clean}"
            return f"🔊 (fallback) Speaking: {clean}"

        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.write(spoken.audio)
        tmp.close()
        subprocess.Popen(["afplay", tmp.name],
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return f"🔊 Speaking: {clean}"
    except requests.exceptions.ConnectionError:
        subprocess.Popen(["say", clean])
        return f"🔊 (macOS say fallback — Kokoro offline) {clean}"
    except Exception as e:
        return f"TTS error: {e}"
