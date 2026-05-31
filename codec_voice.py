"""
CODEC Voice v2 — voice-to-voice pipeline with interruption support.
WebSocket receives PCM16 audio + JSON control messages from browser.
Two concurrent tasks: audio receiver + pipeline processor.
Interruption: user speaking mid-response cancels TTS immediately.
"""
import asyncio
import contextvars
import io
import json
import os
import re
import secrets
import sys
import time
import wave
from datetime import datetime, timezone
import logging
from typing import Optional

import base64
import subprocess

import httpx
# B6-P4: numpy use moved with rms_int16 to codec_voice_filters. Kept as
# noqa here in case downstream code references numpy via `codec_voice.np`.
import numpy as np  # noqa: F401 — re-exported indirectly via filters helpers

log = logging.getLogger("codec_voice")

from codec_audit import log_event as _voice_log_event
from codec_hooks import (
    HookVeto,
    emit_operation_end as _voice_emit_op_end,
    emit_operation_start as _voice_emit_op_start,
    run_with_hooks as _voice_run_with_hooks,
)
from codec_llm_proxy import llm_queue, Priority

# Per-session correlation_id contextvar — set at VoicePipeline.run entry,
# inherited by every audit emit during the session lifetime (incl. nested
# tool calls fired from inside the pipeline). See design §1.4.
#
# A5 / SR-5: canonical home moved to codec_audit. Re-exported here for
# back-compat with any external importer (codec_ask_user previously pulled
# from here; now reads from codec_audit directly).
from codec_audit import _voice_correlation_id_var as _voice_correlation_id_var  # noqa: F401 — re-export

# ── Phase 1 Step 3 §5.3.1 — fuzzy-option-match for AskUserQuestion ────────
# When the question carries `options`, the voice ASR layer maps the spoken
# transcript to the closest option label via:
#   1. Exact substring match (transcript contains lowercased option label)
#   2. Curated synonym dict below
#   3. Levenshtein fallback (≤3 edits AND ≤30% of label length)
# Strict-consent (§1.7) BYPASSES this layer — irreversible actions force
# literal verb-match. The asymmetry is deliberate: fuzzy intent inference
# is wrong for irreversible actions.
_VOICE_OPTION_SYNONYMS = {
    "approve":  ["yes", "yeah", "ok", "okay", "go ahead", "do it",
                 "approve it", "go for it", "sounds good"],
    "reject":   ["no", "nope", "skip", "skip it", "cancel", "don't",
                 "abort", "forget it", "nevermind"],
    "modify":   ["change", "edit", "different", "tweak", "adjust"],
    "delete":   ["delete", "remove", "destroy", "wipe", "trash"],
    "send":     ["send", "send it", "transmit", "deliver"],
    "transfer": ["transfer", "move", "wire", "send money"],
    "abandon":  ["abandon", "give up", "stop", "quit", "drop it"],
    "continue": ["continue", "keep going", "carry on", "press on"],
}


def _levenshtein(a: str, b: str) -> int:
    """Compute Levenshtein distance between two strings. Caps at 100 chars
    of each input — any sane option label / transcript stays well under."""
    a = (a or "")[:100]
    b = (b or "")[:100]
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            curr[j] = min(
                prev[j] + 1,
                curr[j - 1] + 1,
                prev[j - 1] + (0 if ca == cb else 1),
            )
        prev = curr
    return prev[-1]


def _resolve_voice_option_choice(
    transcript: str,
    options: list,
    *,
    strict: bool = False,
    destructive_verb: Optional[str] = None,
) -> str:
    """Map an ASR transcript to one of the structured option labels.

    Returns the matched option label as-is, OR the raw transcript if no
    match (treated as free-text by the answer-acceptance path).

    When ``strict=True`` (§1.7 destructive consent), this resolver is
    BYPASSED — the caller must check the destructive_verb literal-match
    rule itself. Returns the raw transcript so the strict-consent gate
    in codec_ask_user.submit_answer evaluates it.
    """
    if not isinstance(options, list) or not options:
        return transcript or ""
    raw = (transcript or "").strip()
    if not raw:
        return raw
    if strict:
        # §1.7 — fuzzy-match disabled for irreversible actions.
        return raw
    low = raw.lower()
    # Strip simple punctuation for matching.
    low = re.sub(r"[^a-z0-9\s]", " ", low).strip()
    # 1. Exact substring of any option label (case-insensitive). Prefer the
    #    LONGEST matching label so "yes and notify" wins over "yes" when both
    #    are options and both appear in the transcript (L2 / SR-62: was
    #    first-match, which mis-routed a non-strict multi-option answer).
    substring_matches = [opt for opt in options if str(opt).lower() in low]
    if substring_matches:
        return max(substring_matches, key=lambda o: len(str(o)))
    # 2. Synonym map: if any synonym word appears, match the option label
    #    whose lowercase contains the synonym key.
    low_words = set(low.split())
    for opt in options:
        opt_low = str(opt).lower()
        for syn_key, syn_phrases in _VOICE_OPTION_SYNONYMS.items():
            if syn_key in opt_low:
                # Check if any of this option's synonyms appear in the transcript.
                for phrase in syn_phrases:
                    if phrase in low or any(w == phrase for w in low_words):
                        return opt
    # 3. Levenshtein fallback (≤3 edits, ≤30% of label length).
    best_opt = None
    best_dist = 10**9
    for opt in options:
        opt_low = str(opt).lower()
        dist = _levenshtein(low[:len(opt_low) + 5], opt_low)
        if dist < best_dist:
            best_dist = dist
            best_opt = opt
    if best_opt is not None:
        opt_low = str(best_opt).lower()
        if best_dist <= 3 and best_dist <= max(1, int(0.3 * len(opt_low))):
            return best_opt
    # 4. No match — return raw transcript as free-text answer.
    return raw


# ── Phase 1 Step 3 §5.3 — voice-session marker file ───────────────────────
# ~/.codec/voice_session.json is touched by VoicePipeline.run start and
# removed in finally. codec_ask_user reads this to decide whether to
# announce + listen for the answer (active session) vs defer to PWA only.
_VOICE_SESSION_MARKER = os.path.expanduser("~/.codec/voice_session.json")


def _touch_voice_session_marker(session_id: str) -> None:
    """Write the active-session marker. Best-effort; failures log + continue."""
    try:
        with open(_VOICE_SESSION_MARKER, "w") as f:
            json.dump({
                "session_id": session_id,
                "started_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            }, f)
    except Exception as e:
        log = logging.getLogger("codec_voice")
        log.debug("voice_session marker write failed: %s", e)


def _clear_voice_session_marker() -> None:
    """Remove the active-session marker. Best-effort."""
    try:
        if os.path.exists(_VOICE_SESSION_MARKER):
            os.remove(_VOICE_SESSION_MARKER)
    except Exception as e:
        log = logging.getLogger("codec_voice")
        log.debug("voice_session marker clear failed: %s", e)

# ── CONFIG — loaded from ~/.codec/config.json ─────────────────────────────
WHISPER_URL   = "http://localhost:8084/v1/audio/transcriptions"
WHISPER_MODEL = "mlx-community/whisper-large-v3-turbo"
QWEN_BASE_URL = "http://localhost:8083/v1"   # A-12 (PR-3E-async): base for codec_llm.astream
QWEN_URL      = "http://localhost:8083/v1/chat/completions"
QWEN_MODEL    = "mlx-community/Qwen3.6-35B-A3B-4bit"
LLM_KWARGS    = {}
KOKORO_URL    = "http://localhost:8085/v1/audio/speech"
KOKORO_MODEL  = "mlx-community/Kokoro-82M-bf16"
KOKORO_VOICE  = "am_adam"
try:
    from codec_config import SKILLS_DIR
except ImportError:
    SKILLS_DIR = os.path.expanduser("~/.codec/skills")

_CONFIG_PATH = os.path.expanduser("~/.codec/config.json")
try:
    with open(_CONFIG_PATH) as _f:
        _cfg = json.load(_f)
    _llm_base     = _cfg.get("llm_base_url", "http://localhost:8083/v1").rstrip("/")
    QWEN_BASE_URL = _llm_base
    QWEN_URL      = _llm_base + "/chat/completions"
    QWEN_MODEL    = _cfg.get("llm_model", QWEN_MODEL)
    LLM_KWARGS    = {k: v for k, v in _cfg.get("llm_kwargs", {}).items() if k != "enable_thinking"}
    KOKORO_URL    = _cfg.get("tts_url",   KOKORO_URL)
    KOKORO_MODEL  = _cfg.get("tts_model", KOKORO_MODEL)
    KOKORO_VOICE  = _cfg.get("tts_voice", KOKORO_VOICE)
    WHISPER_URL   = _cfg.get("stt_url",   WHISPER_URL)
    WHISPER_MODEL = _cfg.get("stt_model", WHISPER_MODEL)
except Exception as _e:
    log.warning(f"Config load warning: {_e} — using defaults")
# ── Vision config ────────────────────────────────────────────────────────
VISION_URL   = "http://localhost:8083/v1/chat/completions"
VISION_MODEL = "mlx-community/Qwen2.5-VL-7B-Instruct-4bit"
GEMINI_API_KEY = ""
VISION_PROVIDER = "local"
try:
    VISION_URL   = _cfg.get("vision_base_url", "http://localhost:8083/v1").rstrip("/") + "/chat/completions"
    VISION_MODEL = _cfg.get("vision_model", VISION_MODEL)
    # PR-2B-2 (D-15): Keychain-aware getter (cfg→Keychain migration + env fallback).
    from codec_config import get_gemini_api_key
    GEMINI_API_KEY = get_gemini_api_key()
    VISION_PROVIDER = _cfg.get("vision_provider", "gemini" if GEMINI_API_KEY else "local")
except Exception:
    log.debug("voice: vision provider/Keychain bootstrap failed", exc_info=True)

# Screen-related trigger phrases
_SCREEN_TRIGGERS = re.compile(
    r"(look at my screen|read my screen|what('?s| is) on my screen|"
    r"what do you see|analyze my screen|check my screen|see my screen|"
    r"what('?s| is) on the screen|describe my screen|screen shot|screenshot|"
    r"look at this|what am i looking at|what('?s| is) this on my screen)",
    re.IGNORECASE,
)

# ── VAD (configurable via config.json → "vad" section) ───────────────────
try:
    _vad_cfg = _cfg.get("vad", {})
except NameError:
    _vad_cfg = {}
VAD_SILENCE_THRESHOLD  = _vad_cfg.get("silence_threshold", 800)     # RMS below this = silence
VAD_SILENCE_DURATION   = _vad_cfg.get("silence_duration",  1.5)     # seconds of silence before flushing
VAD_MIN_SPEECH_SECONDS = _vad_cfg.get("min_speech_seconds", 0.4)    # minimum speech before considering flush
VAD_ECHO_COOLDOWN      = _vad_cfg.get("echo_cooldown",     2.5)     # ignore mic this long after TTS playback FINISHES (was 1.2 — too short for room reverb)
# Bytes-per-second estimate for Kokoro MP3 output (24 kHz mono ~64 kbps).
# Used to schedule `last_tts_end` to AFTER browser actually finishes playing,
# not the moment we send the bytes. Without this, the echo cooldown timer
# starts while the browser is still playing CODEC's voice, the mic picks
# it up, and CODEC interrupts itself.
TTS_BYTES_PER_SEC      = _vad_cfg.get("tts_bytes_per_sec", 8000)
TTS_BROWSER_DECODE_LAG = _vad_cfg.get("tts_browser_decode_lag", 0.3)  # seconds — extra padding for browser audio buffering
SAMPLE_RATE            = 16000
BYTES_PER_SAMPLE       = 2
MIN_SPEECH_BYTES       = int(SAMPLE_RATE * BYTES_PER_SAMPLE * VAD_MIN_SPEECH_SECONDS)
# L2 / SR-62: hard upper bound on a single utterance. Continuous mic noise above
# the VAD threshold keeps last_speech_time fresh, so the silence gate would never
# fire and audio_buffer would grow unbounded (~32 KB/s). Force-flush at this cap.
VAD_MAX_UTTERANCE_SECONDS = _vad_cfg.get("max_utterance_seconds", 30)
MAX_UTTERANCE_BYTES    = int(SAMPLE_RATE * BYTES_PER_SAMPLE * VAD_MAX_UTTERANCE_SECONDS)

# RMS threshold for interrupt detection (slightly lower than VAD to catch early speech)
INTERRUPT_THRESHOLD = 1500  # raised from 600 — too sensitive to background noise

# ── Whisper noise filter ──────────────────────────────────────────────────
# B6-P4 / SR-35: NOISE_WORDS + WHISPER_HALLUCINATIONS moved to
# codec_voice_filters. Re-exported here for back-compat with any
# external import that grabbed them from codec_voice.
from codec_voice_filters import NOISE_WORDS, WHISPER_HALLUCINATIONS  # noqa: F401,E402

# Max conversation turns to keep in context (prevents bloat → keeps LLM fast)
MAX_CONTEXT_TURNS = 20

# ── System Prompt ─────────────────────────────────────────────────────────
def _build_system_prompt() -> str:
    import datetime as _dt
    from codec_config import ASSISTANT_NAME, USER_NAME
    now = _dt.datetime.now()
    days = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    date_str = now.strftime(f"{days[now.weekday()]}, %-d %B %Y")
    time_str = now.strftime("%-I:%M %p")
    _aname = ASSISTANT_NAME or "CODEC"
    _uname = USER_NAME
    _user_ref = _uname if _uname else "the user"

    # Memory upgrade: boot identity + active temporal facts
    _boot = ""
    try:
        from codec_memory_upgrade import load_identity, query_valid_facts
        ident = load_identity()
        if ident:
            _boot += f"\n\n[IDENTITY]\n{ident}\n[/IDENTITY]"
        facts = query_valid_facts(limit=20)
        if facts:
            _boot += "\n\n[ACTIVE FACTS]\n" + "\n".join(
                f"  {f['key']} = {f['value']}" for f in facts
            ) + "\n[/FACTS]"
    except Exception:
        log.debug("voice: facts injection into system prompt skipped", exc_info=True)

    return f"""You are {_aname} — CODEC Voice, a JARVIS-class local AI running on a Mac Studio M1 Ultra.{_boot}
{f'The user is {_uname}. ' if _uname else ''}Fully local. No cloud. No external logs.

CURRENT DATE AND TIME: {date_str}, {time_str} (Madrid / Europe time)
Use this to correctly interpret "today", "tomorrow", "this afternoon", etc.

━━ VOICE OUTPUT RULES ━━
Your responses go directly to speech via Kokoro TTS. Format for ears only:
- NO markdown: no asterisks, no hashtags, no bullets, no tables, no dashes
- NO special characters, symbols, or URLs
- Be conversational and warm — like a trusted colleague who happens to know everything
- 2-4 sentences is the sweet spot. Give context, add a useful detail, make it feel human
- Start with natural openers: "Right,", "Sure thing.", "Got it.", "So,", "Alright,"
- For factual questions: give the answer PLUS one interesting detail or context
- Never give one-word answers — always add warmth or context

━━ INPUT HANDLING ━━
Input is live voice transcription (Whisper STT). Expect noise:
- "hey codec", "hey codex", "okay codec" at start = wake words — ignore them
- "uh", "um", "er" = filler — ignore
- Strange words = infer from context
- Never mention transcription errors unless they cause real confusion
- Math ("one plus one", "7 times 8") → answer with just the number
- "Speed test [X]" → just answer X, do NOT run diagnostics

━━ SKILLS ━━
You have {len([f for f in os.listdir(SKILLS_DIR) if f.endswith('.py')])} built-in skills (calendar, email, drive, chrome, weather, etc.).
Skills execute mid-call and return a result string.
Report results conversationally — 1-2 sentences max.
NEVER say you completed an action unless the skill result explicitly confirms it.
NEVER delegate to any other agent.

━━ ANTI-HALLUCINATION ━━
- Skill returns "Done. [X] added" → confirm done
- Skill returns "No events" → that's a READ result, NOT creation confirmation
- Skill returns error → report honestly, offer to retry
- Unsure → say "Let me check" and report actual result

━━ MEMORY ━━
All sessions are saved to CODEC shared memory (FTS5 indexed).
If {_user_ref} asks to remember something: confirm "Saved to memory."

━━ PERSONA ━━
Warm, sharp, and confident. Think J.A.R.V.I.S. — loyal, witty, always one step ahead.
Be the kind of assistant people actually enjoy talking to. Dry humor welcome.
Show personality. You are not a search engine — you are a companion with opinions.
Your user's right hand — not a customer service bot."""


# ─────────────────────────────────────────────────────────────────────────────
class VoicePipeline:
    """One voice session per WebSocket connection. Two-task architecture."""

    # Class-level cache for resumable sessions (session_id → messages list)
    _resumable_sessions: dict[str, list] = {}
    _resume_timestamps: dict[str, float] = {}  # session_id → time.monotonic() at save (M-6)
    _RESUME_TTL = 600  # seconds — discard saved sessions older than this (10 min)

    def __init__(self, websocket, resume_session_id: str | None = None):
        self.ws              = websocket
        self.session_id      = resume_session_id or "voice_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        self._disconnect_reason = "unexpected"  # "user" or "unexpected"

        # M-6: prune stale resumable sessions on every new connection — so a
        # dropped-and-never-reconnected session is evicted the moment ANY new
        # voice activity happens, not only on the next _save_for_resume.
        self._prune_resumable()

        # Resume conversation context if available
        self._is_resumed = False
        if resume_session_id and resume_session_id in self._resumable_sessions:
            saved = self._resumable_sessions.pop(resume_session_id)
            VoicePipeline._resume_timestamps.pop(resume_session_id, None)  # keep the two dicts in sync
            self.messages = saved
            self._is_resumed = True
            log.info(f"Resumed session {resume_session_id} with {len(saved)} messages")
        else:
            self.messages = [{"role": "system", "content": _build_system_prompt()}]

        # VAD state
        self.audio_buffer     = bytearray()
        self.last_speech_time = 0.0
        self.is_speaking      = False
        self.last_tts_end     = 0.0

        # Concurrency
        self.utterance_queue = asyncio.Queue(maxsize=3)   # completed utterances ready to process
        self.interrupted     = asyncio.Event()   # set when user speaks mid-response
        self.processing      = False             # True while generating/speaking a response
        # L2 / SR-62: flipped False the moment the client disconnects, so sends
        # in _speak / the crew callback no-op instead of raising against a dead
        # socket (which previously aborted a detached crew dispatch mid-run).
        self._ws_alive = True

        self.skills = {}
        self._http  = httpx.AsyncClient(timeout=120.0)
        self._warmed_up = False
        self._load_skills()

    @classmethod
    def _prune_resumable(cls, now=None):
        """M-6: evict resumable sessions (and their timestamps) older than
        _RESUME_TTL. Called on every save AND on __init__, so stale entries are
        swept on any voice activity — no background timer needed (the leak the
        audit flags is minor + usage-bounded; a timer's lifecycle isn't worth
        it). Never raises."""
        now = now if now is not None else time.monotonic()
        try:
            stale = [sid for sid, ts in cls._resume_timestamps.items()
                     if now - ts > cls._RESUME_TTL]
            for sid in stale:
                cls._resumable_sessions.pop(sid, None)
                cls._resume_timestamps.pop(sid, None)
        except Exception:
            log.debug("voice: stale-resume-session cleanup pass swallowed", exc_info=True)

    def _save_for_resume(self):
        """Stash conversation state so a reconnecting client can resume."""
        self._resumable_sessions[self.session_id] = list(self.messages)
        VoicePipeline._resume_timestamps[self.session_id] = time.monotonic()
        self._prune_resumable()
        log.info(f"Session {self.session_id} saved for resume ({len(self.messages)} messages)")
    # ── Skill loader (lazy via SkillRegistry) ─────────────────────────────

    def _load_skills(self):
        from codec_skill_registry import SkillRegistry
        self._skill_registry = SkillRegistry(SKILLS_DIR)
        self._skill_registry.scan()
        # Build a lightweight dict with triggers only (no module imports)
        for name in self._skill_registry.names():
            triggers = self._skill_registry.get_triggers(name)
            if triggers:
                self.skills[name] = {
                    "triggers": [t.lower() for t in triggers],
                    "desc":     self._skill_registry.get_description(name),
                }
        log.info(f"{len(self.skills)} skills registered (lazy)")
    # ── LLM Warmup ────────────────────────────────────────────────────────

    async def warmup_llm(self):
        """Pre-load system prompt + recent memory when VAD detects speech start."""
        if self._warmed_up:
            return
        self._warmed_up = True
        try:
            _dash = os.path.dirname(os.path.abspath(__file__))
            if _dash not in sys.path:
                sys.path.insert(0, _dash)
            from codec_memory import CodecMemory
            mem = CodecMemory()
            context = mem.get_context("recent", 5)
            if context:
                base = _build_system_prompt()
                self.messages[0] = {
                    "role": "system",
                    "content": base + "\n\nRecent memory:\n" + context
                }
                log.debug("Warmup: memory context injected into system prompt")
        except Exception as e:
            log.error(f"Warmup error: {e}")
    _VOICE_SKIP_SKILLS = {"calculator", "app_switch", "brightness", "clipboard"}

    def _match_skill(self, text: str) -> Optional[dict]:
        text_lower = text.lower().strip()
        best_match, best_len = None, 0
        for name, skill in self.skills.items():
            if name in self._VOICE_SKIP_SKILLS:
                continue
            for trigger in skill["triggers"]:
                if len(trigger.split()) < 2:
                    continue
                if trigger in text_lower and len(trigger) > best_len:
                    best_len   = len(trigger)
                    # Lazy-load: get run function from registry on match
                    mod = self._skill_registry.load(name)
                    if mod and hasattr(mod, "run"):
                        best_match = {"name": name, "run": mod.run}
        return best_match

    # ── VAD ───────────────────────────────────────────────────────────────

    # B6-P4 / SR-35: _rms delegates to codec_voice_filters.rms_int16 so
    # the function is testable without instantiating VoicePipeline.
    @staticmethod
    def _rms(chunk: bytes) -> float:
        from codec_voice_filters import rms_int16
        return rms_int16(chunk)

    def feed_audio(self, chunk: bytes) -> Optional[bytes]:
        rms = self._rms(chunk)
        now = time.monotonic()

        # Echo cooldown: ignore mic after Q speaks — but ONLY suppress a NEW
        # speech start. If the user is already mid-utterance (is_speaking), keep
        # capturing so a barge-in's leading words aren't silently dropped
        # (L2 / SR-62: was an unconditional return → truncated transcripts like
        # "...end my email" instead of "send my email").
        if (now - self.last_tts_end < VAD_ECHO_COOLDOWN) and not self.is_speaking:
            return None

        if rms > VAD_SILENCE_THRESHOLD:
            self.is_speaking = True
            self.last_speech_time = now
            self.audio_buffer.extend(chunk)
            # L2 / SR-62: force-flush a runaway utterance so continuous noise
            # can't grow the buffer without bound.
            if len(self.audio_buffer) >= MAX_UTTERANCE_BYTES:
                utterance = bytes(self.audio_buffer)
                self.audio_buffer = bytearray()
                self.is_speaking = False
                return utterance
            return None

        if self.is_speaking:
            self.audio_buffer.extend(chunk)
            if (now - self.last_speech_time > VAD_SILENCE_DURATION
                    or len(self.audio_buffer) >= MAX_UTTERANCE_BYTES):
                self.is_speaking = False
                if len(self.audio_buffer) >= MIN_SPEECH_BYTES:
                    utterance = bytes(self.audio_buffer)
                    self.audio_buffer = bytearray()
                    return utterance
                self.audio_buffer = bytearray()
        return None

    # ── STT ───────────────────────────────────────────────────────────────

    async def transcribe(self, pcm: bytes) -> str:
        wav_buf = io.BytesIO()
        with wave.open(wav_buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(pcm)
        wav_buf.seek(0)
        try:
            r = await self._http.post(
                WHISPER_URL,
                files={"file": ("audio.wav", wav_buf, "audio/wav")},
                data={"model": WHISPER_MODEL, "language": "en"},
            )
            if r.status_code == 200:
                text  = r.json().get("text", "").strip()
                clean = text.lower().rstrip(".!?, ")
                if clean in NOISE_WORDS:
                    log.debug(f"Discarded noise: '{text}'")
                    return ""
                # Whisper hallucination filter (YouTube outros, annotations, etc.)
                text_lower = text.strip().lower()
                if text_lower in WHISPER_HALLUCINATIONS:
                    log.debug(f"Discarded hallucination: '{text}'")
                    return ""
                # Detect repetitive hallucinations like "thank you. thank you. thank you."
                if re.search(r'(.{4,}?)\1{2,}', text_lower):
                    log.debug(f"Discarded repetitive: '{text}'")
                    return ""
                words = [w for w in clean.split() if w not in {"uh","um","er","hmm","ah"}]
                if len(words) < 2:
                    log.debug(f"Discarded too short: '{text}'")
                    return ""
                try:
                    _dash = os.path.dirname(os.path.abspath(__file__))
                    if _dash not in sys.path:
                        sys.path.insert(0, _dash)
                    from codec_config import clean_transcript as _clean
                    text = _clean(text) or text
                except Exception as e:
                    log.warning(f"Transcript clean warning: {e}")
                return text
            log.error(f"Whisper {r.status_code}: {r.text[:200]}")
        except Exception as e:
            log.error(f"Whisper error: {e}")
        return ""

    # ── LLM ───────────────────────────────────────────────────────────────

    def _trimmed_messages(self) -> list:
        """Keep system prompt + last MAX_CONTEXT_TURNS message pairs."""
        system = [m for m in self.messages if m["role"] == "system"]
        convo  = [m for m in self.messages if m["role"] != "system"]
        # Each turn = 2 messages (user + assistant)
        max_msgs = MAX_CONTEXT_TURNS * 2
        return system + convo[-max_msgs:]

    async def _stream_qwen(self, messages: list, max_tokens: int = 2000):
        # A-12 (PR-3E-async): codec_llm.astream owns the SSE POST + parsing and
        # PROPAGATES errors, so the except below still speaks the failure. The
        # queue (CRITICAL) stays here; the per-token <think> strip stays here too
        # (Qwen 3.6 may put thinking in content — strip it before yielding).
        import codec_llm
        # L2 / SR-62: reset per-stream error flag. The consumer checks it to
        # avoid persisting the spoken error sentinel as a real assistant turn.
        self._stream_error = False
        await llm_queue.acquire(Priority.CRITICAL)
        try:
            async for token in codec_llm.astream(
                messages, base_url=QWEN_BASE_URL, model=QWEN_MODEL,
                max_tokens=max_tokens, temperature=0.7, enable_thinking=False,
                extra_kwargs={"top_p": 0.9, "frequency_penalty": 0.8, **LLM_KWARGS},
                http=self._http,
            ):
                token = re.sub(r"<think>[\s\S]*?</think>", "", token)
                if token:
                    yield token
        except Exception as e:
            log.error(f"Qwen error: {e}")
            self._stream_error = True
            yield "Sorry, I had a processing error."
        finally:
            await llm_queue.release(Priority.CRITICAL)

    # ── Screenshot + Vision ─────────────────────────────────────────────

    def _is_screen_request(self, text: str) -> bool:
        """Detect if user is asking to look at their screen."""
        return bool(_SCREEN_TRIGGERS.search(text))

    async def _take_screenshot(self) -> Optional[str]:
        """Take a screenshot, downscale to 1280px wide, return base64 JPEG."""
        path = os.path.expanduser("~/.codec/voice_screenshot.png")
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    ["screencapture", "-x", path],
                    timeout=5, check=True,
                ),
            )
            if not os.path.exists(path):
                return None
            # Downscale to 1280px wide JPEG to reduce vision model latency
            def _downscale():
                from PIL import Image
                img = Image.open(path)
                if img.mode == "RGBA":
                    img = img.convert("RGB")
                w, h = img.size
                if w > 1280:
                    ratio = 1280 / w
                    img = img.resize((1280, int(h * ratio)), Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=75)
                return base64.b64encode(buf.getvalue()).decode()
            return await loop.run_in_executor(None, _downscale)
        except Exception as e:
            log.error(f"Screenshot failed: {e}")
        return None

    async def _analyze_screenshot(self, image_b64: str, user_text: str) -> str:
        """Send screenshot to vision model (Gemini Flash or local Qwen VL)."""
        prompt = (
            f"The user said: \"{user_text}\"\n\n"
            "Describe what you see on this screen in 2-4 concise sentences. "
            "Focus on the main content, app, or task visible. "
            "Be specific about text, UI elements, and what the user appears to be working on."
        )
        # A-11 (PR-3E): canonical vision helper (Gemini Flash → local Qwen-VL).
        # Reuses this pipeline's httpx client. Was an inline duplicate of the
        # same fallback logic in codec.py + codec_session.
        import codec_vision
        return await codec_vision.describe_async(
            image_b64, prompt, mime="image/jpeg", max_tokens=500, http=self._http)

    async def generate_response(self, user_text: str):
        self.messages.append({"role": "user", "content": user_text})
        self._warmed_up = False  # reset so next speech start can warm up again
        # Inject targeted memory context for this specific question
        try:
            from codec_memory import CodecMemory
            cm = CodecMemory()
            targeted = cm.get_context(user_text, n=3)
            if targeted:
                self.messages[0] = {
                    "role": "system",
                    "content": _build_system_prompt() + f"\n\n[MEMORY — RELEVANT CONTEXT]\n{targeted}\n[END MEMORY]"
                }
        except Exception:
            log.debug("voice: targeted-memory injection skipped", exc_info=True)
        # Phase 2 Step 5 — Observer summary injection (gated per §X).
        # Voice always uses local Qwen by default (transport="local"); if
        # the user has cloud-routed voice configured (vision_provider=
        # "gemini"), pass transport="voice" so the cloud-transport gate
        # applies. Audit emit fires inside the helper.
        try:
            from codec_observer import maybe_inject_observation_summary
            _voice_transport = "voice" if VISION_PROVIDER == "gemini" else "local"
            _obs_summary, _obs_reason = maybe_inject_observation_summary(
                user_prompt=user_text or "",
                transport=_voice_transport,
                skill_name=None,
                skill_module=None,
            )
            if _obs_summary and self.messages and self.messages[0].get("role") == "system":
                # Append after memory block, before next user turn
                self.messages[0]["content"] += f"\n\n{_obs_summary}"
        except Exception as _e:
            log.debug(f"observer injection failed (non-fatal): {_e}")
        full = ""
        async for chunk in self._stream_qwen(self._trimmed_messages()):
            full += chunk
            yield chunk
        # L2 / SR-62: don't persist the spoken error sentinel as a real
        # assistant turn — it would pollute conversation context + memory on
        # the next turn (the LLM would "see" a fake apology it never reasoned).
        # The user still HEARD it (it was yielded above).
        if not getattr(self, "_stream_error", False):
            self.messages.append({"role": "assistant", "content": full})

    # ── TTS ───────────────────────────────────────────────────────────────

    async def synthesize(self, text: str) -> Optional[bytes]:
        text = text.strip()
        if not text:
            return None
        try:
            r = await self._http.post(
                KOKORO_URL,
                json={"model": KOKORO_MODEL, "input": text,
                      "voice": KOKORO_VOICE, "speed": 1.15},
            )
            if r.status_code == 200:
                return r.content
            log.error(f"Kokoro {r.status_code}: {r.text[:200]}")
        except Exception as e:
            log.error(f"TTS error: {e}")
        return None

    # ── Sentence boundary ─────────────────────────────────────────────────

    def _flush_on_boundary(self, buf: str) -> tuple[str, str]:
        """Return (to_speak, remainder) at first sentence boundary.
        Avoids splitting on abbreviations (Dr., Mr., etc.), decimals (3.14), times (10:30)."""
        import re
        # Match sentence-ending punctuation NOT preceded by common abbreviations
        # and NOT followed by a digit (decimals, times)
        # Simple sentence-end: punctuation followed by space (skip abbreviations with a simpler check)
        m = re.search(r'[.!?]\s+[A-Z]', buf)
        if not m:
            m = re.search(r'[.!?]\s', buf)
        if m:
            end = m.end() - 1  # keep the space in remainder
            return buf[:end].strip(), buf[end:]
        return "", buf

    # ── TTS with interruption check ───────────────────────────────────────

    async def _safe_send_bytes(self, data: bytes) -> bool:
        """Send bytes only if the socket is still alive; swallow + flag on a
        closed-socket error. Returns True if sent. (L2 / SR-62.)"""
        if not self._ws_alive:
            return False
        try:
            await self.ws.send_bytes(data)
            return True
        except Exception as e:
            self._ws_alive = False
            log.info(f"[Voice] send_bytes on dead socket — stopping: {e}")
            return False

    async def _safe_send_json(self, obj: dict) -> bool:
        """Send JSON only if the socket is still alive; swallow + flag on a
        closed-socket error. Returns True if sent. (L2 / SR-62.)"""
        if not self._ws_alive:
            return False
        try:
            await self.ws.send_json(obj)
            return True
        except Exception as e:
            self._ws_alive = False
            log.info(f"[Voice] send_json on dead socket — stopping: {e}")
            return False

    async def _speak(self, text: str) -> bool:
        """
        Synthesize and send one chunk of speech.
        Returns False if interrupted before/after sending — caller should stop.
        """
        if self.interrupted.is_set():
            return False
        audio = await self.synthesize(text)
        if self.interrupted.is_set():
            return False
        if audio:
            # L2 / SR-62: guard the send so a client disconnect mid-TTS doesn't
            # raise out of every caller (_pipeline, crew dispatch, greeting).
            if not await self._safe_send_bytes(audio):
                return False
            self.last_tts_end = self._tts_playback_end_time(audio)
        return True

    def _tts_playback_end_time(self, audio_bytes: bytes) -> float:
        """Return the monotonic timestamp at which the BROWSER finishes playing.

        The browser plays the audio for roughly `len(audio) / TTS_BYTES_PER_SEC`
        seconds after a small decode lag. Setting `last_tts_end` to this future
        time prevents the VAD from re-engaging while CODEC is still audibly
        speaking (which is what was creating the echo / self-interrupt loop).
        """
        if not audio_bytes:
            return time.monotonic()
        duration = len(audio_bytes) / float(TTS_BYTES_PER_SEC)
        return time.monotonic() + TTS_BROWSER_DECODE_LAG + duration

    # ── Skill dispatch ────────────────────────────────────────────────────

    # ── Phase 1 Step 3 §5.3 — voice AskUserQuestion handlers ─────────
    async def _poll_pending_question_for_voice(self) -> Optional[dict]:
        """Check pending_questions.json for a question this voice session
        should answer. Returns the record dict if one is ready to be
        announced, otherwise None.

        Strategy: pick the OLDEST status="pending" record whose
        operation_id matches this session's _cid OR whose asked_from is
        "voice"/"crew" (background ops the user might want to answer
        out-loud). Skip questions that have already been announced this
        session (tracked in self._announced_question_ids)."""
        if not hasattr(self, "_announced_question_ids"):
            self._announced_question_ids = set()
        try:
            from codec_ask_user import _load_pending_questions
            data = _load_pending_questions()
        except Exception as e:
            log.error(f"ask_user poll failed: {e}")
            return None
        for rec in data.get("pending_questions", []):
            if rec.get("status") != "pending":
                continue
            if rec.get("id") in self._announced_question_ids:
                continue
            asked_from = rec.get("asked_from", "")
            # Match: same correlation_id OR a background ask from a crew /
            # voice operation.
            if (rec.get("correlation_id") == self._cid
                    or asked_from in ("crew", "voice")):
                # L2 / SR-62: do NOT mark announced here — the caller marks it
                # only AFTER a successful announce, so a failed/closed-socket
                # announce retries next poll instead of silently swallowing the
                # question (which left the user answering something unheard).
                return rec
        return None

    async def _announce_pending_question(self, rec: dict) -> bool:
        """TTS-announce the question. Returns True only if it was actually
        spoken — the caller arms the answer slot + marks it announced on success
        only (L2 / SR-62)."""
        try:
            agent = rec.get("agent") or "CODEC"
            question = rec.get("question") or ""
            options = rec.get("options")
            if options:
                opt_phrase = "Options: " + ", or ".join(str(o) for o in options) + "."
                announcement = f"{agent} is asking: {question}. {opt_phrase}"
            else:
                announcement = f"{agent} is asking: {question}"
            if rec.get("consent_strict"):
                verb = rec.get("destructive_verb") or "confirm"
                announcement += (
                    f" This is a destructive action — please say the word "
                    f"'{verb}' clearly to confirm, or say 'cancel' to abort."
                )
            return await self._speak(announcement)
        except Exception as e:
            log.error(f"ask_user announce failed: {e}")
            return False
    async def _handle_voice_ask_user_answer(self, qid: str,
                                             user_text: str) -> None:
        """Route the user's spoken transcript to /api/agents/answer/{qid}
        — same backend as the PWA path. Resolves fuzzy options for
        non-strict-consent questions; passes strict ones through
        verbatim so codec_ask_user.submit_answer evaluates the literal
        verb-match rule.
        """
        try:
            from codec_ask_user import _find_pending_record, submit_answer
            rec = _find_pending_record(qid)
            if rec is None:
                await self._speak("Sorry, that question already expired.")
                return
            options = rec.get("options")
            strict = bool(rec.get("consent_strict"))
            destructive_verb = rec.get("destructive_verb")
            # Apply fuzzy match (skipped when strict=True per §5.3.1).
            if options and not strict:
                resolved = _resolve_voice_option_choice(
                    user_text, options, strict=False,
                    destructive_verb=destructive_verb)
                answer_to_send = resolved
            else:
                answer_to_send = user_text
            result = submit_answer(qid, answer_to_send, answered_via="voice")
            if result.get("ok"):
                await self._speak("Got it. Thanks.")
            elif result.get("rejected") and result.get("reason") == "ambiguous_consent":
                remaining = result.get("remaining_attempts", 0)
                if remaining > 0:
                    await self._speak(
                        f"That wasn't a clear confirmation. Please say "
                        f"'{destructive_verb or 'confirm'}' to proceed, or "
                        f"'cancel' to abort. {remaining} attempt left."
                    )
                    # Re-arm: same qid, next utterance is another attempt.
                    self._awaiting_ask_user = qid
                else:
                    await self._speak("Question canceled — too many ambiguous answers.")
            else:
                err = result.get("error", "")
                await self._speak(f"Couldn't record that answer: {err}")
        except Exception as e:
            log.error(f"ask_user answer handler failed: {e}")
            try:
                await self._speak("Something went wrong recording your answer.")
            except Exception:
                log.debug("voice: fallback TTS for ask_user error path failed", exc_info=True)

    async def dispatch_skill(self, skill: dict, user_text: str) -> Optional[str]:
        try:
            log.info(f"→ skill: {skill['name']}")
            loop = asyncio.get_event_loop()
            # Phase 1 Step 2: route the voice skill call through the unified
            # hook surface. self._cid is set at VoicePipeline.run entry per
            # Step 1 (d); plugins inherit it automatically.
            _skill_name = skill["name"]
            _skill_run = skill["run"]
            _voice_cid = getattr(self, "_cid", None) or _voice_correlation_id_var.get()

            def _run_with_hooks_sync():
                def _inner(t, _c):
                    return _skill_run(t)
                return _voice_run_with_hooks(
                    tool_name=_skill_name,
                    task=user_text,
                    context="",
                    transport="voice",
                    correlation_id=_voice_cid or "",
                    invoke=_inner,
                )

            ctx = contextvars.copy_context()
            result = await loop.run_in_executor(None, ctx.run, _run_with_hooks_sync)
            if isinstance(result, HookVeto):
                return (f"Skill '{_skill_name}' was vetoed by plugin "
                        f"'{result.plugin_name}': {result.reason}")
            result = str(result).strip() if result else ""
            if not result or result.lower() in ("none", "done, but no output.", ""):
                log.debug(f"Skill {skill['name']} empty — falling through to Qwen")
                return None
            return result
        except Exception as e:
            log.error(f"Skill error: {e}")
            return f"There was an error running that: {e}"

    async def _skill_to_speech(self, result: str) -> str:
        if len(result) <= 500:
            return result
        summary_msgs = [
            {"role": "system", "content": "Summarise in 1-2 spoken sentences. No formatting."},
            {"role": "user",   "content": result},
        ]
        summary = ""
        async for chunk in self._stream_qwen(summary_msgs, max_tokens=1000):
            summary += chunk
        return summary.strip() or result[:300]

    # ── Crew dispatch ─────────────────────────────────────────────────────

    _CREW_TRIGGERS = {
        "deep research":        ("deep_research",       lambda t: {"topic": t}),
        "research on":          ("deep_research",       lambda t: {"topic": t}),
        "run research":         ("deep_research",       lambda t: {"topic": t}),
        "morning briefing":     ("daily_briefing",      lambda t: {}),
        "daily briefing":       ("daily_briefing",      lambda t: {}),
        "my briefing":          ("daily_briefing",      lambda t: {}),
        "plan a trip":          ("trip_planner",        lambda t: {"destination": t}),
        "plan trip to":         ("trip_planner",        lambda t: {"destination": t}),
        "trip to":              ("trip_planner",        lambda t: {"destination": t}),
        "competitor analysis":  ("competitor_analysis", lambda t: {"topic": t}),
        "analyze competitors":  ("competitor_analysis", lambda t: {"topic": t}),
        "handle my email":      ("email_handler",       lambda t: {}),
        "check my inbox":       ("email_handler",       lambda t: {}),
        "email handler":        ("email_handler",       lambda t: {}),
        # Content Writer
        "write a blog":         ("content_writer",      lambda t: {"topic": t, "content_type": "blog post"}),
        "write an article":     ("content_writer",      lambda t: {"topic": t, "content_type": "article"}),
        "write a post":         ("content_writer",      lambda t: {"topic": t, "content_type": "LinkedIn post"}),
        "write content":        ("content_writer",      lambda t: {"topic": t}),
        "write a newsletter":   ("content_writer",      lambda t: {"topic": t, "content_type": "newsletter"}),
        # Meeting Summarizer
        "summarize meeting":    ("meeting_summarizer",  lambda t: {"meeting_input": t}),
        "meeting summary":      ("meeting_summarizer",  lambda t: {"meeting_input": t}),
        "summarize the call":   ("meeting_summarizer",  lambda t: {"meeting_input": "summarize the last voice call"}),
        "meeting notes":        ("meeting_summarizer",  lambda t: {"meeting_input": t}),
        # Invoice Generator
        "create invoice":       ("invoice_generator",   lambda t: {"invoice_details": t}),
        "generate invoice":     ("invoice_generator",   lambda t: {"invoice_details": t}),
        "make invoice":         ("invoice_generator",   lambda t: {"invoice_details": t}),
        "invoice for":          ("invoice_generator",   lambda t: {"invoice_details": t}),
        "bill client":          ("invoice_generator",   lambda t: {"invoice_details": t}),
        # Project Manager
        "project status":       ("project_manager",     lambda t: {"project": t}),
        "project update":       ("project_manager",     lambda t: {"project": t}),
        "check project":        ("project_manager",     lambda t: {"project": t}),
        "project report":       ("project_manager",     lambda t: {"project": t}),
        "how is project":       ("project_manager",     lambda t: {"project": t}),
        "status report":        ("project_manager",     lambda t: {"project": t}),
    }

    async def dispatch_crew_from_voice(self, user_text: str) -> Optional[str]:
        low = user_text.lower()
        for trigger, (crew_name, arg_builder) in self._CREW_TRIGGERS.items():
            if trigger in low:
                topic = low.split(trigger, 1)[-1].strip(" ?.,")
                if not topic:
                    topic = low

                label = crew_name.replace("_", " ")
                notify = f"Starting {label}. This will take a few minutes. I'll keep you posted."
                # L2 / SR-62: guarded sends — a disconnect during a multi-minute
                # crew must not raise out of the callback (the crew runs detached
                # in the background; an unguarded send would surface as an error).
                await self._safe_send_json({"type": "transcript", "role": "assistant", "text": notify})
                audio = await self.synthesize(notify)
                if audio and await self._safe_send_bytes(audio):
                    self.last_tts_end = self._tts_playback_end_time(audio)

                async def voice_cb(update):
                    status = update.get("status", "")
                    agent  = update.get("agent", "")
                    tool   = update.get("tool", "")
                    if status == "tool_call" and tool:
                        msg = f"{agent} is using {tool}."
                    elif status == "agent_start":
                        msg = f"{agent} is starting, step {update.get('task_num','')} of {update.get('total','')}."
                    else:
                        return
                    if not await self._safe_send_json({"type": "transcript", "role": "assistant", "text": msg}):
                        return
                    a = await self.synthesize(msg)
                    if a and await self._safe_send_bytes(a):
                        self.last_tts_end = self._tts_playback_end_time(a)

                _dash = os.path.dirname(os.path.abspath(__file__))
                if _dash not in sys.path:
                    sys.path.insert(0, _dash)
                from codec_agents import run_crew
                kwargs = arg_builder(topic)
                result = await run_crew(crew_name, callback=voice_cb, **kwargs)

                if result.get("status") == "complete":
                    full    = result.get("result", "")
                    elapsed = result.get("elapsed_seconds", "?")
                    if len(full) > 500:
                        summary = f"{label.title()} complete. Took {elapsed} seconds."
                        if re.search(r'https://docs\.google\.com', full):
                            summary += " Full report saved to Google Docs."
                        else:
                            summary += " " + full[:300]
                        return summary
                    return full
                return f"Agent error: {result.get('error', 'unknown')}"

        return None

    # ── Memory ────────────────────────────────────────────────────────────

    def save_to_memory(self):
        # L2 / SR-62: idempotent. BOTH VoicePipeline.run()'s finally AND the
        # websocket route's finally (routes/websocket.py) call this — without a
        # guard every voice turn was written to memory.db twice per session.
        # First successful full save wins; a mid-loop failure leaves the flag
        # unset so a later call can retry.
        if getattr(self, "_memory_saved", False):
            return
        try:
            _dash = os.path.dirname(os.path.abspath(__file__))
            if _dash not in sys.path:
                sys.path.insert(0, _dash)
            from codec_memory import CodecMemory
            mem   = CodecMemory()
            saved = 0
            for msg in self.messages:
                role = msg.get("role", "")
                if role not in ("user", "assistant"):
                    continue
                content = msg.get("content", "")
                if isinstance(content, list):
                    content = " ".join(str(p) for p in content)
                if not content:
                    continue
                mem.save(self.session_id, role, str(content)[:2000])
                saved += 1
            self._memory_saved = True
            log.info(f"Saved {saved} messages → {self.session_id}")
        except Exception as e:
            log.error(f"Memory save error: {e}")

    # ── Concurrency helpers (L2 / SR-62) ───────────────────────────────────

    def _enqueue_utterance(self, utterance: bytes) -> None:
        """Non-blocking enqueue so the receiver loop never parks on a full
        queue. `await queue.put()` on the maxsize=3 utterance_queue would block
        the receiver while the pipeline is slow — and a blocked receiver stops
        reading interrupt / ping control frames (head-of-line block). On
        overflow we drop the OLDEST queued utterance to make room for the
        newest (most recent speech is the most relevant)."""
        try:
            self.utterance_queue.put_nowait(utterance)
        except asyncio.QueueFull:
            try:
                self.utterance_queue.get_nowait()
                log.warning("[Voice] utterance queue full — dropped oldest to enqueue newest")
                self.utterance_queue.put_nowait(utterance)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    @staticmethod
    def _log_task_exception(task: "asyncio.Task") -> None:
        """done_callback for fire-and-forget tasks: retrieve + log any exception
        so it doesn't become an un-retrieved-future warning (and isn't silently
        swallowed)."""
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            log.warning(f"[Voice] background task failed: {type(exc).__name__}: {exc}")

    @staticmethod
    def _spawn_detached(argv: list) -> None:
        """Fire-and-forget a cosmetic side-effect subprocess (camera-shutter
        sound, screen overlay). Popen returns immediately, so no event-loop
        offload is needed — this replaces the orphaned run_in_executor futures
        (+ deprecated get_event_loop()) the overlay used to create. A missing
        afplay / tkinter is non-fatal. (L2 / SR-62.)"""
        try:
            subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            log.debug(f"[Voice] detached spawn failed (non-fatal): {e}")

    # ── Audio receiver task ────────────────────────────────────────────────

    async def _audio_receiver(self):
        """
        Continuously reads WebSocket messages (bytes = audio, text = control).
        - Bytes: feeds VAD; queues complete utterances for processing.
        - Text {"type":"interrupt"}: sets self.interrupted to stop active TTS.
        - Text {"type":"ping"}: responds with pong (heartbeat keepalive).
        """
        try:
            while True:
                msg = await self.ws.receive()
                msg_type = msg.get("type", "")

                if msg_type == "websocket.disconnect":
                    log.info("WebSocket disconnected in receiver")
                    # L2 / SR-62: flag the socket dead so any in-flight _speak /
                    # crew-callback send stops instead of raising on the close.
                    self._ws_alive = False
                    await self.utterance_queue.put(None)  # signal pipeline to stop
                    # If unexpected, save state for possible resume
                    if self._disconnect_reason != "user":
                        self._save_for_resume()
                    break

                # ── Text / JSON control message ──
                raw_text = msg.get("text")
                if raw_text:
                    try:
                        ctrl = json.loads(raw_text)
                        ctrl_type = ctrl.get("type", "")

                        if ctrl_type == "interrupt":
                            if self.processing:
                                log.info("Interrupt received")
                                self.interrupted.set()

                        elif ctrl_type == "your_turn":
                            # Force-flush audio buffer as an utterance (user says "your turn")
                            if self.audio_buffer and len(self.audio_buffer) >= MIN_SPEECH_BYTES:
                                utterance = bytes(self.audio_buffer)
                                self.audio_buffer = bytearray()
                                self.is_speaking = False
                                log.info(f"Your-turn: flushing {len(utterance)} bytes")
                                self._enqueue_utterance(utterance)
                            elif self.audio_buffer:
                                # Buffer too short — discard and notify
                                self.audio_buffer = bytearray()
                                self.is_speaking = False
                                await self.ws.send_json({"type": "status", "status": "listening"})
                                log.debug("Your-turn: buffer too short, discarded")
                            else:
                                log.info("Your-turn: no audio buffered")
                        elif ctrl_type == "nudge":
                            # User tapped "still there?" — send reassurance
                            if self.processing:
                                await self.ws.send_json({"type": "transcript", "role": "system", "text": "Still processing — hang on…"})
                                log.info("Nudge acknowledged — still processing")
                        elif ctrl_type == "ping":
                            await self.ws.send_json({"type": "pong"})

                        elif ctrl_type == "end_call":
                            # User intentionally ending the call — no resume needed
                            self._disconnect_reason = "user"
                            log.info("User ended call intentionally")
                            await self.utterance_queue.put(None)
                            return

                        elif ctrl_type == "hold_start":
                            # User started hold-to-talk — ensure we're in listening mode
                            log.info("Hold-to-talk started")
                    except Exception as e:
                        log.warning(f"WS text parse warning: {e}")
                    continue

                # ── Audio bytes ──
                raw_bytes = msg.get("bytes")
                if not raw_bytes:
                    continue

                # While processing: still feed audio to VAD so follow-up
                # speech is captured, but also check for interrupt.
                if self.processing:
                    rms = self._rms(raw_bytes)
                    if (rms > INTERRUPT_THRESHOLD and
                            time.monotonic() - self.last_tts_end > VAD_ECHO_COOLDOWN):
                        log.info(f"Interrupt by audio energy (RMS {rms:.0f})")
                        self.interrupted.set()
                    # Feed VAD so utterance is buffered (queued once processing ends)
                    utterance = self.feed_audio(raw_bytes)
                    if utterance:
                        self._enqueue_utterance(utterance)
                    continue

                # Normal VAD feeding — trigger warmup on speech start
                was_speaking = self.is_speaking
                utterance = self.feed_audio(raw_bytes)
                if self.is_speaking and not was_speaking and not self._warmed_up:
                    # L2 / SR-62: keep a ref (so it isn't GC'd mid-flight) +
                    # attach an exception logger (so a warmup failure isn't an
                    # un-retrieved-future warning). Cancelled in run()'s finally.
                    self._warmup_task = asyncio.create_task(self.warmup_llm())
                    self._warmup_task.add_done_callback(self._log_task_exception)
                if utterance:
                    self._enqueue_utterance(utterance)

        except Exception as e:
            log.error(f"Receiver error: {type(e).__name__}: {e}")
            # Try to send reconnect advisory before dying
            try:
                await self.ws.send_json({
                    "type": "reconnect",
                    "session_id": self.session_id,
                    "reason": str(e),
                })
            except Exception:
                pass  # connection may already be dead
            self._save_for_resume()
            await self.utterance_queue.put(None)

    # ── Pipeline processor task ───────────────────────────────────────────

    async def _pipeline(self):
        """
        Dequeues utterances and runs the full STT → skill/LLM → TTS pipeline.
        Checks self.interrupted before/after each TTS chunk.
        """
        while True:
            try:
                utterance = await asyncio.wait_for(self.utterance_queue.get(), timeout=60)
            except asyncio.TimeoutError:
                continue

            if utterance is None:  # disconnect signal
                break

            self.interrupted.clear()
            self.processing = True
            await self.ws.send_json({"type": "status", "status": "processing"})
            await self.ws.send_json({"type": "hint", "text": "Speak to interrupt"})

            try:
                # 1. STT
                user_text = await self.transcribe(utterance)
                if not user_text:
                    self.processing = False
                    await self.ws.send_json({"type": "status", "status": "listening"})
                    continue

                log.info(f"User: {user_text}")
                await self.ws.send_json({"type": "transcript", "role": "user", "text": user_text})

                # Phase 1 Step 3 §5.3 — single-question listen mode.
                # If an AskUserQuestion is awaiting an answer for THIS
                # voice session, route this transcript as the answer
                # (NOT as a new command). Resolve fuzzy options for
                # non-strict-consent questions; let the strict-consent
                # gate in codec_ask_user.submit_answer evaluate strict
                # ones literally.
                if self._awaiting_ask_user:
                    qid = self._awaiting_ask_user
                    self._awaiting_ask_user = None  # consume the slot
                    await self._handle_voice_ask_user_answer(qid, user_text)
                    self.processing = False
                    await self.ws.send_json({"type": "status", "status": "listening"})
                    continue

                # Otherwise: poll pending_questions.json for a question
                # this session should answer. If one exists, announce it
                # via TTS and switch into single-question listen mode for
                # the NEXT utterance (don't process this one as a command).
                _q = await self._poll_pending_question_for_voice()
                if _q is not None and await self._announce_pending_question(_q):
                    # L2 / SR-62: arm the answer slot + mark announced ONLY after
                    # the user actually heard it. If the announce failed (TTS
                    # down / socket closed) we fall through to process this
                    # utterance normally and re-poll the question next loop.
                    self._awaiting_ask_user = _q.get("id")
                    self._announced_question_ids.add(_q.get("id"))
                    self.processing = False
                    await self.ws.send_json({"type": "status", "status": "listening"})
                    continue

                # 1b. Screenshot + Vision — "look at my screen" etc.
                if self._is_screen_request(user_text):
                    log.info("Screen analysis requested")
                    await self.ws.send_json({"type": "status", "status": "analyzing_screen"})
                    # Camera shutter sound + overlay for visual feedback
                    self._spawn_detached(["afplay", "/System/Library/Sounds/Tink.aiff"])
                    self._spawn_detached(
                        [sys.executable, "-c",
                         "import tkinter as tk;r=tk.Tk();r.overrideredirect(1);r.attributes('-topmost',1);"
                         "r.attributes('-alpha',0.95);r.configure(bg='#0a0a0a');"
                         "sw=r.winfo_screenwidth();sh=r.winfo_screenheight();"
                         "w,h=360,60;r.geometry(f'{w}x{h}+{(sw-w)//2}+{sh-130}');"
                         "c=tk.Canvas(r,bg='#0a0a0a',highlightthickness=0,width=w,height=h);c.pack();"
                         "c.create_rectangle(1,1,w-1,h-1,outline='#00aaff',width=1);"
                         "c.create_text(w//2,h//2,text='\U0001f4f7  Analyzing your screen...',fill='#00aaff',font=('Helvetica',13));"
                         "r.after(8000,r.destroy);r.mainloop()"])
                    screenshot_b64 = await self._take_screenshot()
                    log.info(f"Screenshot taken: {'OK' if screenshot_b64 else 'FAILED'}")
                    if screenshot_b64:
                        self.interrupted.clear()
                        await self.ws.send_json({"type": "status", "status": "processing"})
                        log.info("Sending to vision model...")
                        vision_desc = await self._analyze_screenshot(screenshot_b64, user_text)
                        log.info(f"Vision result: {'OK (' + str(len(vision_desc)) + ' chars)' if vision_desc else 'EMPTY/FAILED'}")
                        # Check for interrupt after vision inference (user may have spoken)
                        if self.interrupted.is_set():
                            log.info("Interrupted during vision inference — discarding result")
                            self.processing = False
                            await self.ws.send_json({"type": "status", "status": "listening"})
                            continue
                        if vision_desc:
                            # Speak the vision description directly — no LLM needed
                            # Clean up vision output for TTS
                            clean_desc = re.sub(r'[*#`\[\]]', '', vision_desc).strip()
                            log.info(f"Speaking vision result directly: {clean_desc[:100]}...")
                            self.messages.append({"role": "user", "content": user_text})
                            screen_context = f"I looked at your screen. Here's what I see: {clean_desc}"
                            self.messages.append({"role": "assistant", "content": screen_context})
                            self.interrupted.clear()
                            # Send transcript and speak
                            response_text = f"Here's what I see on your screen. {clean_desc}"
                            await self.ws.send_json({
                                "type": "transcript", "role": "assistant",
                                "text": response_text
                            })
                            await self._speak(response_text)
                            self.processing = False
                            await self.ws.send_json({"type": "status", "status": "listening"})
                            continue
                    # Screenshot failed — tell user and fall through to normal LLM
                    fail_msg = "Sorry, I couldn't capture your screen right now. What would you like help with?"
                    await self.ws.send_json({"type": "transcript", "role": "assistant", "text": fail_msg})
                    await self._speak(fail_msg)
                    self.processing = False
                    await self.ws.send_json({"type": "status", "status": "listening"})
                    continue

                # 2a. Crew dispatch
                crew_result = await self.dispatch_crew_from_voice(user_text)
                if crew_result:
                    self.messages.append({"role": "user",      "content": user_text})
                    self.messages.append({"role": "assistant", "content": crew_result})
                    await self.ws.send_json({"type": "transcript", "role": "assistant", "text": crew_result})
                    await self._speak(crew_result)
                    self.processing = False
                    await self.ws.send_json({"type": "status", "status": "listening"})
                    continue

                # 2b. Skill dispatch
                skill_match = self._match_skill(user_text)
                if skill_match:
                    raw_result = await self.dispatch_skill(skill_match, user_text)
                    if raw_result is not None:
                        spoken = await self._skill_to_speech(raw_result)
                        self.messages.append({"role": "user",      "content": user_text})
                        self.messages.append({"role": "assistant", "content": spoken})
                        await self.ws.send_json({"type": "transcript", "role": "assistant", "text": spoken})
                        await self._speak(spoken)
                        self.processing = False
                        await self.ws.send_json({"type": "status", "status": "listening"})
                        continue
                    # skill returned nothing → fall through to LLM

                # 3. LLM streaming path
                sentence_buf = ""
                full_text    = ""
                interrupted_mid = False

                async for token in self.generate_response(user_text):
                    if self.interrupted.is_set():
                        interrupted_mid = True
                        break
                    sentence_buf += token
                    full_text    += token

                    to_speak, sentence_buf = self._flush_on_boundary(sentence_buf)
                    if to_speak:
                        await self.ws.send_json({"type": "transcript_chunk", "text": to_speak})
                        ok = await self._speak(to_speak)
                        if not ok:
                            interrupted_mid = True
                            break

                # Flush remainder (only if not interrupted)
                if not interrupted_mid and sentence_buf.strip():
                    await self.ws.send_json({"type": "transcript_chunk", "text": sentence_buf})
                    await self._speak(sentence_buf.strip())

                await self.ws.send_json({
                    "type": "transcript", "role": "assistant",
                    "text": full_text.strip() + (" [interrupted]" if interrupted_mid else "")
                })

                if interrupted_mid:
                    log.info("Response interrupted by user")
            except Exception as e:
                log.error(f"Pipeline error: {type(e).__name__}: {e}")
            finally:
                self.interrupted.clear()
                self.processing = False
                await self.ws.send_json({"type": "status", "status": "listening"})

    # ── Main entry point ──────────────────────────────────────────────────

    async def run(self):
        is_resumed = self._is_resumed
        # One correlation_id per voice session. Inherited by any nested
        # tool_call / tool_result / voice_interrupt emits.
        cid = secrets.token_hex(6)
        cid_token = _voice_correlation_id_var.set(cid)
        self._cid = cid
        # Phase 1 Step 3 §5.3 — single-question listen mode state.
        # When non-None, the next user utterance is treated as the answer
        # to the pending question (NOT a new wake-word command). Cleared
        # after the answer is routed to /api/agents/answer/{id}.
        self._awaiting_ask_user = None
        run_t0 = time.monotonic()
        log.info(f"Session {'resumed' if is_resumed else 'started'}: {self.session_id}")
        # Phase 1 Step 3 §5.3 — touch the active-session marker so
        # codec_ask_user knows whether to announce-and-listen vs defer
        # to PWA-only.
        _touch_voice_session_marker(self.session_id)
        try:
            _voice_log_event("voice_session_start", "codec-voice",
                             f"Voice session {'resumed' if is_resumed else 'started'}",
                             extra={"session_id": self.session_id,
                                    "resume_id": self.session_id if is_resumed else None},
                             correlation_id=cid)
        except Exception:
            log.debug("voice: voice_session_start audit emit failed", exc_info=True)
        # Phase 1 Step 2: fire on_operation_start hooks (per-plugin, not the
        # voice_session_start audit event above — that's Step 1 vocabulary
        # and intentionally unchanged). Hook layer never raises.
        try:
            _voice_emit_op_start(operation_id=self.session_id,
                                 transport="voice",
                                 correlation_id=cid)
        except Exception:
            log.debug("voice: on_operation_start hook emit failed", exc_info=True)

        # Send session ID so client can reconnect to this session
        await self.ws.send_json({"type": "session", "session_id": self.session_id})

        if is_resumed:
            # Reconnected — short acknowledgement, no full greeting
            greeting = "Reconnected. I'm still here. Go ahead."
            self.messages.append({"role": "assistant", "content": greeting})
            await self.ws.send_json({"type": "transcript", "role": "assistant", "text": greeting})
            g_audio = await self.synthesize(greeting)
            if g_audio:
                await self.ws.send_bytes(g_audio)
                self.last_tts_end = time.monotonic()
        else:
            # Fresh session — full greeting
            _user_name = ""
            try:
                _user_name = _cfg.get("user_name", "")
            except NameError:
                pass
            if _user_name:
                greeting = f"Greetings {_user_name}. CODEC is online. All systems local. What do you need?"
            else:
                greeting = "Greetings. CODEC is online. All systems local. What do you need?"
            self.messages.append({"role": "assistant", "content": greeting})
            await self.ws.send_json({"type": "transcript", "role": "assistant", "text": greeting})
            g_audio = await self.synthesize(greeting)
            if g_audio:
                await self.ws.send_bytes(g_audio)
                self.last_tts_end = time.monotonic()

        # Run both tasks concurrently — receiver feeds queue, pipeline processes
        receiver = asyncio.create_task(self._audio_receiver())
        pipeline = asyncio.create_task(self._pipeline())

        run_outcome = "ok"
        run_error_type = None
        run_error = None
        try:
            await asyncio.gather(receiver, pipeline)
        except Exception as e:
            run_outcome = "error"
            run_error_type = type(e).__name__
            run_error = str(e)[:500]
            log.error(f"Session error: {type(e).__name__}: {e}")
        finally:
            receiver.cancel()
            pipeline.cancel()
            # L2 / SR-62: cancel the fire-and-forget warmup task if still
            # pending (was orphaned before — no ref, no cancel).
            _wt = getattr(self, "_warmup_task", None)
            if _wt is not None and not _wt.done():
                _wt.cancel()
            self.save_to_memory()
            log.info(f"Session ended: {self.session_id}")
            try:
                duration_ms = (time.monotonic() - run_t0) * 1000.0
                turns = sum(1 for m in self.messages if m.get("role") == "user")
                _voice_log_event("voice_session_end", "codec-voice",
                                 f"Voice session ended: {self.session_id}",
                                 extra={"session_id": self.session_id,
                                        "turns": turns,
                                        "disconnect_reason": self._disconnect_reason},
                                 outcome=run_outcome,
                                 duration_ms=duration_ms,
                                 error_type=run_error_type,
                                 error=run_error,
                                 correlation_id=cid)
            except Exception:
                log.debug("voice: voice_session_end audit emit failed", exc_info=True)
            # Phase 1 Step 2: fire on_operation_end hooks. Same caveat as
            # the start emit above — voice_session_end audit event is Step 1
            # vocabulary and unchanged; on_operation_end is the hook-layer
            # event with §11 Q6 vocabulary.
            try:
                _voice_emit_op_end(operation_id=self.session_id,
                                   transport="voice",
                                   correlation_id=cid,
                                   duration_ms=duration_ms,
                                   outcome=run_outcome)
            except Exception:
                log.debug("voice: on_operation_end hook emit failed", exc_info=True)
            try:
                _voice_correlation_id_var.reset(cid_token)
            except Exception:
                log.debug("voice: correlation_id contextvar reset failed", exc_info=True)
            # Phase 1 Step 3 §5.3 — clear the active-session marker so
            # codec_ask_user falls back to PWA-only for any subsequent
            # questions. Best-effort; failures don't break shutdown.
            _clear_voice_session_marker()

    async def close(self):
        try:
            await self._http.aclose()
        except Exception as e:
            log.warning(f"HTTP client close warning: {e}")