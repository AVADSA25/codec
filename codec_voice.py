"""
CODEC Voice — Own voice-to-voice pipeline
Replaces Pipecat with a single-file WebSocket server.
WebSocket receives raw PCM16 audio chunks from browser,
runs energy-based VAD, transcribes with Whisper (8084),
dispatches CODEC skills mid-call or streams Qwen (8081),
TTS via Kokoro (8085), audio streams back to browser.
"""
import asyncio
import io
import json
import os
import re
import sqlite3
import time
import wave
from datetime import datetime
from typing import Optional

import httpx
import numpy as np

# ── CONFIG — defaults (overridden by ~/.codec/config.json) ──────────────────
WHISPER_URL   = "http://localhost:8084/v1/audio/transcriptions"
WHISPER_MODEL = "mlx-community/whisper-large-v3-turbo"
QWEN_URL      = "http://localhost:8081/v1/chat/completions"
QWEN_MODEL    = "mlx-community/Qwen3.5-35B-A3B-4bit"
LLM_KWARGS    = {}                              # extra body params (e.g. chat_template_kwargs)
KOKORO_URL    = "http://localhost:8085/v1/audio/speech"
KOKORO_MODEL  = "mlx-community/Kokoro-82M-bf16"
KOKORO_VOICE  = "am_adam"
DB_PATH       = os.path.expanduser("~/.q_memory.db")
SKILLS_DIR    = os.path.expanduser("~/.codec/skills")

# Load all values from ~/.codec/config.json at import time
_CONFIG_PATH = os.path.expanduser("~/.codec/config.json")
try:
    with open(_CONFIG_PATH) as _f:
        _cfg = json.load(_f)

    # LLM — llm_base_url already includes /v1, just append /chat/completions
    _llm_base = _cfg.get("llm_base_url", "http://localhost:8081/v1").rstrip("/")
    QWEN_URL   = _llm_base + "/chat/completions"
    QWEN_MODEL = _cfg.get("llm_model", QWEN_MODEL)
    LLM_KWARGS = {k: v for k, v in _cfg.get("llm_kwargs", {}).items()
                  if k != "enable_thinking"}   # safe passthrough

    # TTS
    KOKORO_URL   = _cfg.get("tts_url",   KOKORO_URL)
    KOKORO_MODEL = _cfg.get("tts_model", KOKORO_MODEL)
    KOKORO_VOICE = _cfg.get("tts_voice", KOKORO_VOICE)

    # STT
    WHISPER_URL   = _cfg.get("stt_url",   WHISPER_URL)
    WHISPER_MODEL = _cfg.get("stt_model", WHISPER_MODEL)

except Exception as _e:
    print(f"[Voice] Config load warning: {_e} — using defaults")

# ── VAD settings ──────────────────────────────────────────────────────────
# Energy-based VAD tuned for conversational voice-to-voice on Mac Studio.
# Pipecat used Silero (neural VAD) — we compensate with tighter thresholds.
VAD_SILENCE_THRESHOLD  = 800    # RMS below this = silence (raised from 500 to ignore room noise)
VAD_SILENCE_DURATION   = 2.2    # seconds of silence before flushing (longer = fewer mid-sentence cuts)
VAD_MIN_SPEECH_SECONDS = 0.6    # minimum sustained speech before even considering a flush
VAD_ECHO_COOLDOWN      = 1.5    # seconds to ignore mic after Q finishes speaking (avoids echo pickup)
SAMPLE_RATE            = 16000
BYTES_PER_SAMPLE       = 2      # int16
MIN_SPEECH_BYTES       = int(SAMPLE_RATE * BYTES_PER_SAMPLE * VAD_MIN_SPEECH_SECONDS)

# ── Whisper noise filter ───────────────────────────────────────────────────
# Whisper hallucinates these phrases from ambient noise / silence segments.
NOISE_WORDS = {
    "you", "thank you", "thanks", "thanks for watching", "bye", "goodbye",
    "see you", "see you next time", "please subscribe", "like and subscribe",
    "", "hmm", "uh", "oh", "hm", "um", "yeah", "yep", "mm", "mhm",
    "okay", "ok", "right", "sure", "yes", "no", "hey", "hi", "hello",
    "so", "well", "um hmm", "uh huh", "ah", "er",
}

# ── System Prompt ─────────────────────────────────────────────────────────
def _build_system_prompt() -> str:
    """Build system prompt with injected current datetime for Madrid timezone."""
    import datetime as _dt
    now = _dt.datetime.now()
    day_names = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    day_name = day_names[now.weekday()]
    date_str = now.strftime(f"{day_name}, %-d %B %Y")
    time_str = now.strftime("%-I:%M %p")
    return f"""You are Q — CODEC Voice, a JARVIS-class local AI assistant running on a Mac Studio M1 Ultra.
The user is M. You are M's private, always-on, fully local AI. No cloud. No logs outside this machine.

CURRENT DATE AND TIME: {date_str}, {time_str} (Madrid / Europe time)
Use this to correctly interpret "today", "tomorrow", "this afternoon", etc.

━━ VOICE OUTPUT RULES ━━
Your responses are converted to speech by Kokoro TTS. Format for ears, not eyes:
- NO markdown: no asterisks, no hashtags, no bullet points, no tables, no dashes
- NO special characters or symbols
- NO numbered lists — speak naturally instead
- Keep responses SHORT: 1-3 sentences normally. Expand only when M asks
- Start with a natural spoken filler: "Right,", "So,", "Done.", "Got it.", "Sure."
- Speak like a sharp, calm person — not a chatbot

━━ INPUT HANDLING ━━
Your input is live voice transcription (Whisper STT). Expect occasional errors:
- "iq" or "hey q" at the start = just M calling your name, ignore it
- "uh", "um", "er" = filler, ignore
- Strange words = guess the intended meaning from context
- Never mention transcription errors to M unless they cause real confusion
- Simple math questions ("one plus one", "what is 7 times 8") = answer directly with the number, nothing else
- "Speed test [X]" where X is a simple question = just answer X quickly and directly, do not run system diagnostics

━━ SKILLS AND ACTIONS ━━
You have 34 built-in skills that execute immediately mid-call:
Google Calendar, Gmail, Drive, Tasks, Docs, Sheets, Chrome, system controls, and more.

When M asks you to DO something (add event, send email, check tasks, search, etc.):
1. The skill runs automatically and returns a result string
2. You receive that result and report it conversationally to M
3. NEVER delegate to Lucy or any other assistant — you have the tools, you do it

━━ ANTI-HALLUCINATION RULES (CRITICAL) ━━
Only confirm actions that the skill result explicitly confirms. Specifically:
- Skill says "Done. [event] added" → confirm it was done
- Skill says "No events today" or "calendar is clear" → that means READ succeeded, NOT that you created anything — do NOT say you added an event
- Skill returns an error → report the error honestly to M, ask if they want to retry
- If you are unsure whether something was done → say "Let me check" and report the actual result
- NEVER say "I have added", "I have sent", "I have created" unless the skill confirmed it
- NEVER pretend success. M trusts you. Be honest.

━━ MEMORY ━━
All voice conversations are saved to shared CODEC memory. If M asks you to remember something, confirm: "Saved to memory." M can retrieve it later via CODEC chat.

━━ PERSONA ━━
Honest. Direct. Dry wit at 10 percent. Commanding presence. You give straight answers.
One well-placed sarcastic remark is allowed per conversation. One.
You are not a customer service bot. You are M's right hand."""

SYSTEM_PROMPT = _build_system_prompt()


# ─────────────────────────────────────────────────────────────────────────────
class VoicePipeline:
    """One voice session per WebSocket connection."""

    def __init__(self, websocket):
        self.ws          = websocket
        self.session_id  = "voice_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        self.messages    = [{"role": "system", "content": _build_system_prompt()}]
        self.audio_buffer    = bytearray()
        self.last_speech_time = 0.0
        self.is_speaking     = False
        self.processing      = False
        self.last_tts_end    = 0.0  # monotonic time when Q last finished speaking
        self.skills       = {}
        self._http        = httpx.AsyncClient(timeout=120.0)
        self._load_skills()

    # ── Skill loader ──────────────────────────────────────────────────────

    def _load_skills(self):
        """Import every CODEC skill; index by trigger phrases."""
        if not os.path.isdir(SKILLS_DIR):
            return
        import importlib.util
        for fname in sorted(os.listdir(SKILLS_DIR)):
            if not fname.endswith(".py") or fname.startswith("_"):
                continue
            try:
                path = os.path.join(SKILLS_DIR, fname)
                spec = importlib.util.spec_from_file_location(fname[:-3], path)
                mod  = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                triggers = getattr(mod, "SKILL_TRIGGERS", [])
                if triggers and hasattr(mod, "run"):
                    self.skills[fname[:-3]] = {
                        "triggers": [t.lower() for t in triggers],
                        "run":      mod.run,
                        "desc":     getattr(mod, "SKILL_DESCRIPTION", ""),
                    }
            except Exception as e:
                print(f"[Voice] Skill load error {fname}: {e}")
        print(f"[Voice] {len(self.skills)} skills loaded for voice dispatch")

    # Skills that should NEVER fire from voice (too noisy / too short triggers)
    _VOICE_SKIP_SKILLS = {"calculator", "app_switch", "brightness", "clipboard"}

    def _match_skill(self, text: str) -> Optional[dict]:
        """
        Return best matching skill or None.

        Rules for voice dispatch:
        1. Skip skills in _VOICE_SKIP_SKILLS (too noisy when triggered by voice)
        2. Only match triggers that are >= 3 words — prevents single words like
           'plus', 'add', 'calendar' firing in the middle of casual conversation
        3. Among all matches, pick the longest trigger (most specific wins)
        """
        text_lower = text.lower().strip()
        best_match = None
        best_len   = 0

        for name, skill in self.skills.items():
            if name in self._VOICE_SKIP_SKILLS:
                continue
            for trigger in skill["triggers"]:
                # Require trigger to be at least 3 words long
                if len(trigger.split()) < 3:
                    continue
                if trigger in text_lower and len(trigger) > best_len:
                    best_len   = len(trigger)
                    best_match = {"name": name, "run": skill["run"]}

        return best_match

    # ── VAD ───────────────────────────────────────────────────────────────

    @staticmethod
    def _rms(chunk: bytes) -> float:
        if len(chunk) < 2:
            return 0.0
        samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
        return float(np.sqrt(np.mean(samples ** 2)))

    def feed_audio(self, chunk: bytes) -> Optional[bytes]:
        """
        Feed a PCM16 chunk into the VAD state machine.
        Returns a complete utterance when silence follows speech, else None.
        """
        rms = self._rms(chunk)
        now = time.monotonic()

        # Echo cooldown: ignore mic for VAD_ECHO_COOLDOWN seconds after Q spoke.
        # This prevents picking up TTS room echo as a new utterance.
        if now - self.last_tts_end < VAD_ECHO_COOLDOWN:
            return None

        if rms > VAD_SILENCE_THRESHOLD:
            self.is_speaking = True
            self.last_speech_time = now
            self.audio_buffer.extend(chunk)
            return None

        if self.is_speaking:
            self.audio_buffer.extend(chunk)
            if now - self.last_speech_time > VAD_SILENCE_DURATION:
                self.is_speaking = False
                if len(self.audio_buffer) >= MIN_SPEECH_BYTES:
                    utterance = bytes(self.audio_buffer)
                    self.audio_buffer = bytearray()
                    return utterance
                # Too short — discard as noise
                self.audio_buffer = bytearray()

        return None

    # ── STT ───────────────────────────────────────────────────────────────

    async def transcribe(self, pcm: bytes) -> str:
        """PCM16 mono 16 kHz → Whisper → text string."""
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
                text = r.json().get("text", "").strip()
                clean = text.lower().rstrip(".!?, ")
                # Reject Whisper hallucinations: noise words or < 3 meaningful words
                if clean in NOISE_WORDS:
                    print(f"[Voice] Discarded noise: '{text}'")
                    return ""
                words = [w for w in clean.split() if w not in {"uh","um","er","hmm","ah"}]
                if len(words) < 2:
                    print(f"[Voice] Discarded too short: '{text}'")
                    return ""
                return text
            print(f"[Voice] Whisper {r.status_code}: {r.text[:200]}")
        except Exception as e:
            print(f"[Voice] Whisper error: {e}")
        return ""

    # ── LLM ───────────────────────────────────────────────────────────────

    async def _stream_qwen(self, messages: list, max_tokens: int = 512):
        """Yield text chunks from Qwen streaming endpoint."""
        payload = {
            "model": QWEN_MODEL,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.7,
            "stream": True,
            **LLM_KWARGS,
        }
        try:
            async with self._http.stream(
                "POST", QWEN_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
            ) as resp:
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        token = json.loads(data)["choices"][0].get("delta", {}).get("content", "")
                        if token:
                            # Strip any stray thinking tags
                            token = re.sub(r"<think>[\s\S]*?</think>", "", token)
                            if token:
                                yield token
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
        except Exception as e:
            print(f"[Voice] Qwen error: {e}")
            yield "Sorry, I had a processing error."

    async def generate_response(self, user_text: str):
        """Add user turn, stream assistant reply, append to history."""
        self.messages.append({"role": "user", "content": user_text})
        full = ""
        async for chunk in self._stream_qwen(self.messages):
            full += chunk
            yield chunk
        self.messages.append({"role": "assistant", "content": full})

    # ── TTS ───────────────────────────────────────────────────────────────

    async def synthesize(self, text: str) -> Optional[bytes]:
        """Text → Kokoro HTTP → raw audio bytes."""
        text = text.strip()
        if not text:
            return None
        try:
            r = await self._http.post(
                KOKORO_URL,
                json={"model": KOKORO_MODEL, "input": text, "voice": KOKORO_VOICE, "speed": 1.1},
            )
            if r.status_code == 200:
                return r.content
            print(f"[Voice] Kokoro {r.status_code}: {r.text[:200]}")
        except Exception as e:
            print(f"[Voice] TTS error: {e}")
        return None

    # ── Skill dispatch ────────────────────────────────────────────────────

    async def dispatch_skill(self, skill: dict, user_text: str) -> Optional[str]:
        """Run skill in executor. Returns result string, or None if empty/failed."""
        try:
            print(f"[Voice] → skill: {skill['name']}")
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, skill["run"], user_text)
            result = str(result).strip() if result else ""
            if not result or result.lower() in ("none", "done, but no output.", ""):
                print(f"[Voice] Skill {skill['name']} returned empty — falling through to Qwen")
                return None
            return result
        except Exception as e:
            print(f"[Voice] Skill dispatch error: {e}")
            return f"There was an error running that: {e}"

    async def _skill_to_speech(self, result: str) -> str:
        """If skill result is long, summarise to 2-3 spoken sentences."""
        if len(result) <= 500:
            return result
        summary_msgs = [
            {"role": "system", "content": "Summarise the following in 2-3 spoken sentences. No formatting."},
            {"role": "user",   "content": result},
        ]
        summary = ""
        async for chunk in self._stream_qwen(summary_msgs, max_tokens=200):
            summary += chunk
        return summary.strip() or result[:400]

    # ── Sentence splitter for TTS streaming ──────────────────────────────

    @staticmethod
    def _flush_on_boundary(buf: str) -> tuple[str, str]:
        """
        Return (to_speak, remainder).
        Flushes when a sentence boundary is found and buffer is long enough.
        """
        if len(buf) < 20:
            return "", buf
        # Look for sentence-ending punctuation
        for i in range(len(buf) - 1, -1, -1):
            if buf[i] in ".!?,;:":
                return buf[:i + 1].strip(), buf[i + 1:]
        return "", buf

    # ── Memory ────────────────────────────────────────────────────────────

    def save_to_memory(self):
        """Write conversation to ~/.q_memory.db (same schema as Pipecat)."""
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("""CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT, timestamp TEXT, role TEXT, content TEXT
            )""")
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
                conn.execute(
                    "INSERT INTO conversations (session_id, timestamp, role, content) VALUES (?,?,?,?)",
                    (self.session_id, datetime.now().isoformat(), role, str(content)[:2000]),
                )
                saved += 1
            conn.commit()
            conn.close()
            print(f"[Voice] Saved {saved} messages → session {self.session_id}")
        except Exception as e:
            print(f"[Voice] Memory save error: {e}")

    # ── Main loop ─────────────────────────────────────────────────────────

    async def run(self):
        print(f"[Voice] Session started: {self.session_id}")

        # Send greeting
        greeting = "Greetings M. Q is online. All systems local. What do you need?"
        greeting_audio = await self.synthesize(greeting)
        if greeting_audio:
            await self.ws.send_bytes(greeting_audio)
        await self.ws.send_json({
            "type": "transcript", "role": "assistant", "text": greeting
        })
        self.messages.append({"role": "assistant", "content": greeting})

        try:
            async for raw_msg in self.ws.iter_bytes():
                # Ignore chunks while we're already generating a response
                if self.processing:
                    continue

                utterance = self.feed_audio(raw_msg)
                if utterance is None:
                    continue

                # ── Got a complete utterance ──
                self.processing = True
                await self.ws.send_json({"type": "status", "status": "processing"})

                # 1. STT
                user_text = await self.transcribe(utterance)
                if not user_text:
                    self.processing = False
                    await self.ws.send_json({"type": "status", "status": "listening"})
                    continue

                print(f"[Voice] User: {user_text}")
                await self.ws.send_json({
                    "type": "transcript", "role": "user", "text": user_text
                })

                # 2. Skill check
                skill_match = self._match_skill(user_text)

                if skill_match:
                    # ── Skill path ──
                    raw_result = await self.dispatch_skill(skill_match, user_text)

                    if raw_result is not None:
                        # Skill returned useful data — summarise and speak it
                        spoken_result = await self._skill_to_speech(raw_result)
                        self.messages.append({"role": "user",      "content": user_text})
                        self.messages.append({"role": "assistant", "content": spoken_result})
                        await self.ws.send_json({
                            "type": "transcript", "role": "assistant", "text": spoken_result
                        })
                        audio = await self.synthesize(spoken_result)
                        if audio:
                            await self.ws.send_bytes(audio)
                            self.last_tts_end = time.monotonic()
                        self.processing = False
                        await self.ws.send_json({"type": "status", "status": "listening"})
                        continue  # skip the LLM path below

                # ── LLM streaming path (also fallback when skill returns nothing) ──
                sentence_buf = ""
                full_text    = ""

                async for token in self.generate_response(user_text):
                    sentence_buf += token
                    full_text    += token

                    to_speak, sentence_buf = self._flush_on_boundary(sentence_buf)
                    if to_speak:
                        audio = await self.synthesize(to_speak)
                        if audio:
                            await self.ws.send_bytes(audio)
                            self.last_tts_end = time.monotonic()
                        await self.ws.send_json({
                            "type": "transcript_chunk", "text": to_speak
                        })

                # Flush remainder
                if sentence_buf.strip():
                    audio = await self.synthesize(sentence_buf.strip())
                    if audio:
                        await self.ws.send_bytes(audio)
                        self.last_tts_end = time.monotonic()
                    await self.ws.send_json({
                        "type": "transcript_chunk", "text": sentence_buf
                    })

                await self.ws.send_json({
                    "type": "transcript", "role": "assistant", "text": full_text.strip()
                })

                self.processing = False
                await self.ws.send_json({"type": "status", "status": "listening"})

        except Exception as e:
            print(f"[Voice] Session error: {type(e).__name__}: {e}")
        finally:
            self.save_to_memory()
            print(f"[Voice] Session ended: {self.session_id}")

    async def close(self):
        try:
            await self._http.aclose()
        except Exception:
            pass
