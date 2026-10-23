"""CODEC speech requests: one place that decides which voice a request asks for (docs/CODEC-VOICE-02-DESIGN.md).

With `tts_clone` in ~/.codec/config.json, the callers that read text aloud (purpose "speak") ask Qwen3-TTS Base to clone a
reference clip, so the voice is the one the owner picked. Anything wrong (no clip, a slow or failing server, a reply that is
not audio) falls back once to the Kokoro request the caller already built, so speech never stops.
Without `tts_clone` every request is exactly what it was.

    "tts_clone": {"enabled_for": ["speak"], "model": "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16",
                  "ref_audio": "~/.codec/voices/codec-02/reference.wav",
                  "ref_text_file": "~/.codec/voices/codec-02/reference.txt"}

The live conversation (purpose "live") is on only when "live" is in `enabled_for`; it streams the clone as raw PCM instead
(first sound after about half a second, docs/CODEC-VOICE-02-LIVE-DESIGN.md).

The clone only applies when it cannot hurt the shared TTS server (found with a real server, 23 Oct 2026): the model must already
be in the local Hugging Face cache (an unknown model makes the server try the network and hold every request for a minute or
more), the reference clip must be a real WAV file, and the text must be short (a clone is spoken at about the speed of speech,
so a long text would hold the server, Kokoro requests included, for as long as it lasts). Long texts stay on Kokoro.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Optional

import requests

log = logging.getLogger("codec.tts")

CLONE_TIMEOUT = 45          # seconds to wait for the clone before the Kokoro request is tried
CLONE_MAX_CHARS = 400       # a longer text goes to Kokoro: about 25 s of speech, so the clone cannot hold the server for long
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


def _hf_hub() -> str:
    return os.environ.get("HF_HUB_CACHE") or os.path.join(os.environ.get("HF_HOME") or os.path.expanduser("~/.cache/huggingface"), "hub")


def model_cached(model: str) -> bool:
    """The model is already in the local Hugging Face cache, so the server never has to fetch it."""
    return os.path.isdir(os.path.join(_hf_hub(), "models--" + model.replace("/", "--"), "snapshots"))


def _is_wav(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(12)
    except OSError:
        return False
    return len(head) == 12 and head[:4] == b"RIFF" and head[8:12] == b"WAVE"


def clone_settings(cfg: Optional[dict], purpose: Optional[str]) -> Optional[dict]:
    """The clone's request fields when it is configured, enabled for this purpose and safe to ask for, else None."""
    c = (cfg or {}).get("tts_clone")
    if not isinstance(c, dict) or not purpose:
        return None
    enabled = c.get("enabled_for")
    if not isinstance(enabled, list) or purpose not in enabled:
        return None
    model = str(c.get("model") or "").strip()
    ref = os.path.expanduser(str(c.get("ref_audio") or ""))
    if not model or not model_cached(model):
        return None
    if not ref or not os.path.isfile(ref) or os.path.getsize(ref) == 0 or not _is_wav(ref):
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
    try:
        max_chars = max(1, int(c.get("max_chars") or CLONE_MAX_CHARS))
    except (TypeError, ValueError):
        max_chars = CLONE_MAX_CHARS
    return {"model": model, "ref_audio": ref, "ref_text": ref_text, "lang_code": str(c.get("lang_code") or "auto"),
            "max_chars": max_chars}


def payloads(text: str, purpose: Optional[str], kokoro: dict, cfg: Optional[dict] = None) -> list:
    """The requests to try, best first, as (json body, is_clone): the clone when it applies, then the caller's Kokoro request."""
    plan = []
    s = clone_settings(cfg if cfg is not None else load_config(), purpose)
    if s and len(text) <= s["max_chars"]:
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


def note_fallback(purpose: Optional[str], reason: str) -> None:
    """Public name of the rate-limited fallback audit line, for the live voice worker."""
    _note_fallback(purpose, reason)


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


def warm_up(url: Optional[str] = None, cfg: Optional[dict] = None, purpose: Optional[str] = None) -> bool:
    """Make the server load the clone model now (one short word), so the first Speak or the first live reply is not the slow one.
    `purpose` None tries "speak", then "live", whichever is enabled. Meant for a background thread. True when the clone answered."""
    cfg = cfg if cfg is not None else load_config()
    url = url or cfg.get("tts_url") or "http://localhost:8085/v1/audio/speech"
    kokoro = {"model": cfg.get("tts_model", "mlx-community/Kokoro-82M-bf16"), "voice": cfg.get("tts_voice", "am_adam"),
              "speed": float(cfg.get("tts_speed", 1.0))}
    for which in ([purpose] if purpose else ["speak", "live"]):
        if clone_settings(cfg, which):
            try:
                return speak(url, WARM_TEXT, which, kokoro, cfg=cfg).cloned
            except Exception:
                return False
    return False


# ── Live conversation: the clone streamed as raw PCM (docs/CODEC-VOICE-02-LIVE-DESIGN.md) ──────────────────────────

CLONE_RATE = 24000      # Qwen3-TTS Base: mono, signed 16-bit little-endian, 24 kHz (the non-streamed WAV header says the same)
LIVE_DEFAULTS = {"interval": 0.5, "first_byte_timeout": 8.0, "idle_timeout": 6.0, "chunk_chars": 280, "breaker_failures": 2}
_SENTENCE_END = re.compile(r"(?<=[.!?\u2026])\s+")
_SOFT_BREAK = re.compile(r"[,;:\u2013\u2014]\s")


class CloneUnavailable(Exception):
    """The clone could not start speaking this text (no audio was made): the caller falls back to the Kokoro request."""


class CloneBroke(Exception):
    """The clone stopped, or went silent, after audio had started: the rest of this text is dropped."""


def _bounded(value, default, lo, hi):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v != v:          # NaN
        return default
    return min(max(v, lo), hi)


def live_settings(cfg: Optional[dict]) -> Optional[dict]:
    """The clone's settings for the live conversation when it is on for purpose "live" and safe to ask for, else None.
    Adds the stream options (all optional under tts_clone.live, kept inside safe bounds)."""
    s = clone_settings(cfg, "live")
    if not s:
        return None
    c = ((cfg or {}).get("tts_clone") or {}).get("live")
    c = c if isinstance(c, dict) else {}
    d = LIVE_DEFAULTS
    s["interval"] = _bounded(c.get("interval"), d["interval"], 0.25, 2.0)
    s["first_byte_timeout"] = _bounded(c.get("first_byte_timeout"), d["first_byte_timeout"], 1.0, 30.0)
    s["idle_timeout"] = _bounded(c.get("idle_timeout"), d["idle_timeout"], 1.0, 30.0)
    s["chunk_chars"] = int(_bounded(c.get("chunk_chars"), d["chunk_chars"], 40, s["max_chars"]))
    s["breaker_failures"] = int(_bounded(c.get("breaker_failures"), d["breaker_failures"], 1, 10))
    s["rate"] = CLONE_RATE
    return s


def models_url(speech_url: str) -> str:
    """The server's list of loaded models, from the speech URL."""
    return speech_url.rsplit("/audio/speech", 1)[0] + "/models"


def model_loaded(speech_url: str, model: str, *, get=None) -> bool:
    """True when the TTS server already holds `model` in memory. Any trouble answers False."""
    try:
        r = (get or requests.get)(models_url(speech_url), timeout=2)
        return any(m.get("id") == model for m in (r.json().get("data") or []))
    except Exception:
        return False


def split_for_speech(text: str, max_chars: int = CLONE_MAX_CHARS) -> list:
    """Pieces of `text` for the clone, each at most `max_chars` characters: sentences stay whole, a longer sentence is cut at a
    comma (or similar), else at a space, else in the middle of a word. No text is lost; spacing is normalised."""
    flat = " ".join(str(text or "").split())
    max_chars = max(1, int(max_chars))
    out = []
    for sentence in _SENTENCE_END.split(flat):
        rest = sentence.strip()
        while len(rest) > max_chars:
            window = rest[:max_chars]
            cut = 0
            for m in _SOFT_BREAK.finditer(window):
                if m.end() >= max_chars * 0.4:
                    cut = m.end()
            if not cut:
                cut = window.rfind(" ") + 1
            if cut <= 0:
                cut = max_chars
            out.append(rest[:cut].strip())
            rest = rest[cut:].strip()
        if rest:
            out.append(rest)
    return [x for x in out if x]


async def astream_clone(http, url: str, text: str, live: dict):
    """Async generator over the PCM of `text` in voice 02: signed 16-bit little-endian mono at live["rate"] Hz, whole samples only.

    `http` is an httpx.AsyncClient. CloneUnavailable is raised when nothing was heard yet (a bad status, an answer that is not
    audio, no first byte in `first_byte_timeout`, an empty body); CloneBroke when it fails after audio started (a stop, or no
    data for `idle_timeout`). Leaving the loop early (or aclose()) closes the connection, and the server then stops generating."""
    import httpx
    body = {"model": live["model"], "input": text, "ref_audio": live["ref_audio"], "ref_text": live["ref_text"],
            "lang_code": live["lang_code"], "response_format": "pcm", "stream": True, "streaming_interval": live["interval"]}
    started = False
    carry = b""
    backstop = max(live["first_byte_timeout"], live["idle_timeout"]) + 2.0

    def fail(reason: str):
        return CloneBroke(reason) if started else CloneUnavailable(reason)

    try:
        async with http.stream("POST", url, json=body, timeout=httpx.Timeout(5.0, read=backstop)) as r:
            if r.status_code != 200:
                raise CloneUnavailable(f"http_{r.status_code}")
            kind = str(r.headers.get("content-type") or "").split(";")[0].strip().lower()
            if not kind.startswith("audio/"):
                raise CloneUnavailable("not_audio")
            parts = r.aiter_raw().__aiter__()
            while True:
                wait = live["idle_timeout"] if started else live["first_byte_timeout"]
                try:
                    part = await asyncio.wait_for(parts.__anext__(), wait)
                except StopAsyncIteration:
                    break
                if not part:
                    continue
                data = carry + part
                keep = len(data) - (len(data) % 2)
                carry = data[keep:]
                if keep:
                    started = True
                    yield data[:keep]
            if not started:
                raise CloneUnavailable("empty")
    except (CloneUnavailable, CloneBroke):
        raise
    except asyncio.TimeoutError:
        raise fail("timeout") from None
    except (httpx.HTTPError, OSError) as e:
        raise fail(type(e).__name__) from None
