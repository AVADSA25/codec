"""CODEC speech requests: one place that decides which voice a request asks for (docs/CODEC-VOICE-02-DESIGN.md).

With `tts_clone` in ~/.codec/config.json, the callers that read text aloud (purpose "speak") ask Qwen3-TTS Base to clone a
reference clip, so the voice is the one the owner picked. Anything wrong (no clip, a slow or failing server, a reply that is
not audio) falls back once to the Kokoro request the caller already built, so speech never stops.
Without `tts_clone` every request is exactly what it was.

    "tts_clone": {"enabled_for": ["speak"], "model": "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16",
                  "ref_audio": "~/.codec/voices/codec-02/reference.wav",
                  "ref_text_file": "~/.codec/voices/codec-02/reference.txt"}

The live conversation (purpose "live") is not enabled by default: a clone takes about 3 seconds before a sentence starts.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Optional

import requests

log = logging.getLogger("codec.tts")

CLONE_TIMEOUT = 45          # seconds to wait for the clone before the Kokoro request is tried
WARM_TEXT = "Ready."
_FALLBACK_EVERY = 60.0      # one audit line per reason per minute, so a stopped server cannot flood the log
_last_note: dict = {}
_note_lock = threading.Lock()


@dataclass
class Spoken:
    """What speak() found: the audio and its type, or nothing."""
    audio: Optional[bytes] = None
    content_type: str = ""
    cloned: bool = False          # the audio came from voice 02 (the clone), not from the Kokoro request
    unreachable: bool = False     # the last try got no answer from the server at all
    status: int = 0               # the last HTTP status seen


def load_config() -> dict:
    try:
        with open(os.path.expanduser("~/.codec/config.json")) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def clone_settings(cfg: Optional[dict], purpose: Optional[str]) -> Optional[dict]:
    """The clone's request fields when it is configured, enabled for this purpose and its reference clip is there, else None."""
    c = (cfg or {}).get("tts_clone")
    if not isinstance(c, dict) or not purpose:
        return None
    enabled = c.get("enabled_for")
    if not isinstance(enabled, list) or purpose not in enabled:
        return None
    model = str(c.get("model") or "").strip()
    ref = os.path.expanduser(str(c.get("ref_audio") or ""))
    if not model or not ref or not os.path.isfile(ref) or os.path.getsize(ref) == 0:
        return None
    ref_text = str(c.get("ref_text") or "").strip()
    if not ref_text and c.get("ref_text_file"):
        try:
            with open(os.path.expanduser(str(c["ref_text_file"])), encoding="utf-8") as f:
                ref_text = f.read().strip()
        except OSError:
            ref_text = ""
    if not ref_text:
        return None
    return {"model": model, "ref_audio": ref, "ref_text": ref_text, "lang_code": str(c.get("lang_code") or "auto")}


def payloads(text: str, purpose: Optional[str], kokoro: dict, cfg: Optional[dict] = None) -> list:
    """The requests to try, best first, as (json body, is_clone): the clone when it applies, then the caller's Kokoro request."""
    plan = []
    s = clone_settings(cfg if cfg is not None else load_config(), purpose)
    if s:
        body = {"model": s["model"], "input": text, "ref_audio": s["ref_audio"], "ref_text": s["ref_text"], "lang_code": s["lang_code"]}
        if kokoro.get("response_format"):
            body["response_format"] = kokoro["response_format"]
        plan.append((body, True))
    plan.append((dict(kokoro, input=text), False))
    return plan


def _note_fallback(purpose: Optional[str], reason: str) -> None:
    """One audit line (the reason, never the text) when the clone failed and Kokoro was used instead."""
    key = (purpose, reason)
    now = time.monotonic()
    with _note_lock:
        if now - _last_note.get(key, -_FALLBACK_EVERY) < _FALLBACK_EVERY:
            return
        _last_note[key] = now
    log.warning("[tts] voice clone failed (%s); using the Kokoro request", reason)
    try:
        from codec_audit import log_event
        log_event("tts_fallback", "codec-tts", "Voice clone failed, Kokoro used", extra={"purpose": purpose or "", "reason": reason},
                  level="warning", outcome="warning")
    except Exception:
        pass


def _is_audio(r) -> bool:
    if r.status_code != 200 or not r.content:
        return False
    headers = getattr(r, "headers", None) or {}
    kind = str(headers.get("content-type") or "").split(";")[0].strip().lower()
    return "json" not in kind and not kind.startswith("text/")


def speak(url: str, text: str, purpose: Optional[str], kokoro: dict, *, cfg: Optional[dict] = None,
          timeout: float = 20, post=None) -> Spoken:
    """Audio for `text`. `kokoro` holds the caller's own Kokoro fields (model, voice, speed, response_format).
    purpose None, or one the clone is not enabled for, asks Kokoro only."""
    out = Spoken()
    for body, cloned in payloads(text, purpose, kokoro, cfg):
        fn = post or requests.post
        try:
            r = fn(url, json=body, timeout=CLONE_TIMEOUT if cloned else timeout)
        except requests.RequestException as e:
            out.unreachable = True
            if cloned:
                _note_fallback(purpose, type(e).__name__)
            continue
        except Exception as e:
            out.unreachable = False
            if cloned:
                _note_fallback(purpose, type(e).__name__)
            continue
        out.unreachable = False
        out.status = int(getattr(r, "status_code", 0) or 0)
        if _is_audio(r):
            headers = getattr(r, "headers", None) or {}
            out.audio = r.content
            out.content_type = str(headers.get("content-type") or "").split(";")[0].strip().lower()
            out.cloned = cloned
            return out
        if cloned:
            _note_fallback(purpose, "not_audio" if out.status == 200 else f"http_{out.status}")
    return out


def warm_up(url: Optional[str] = None, cfg: Optional[dict] = None) -> bool:
    """Make the server load the clone model now (one short word), so the first Speak is not the slow one.
    Meant for a background thread. True when the clone answered."""
    cfg = cfg if cfg is not None else load_config()
    url = url or cfg.get("tts_url") or "http://localhost:8085/v1/audio/speech"
    kokoro = {"model": cfg.get("tts_model", "mlx-community/Kokoro-82M-bf16"), "voice": cfg.get("tts_voice", "am_adam"),
              "speed": float(cfg.get("tts_speed", 1.0))}
    if not clone_settings(cfg, "speak"):
        return False
    try:
        return speak(url, WARM_TEXT, "speak", kokoro, cfg=cfg).cloned
    except Exception:
        return False
