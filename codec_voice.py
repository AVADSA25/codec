"""
CODEC Voice v2 — voice-to-voice pipeline with interruption support.
WebSocket receives PCM16 audio + JSON control messages from browser.
Two concurrent tasks: audio receiver + pipeline processor.
Interruption: user speaking mid-response cancels TTS immediately.
"""
import asyncio
import io
import json
import os
import re
import sys
import time
import wave
from datetime import datetime
from typing import Optional

import httpx
import numpy as np

# ── CONFIG — loaded from ~/.codec/config.json ─────────────────────────────
WHISPER_URL   = "http://localhost:8084/v1/audio/transcriptions"
WHISPER_MODEL = "mlx-community/whisper-large-v3-turbo"
QWEN_URL      = "http://localhost:8081/v1/chat/completions"
QWEN_MODEL    = "mlx-community/Qwen3.5-35B-A3B-4bit"
LLM_KWARGS    = {}
KOKORO_URL    = "http://localhost:8085/v1/audio/speech"
KOKORO_MODEL  = "mlx-community/Kokoro-82M-bf16"
KOKORO_VOICE  = "am_adam"
SKILLS_DIR    = os.path.expanduser("~/.codec/skills")

_CONFIG_PATH = os.path.expanduser("~/.codec/config.json")
try:
    with open(_CONFIG_PATH) as _f:
        _cfg = json.load(_f)
    _llm_base     = _cfg.get("llm_base_url", "http://localhost:8081/v1").rstrip("/")
    QWEN_URL      = _llm_base + "/chat/completions"
    QWEN_MODEL    = _cfg.get("llm_model", QWEN_MODEL)
    LLM_KWARGS    = {k: v for k, v in _cfg.get("llm_kwargs", {}).items() if k != "enable_thinking"}
    KOKORO_URL    = _cfg.get("tts_url",   KOKORO_URL)
    KOKORO_MODEL  = _cfg.get("tts_model", KOKORO_MODEL)
    KOKORO_VOICE  = _cfg.get("tts_voice", KOKORO_VOICE)
    WHISPER_URL   = _cfg.get("stt_url",   WHISPER_URL)
    WHISPER_MODEL = _cfg.get("stt_model", WHISPER_MODEL)
except Exception as _e:
    print(f"[Voice] Config load warning: {_e} — using defaults")

# ── VAD ───────────────────────────────────────────────────────────────────
VAD_SILENCE_THRESHOLD  = 800    # RMS below this = silence
VAD_SILENCE_DURATION   = 1.5   # seconds of silence before flushing (was 2.2 — main latency)
VAD_MIN_SPEECH_SECONDS = 0.4   # minimum speech before considering a flush (was 0.6)
VAD_ECHO_COOLDOWN      = 1.2   # ignore mic this long after Q finishes speaking
SAMPLE_RATE            = 16000
BYTES_PER_SAMPLE       = 2
MIN_SPEECH_BYTES       = int(SAMPLE_RATE * BYTES_PER_SAMPLE * VAD_MIN_SPEECH_SECONDS)

# RMS threshold for interrupt detection (slightly lower than VAD to catch early speech)
INTERRUPT_THRESHOLD = 600

# ── Whisper noise filter ──────────────────────────────────────────────────
NOISE_WORDS = {
    "you", "thank you", "thanks", "thanks for watching", "bye", "goodbye",
    "see you", "see you next time", "please subscribe", "like and subscribe",
    "", "hmm", "uh", "oh", "hm", "um", "yeah", "yep", "mm", "mhm",
    "okay", "ok", "right", "sure", "yes", "no", "hey", "hi", "hello",
    "so", "well", "um hmm", "uh huh", "ah", "er",
}

# Max conversation turns to keep in context (prevents bloat → keeps LLM fast)
MAX_CONTEXT_TURNS = 20

# ── System Prompt ─────────────────────────────────────────────────────────
def _build_system_prompt() -> str:
    import datetime as _dt
    now = _dt.datetime.now()
    days = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    date_str = now.strftime(f"{days[now.weekday()]}, %-d %B %Y")
    time_str = now.strftime("%-I:%M %p")
    return f"""You are Q — CODEC Voice, a JARVIS-class local AI running on a Mac Studio M1 Ultra.
The user is M. Fully local. No cloud. No external logs.

CURRENT DATE AND TIME: {date_str}, {time_str} (Madrid / Europe time)
Use this to correctly interpret "today", "tomorrow", "this afternoon", etc.

━━ VOICE OUTPUT RULES ━━
Your responses go directly to speech via Kokoro TTS. Format for ears only:
- NO markdown: no asterisks, no hashtags, no bullets, no tables, no dashes
- NO special characters, symbols, or URLs
- SHORT answers: 1-3 sentences for casual questions, expand only when M explicitly asks
- Start with a natural spoken opener: "Right,", "Sure.", "Got it.", "Done.", "So,"
- Speak like a sharp calm person, not a chatbot
- For simple factual answers (math, date, name): give just the answer, nothing else

━━ INPUT HANDLING ━━
Input is live voice transcription (Whisper STT). Expect noise:
- "iq", "hey q", "hey mike", "hey codec" at start = M calling your name — ignore it
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
NEVER delegate to Lucy or any other agent.

━━ ANTI-HALLUCINATION ━━
- Skill returns "Done. [X] added" → confirm done
- Skill returns "No events" → that's a READ result, NOT creation confirmation
- Skill returns error → report honestly, offer to retry
- Unsure → say "Let me check" and report actual result

━━ MEMORY ━━
All sessions are saved to CODEC shared memory (FTS5 indexed).
If M asks to remember something: confirm "Saved to memory."

━━ PERSONA ━━
Honest. Direct. Dry wit at 10 percent. Commanding presence.
Straight answers. One well-placed sarcastic remark per conversation maximum.
M's right hand — not a customer service bot."""


# ─────────────────────────────────────────────────────────────────────────────
class VoicePipeline:
    """One voice session per WebSocket connection. Two-task architecture."""

    def __init__(self, websocket):
        self.ws              = websocket
        self.session_id      = "voice_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        self.messages        = [{"role": "system", "content": _build_system_prompt()}]

        # VAD state
        self.audio_buffer     = bytearray()
        self.last_speech_time = 0.0
        self.is_speaking      = False
        self.last_tts_end     = 0.0

        # Concurrency
        self.utterance_queue = asyncio.Queue(maxsize=3)   # completed utterances ready to process
        self.interrupted     = asyncio.Event()   # set when user speaks mid-response
        self.processing      = False             # True while generating/speaking a response

        self.skills = {}
        self._http  = httpx.AsyncClient(timeout=120.0)
        self._warmed_up = False
        self._load_skills()

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
        print(f"[Voice] {len(self.skills)} skills registered (lazy)")

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
                print("[Voice] Warmup: memory context injected into system prompt")
        except Exception as e:
            print(f"[Voice] Warmup error: {e}")

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

    @staticmethod
    def _rms(chunk: bytes) -> float:
        if len(chunk) < 2:
            return 0.0
        samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
        return float(np.sqrt(np.mean(samples ** 2)))

    def feed_audio(self, chunk: bytes) -> Optional[bytes]:
        rms = self._rms(chunk)
        now = time.monotonic()

        # Echo cooldown: ignore mic after Q speaks
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
                    print(f"[Voice] Discarded noise: '{text}'")
                    return ""
                words = [w for w in clean.split() if w not in {"uh","um","er","hmm","ah"}]
                if len(words) < 2:
                    print(f"[Voice] Discarded too short: '{text}'")
                    return ""
                try:
                    _dash = os.path.dirname(os.path.abspath(__file__))
                    if _dash not in sys.path:
                        sys.path.insert(0, _dash)
                    from codec_config import clean_transcript as _clean
                    text = _clean(text) or text
                except Exception as e:
                    print(f"[Voice] Transcript clean warning: {e}")
                return text
            print(f"[Voice] Whisper {r.status_code}: {r.text[:200]}")
        except Exception as e:
            print(f"[Voice] Whisper error: {e}")
        return ""

    # ── LLM ───────────────────────────────────────────────────────────────

    def _trimmed_messages(self) -> list:
        """Keep system prompt + last MAX_CONTEXT_TURNS message pairs."""
        system = [m for m in self.messages if m["role"] == "system"]
        convo  = [m for m in self.messages if m["role"] != "system"]
        # Each turn = 2 messages (user + assistant)
        max_msgs = MAX_CONTEXT_TURNS * 2
        return system + convo[-max_msgs:]

    async def _stream_qwen(self, messages: list, max_tokens: int = 300):
        payload = {
            "model": QWEN_MODEL,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.7,
            "top_p": 0.9,
            "frequency_penalty": 0.8,
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
                            token = re.sub(r"<think>[\s\S]*?</think>", "", token)
                            if token:
                                yield token
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
        except Exception as e:
            print(f"[Voice] Qwen error: {e}")
            yield "Sorry, I had a processing error."

    async def generate_response(self, user_text: str):
        self.messages.append({"role": "user", "content": user_text})
        self._warmed_up = False  # reset so next speech start can warm up again
        full = ""
        async for chunk in self._stream_qwen(self._trimmed_messages()):
            full += chunk
            yield chunk
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
            print(f"[Voice] Kokoro {r.status_code}: {r.text[:200]}")
        except Exception as e:
            print(f"[Voice] TTS error: {e}")
        return None

    # ── Sentence boundary ─────────────────────────────────────────────────

    def _flush_on_boundary(self, buf: str) -> tuple[str, str]:
        """Return (to_speak, remainder) at first sentence boundary.
        Avoids splitting on abbreviations (Dr., Mr., etc.), decimals (3.14), times (10:30)."""
        import re
        # Match sentence-ending punctuation NOT preceded by common abbreviations
        # and NOT followed by a digit (decimals, times)
        m = re.search(r'(?<!\b(?:Dr|Mr|Mrs|Ms|Jr|Sr|St|vs|etc|approx|incl|govt))[.!?]\s', buf)
        if m:
            end = m.end() - 1  # keep the space in remainder
            return buf[:end].strip(), buf[end:]
        return "", buf

    # ── TTS with interruption check ───────────────────────────────────────

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
            await self.ws.send_bytes(audio)
            self.last_tts_end = time.monotonic()
        return True

    # ── Skill dispatch ────────────────────────────────────────────────────

    async def dispatch_skill(self, skill: dict, user_text: str) -> Optional[str]:
        try:
            print(f"[Voice] → skill: {skill['name']}")
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, skill["run"], user_text)
            result = str(result).strip() if result else ""
            if not result or result.lower() in ("none", "done, but no output.", ""):
                print(f"[Voice] Skill {skill['name']} empty — falling through to Qwen")
                return None
            return result
        except Exception as e:
            print(f"[Voice] Skill error: {e}")
            return f"There was an error running that: {e}"

    async def _skill_to_speech(self, result: str) -> str:
        if len(result) <= 500:
            return result
        summary_msgs = [
            {"role": "system", "content": "Summarise in 1-2 spoken sentences. No formatting."},
            {"role": "user",   "content": result},
        ]
        summary = ""
        async for chunk in self._stream_qwen(summary_msgs, max_tokens=150):
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
                await self.ws.send_json({"type": "transcript", "role": "assistant", "text": notify})
                audio = await self.synthesize(notify)
                if audio:
                    await self.ws.send_bytes(audio)
                    self.last_tts_end = time.monotonic()

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
                    await self.ws.send_json({"type": "transcript", "role": "assistant", "text": msg})
                    a = await self.synthesize(msg)
                    if a:
                        await self.ws.send_bytes(a)
                        self.last_tts_end = time.monotonic()

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
            print(f"[Voice] Saved {saved} messages → {self.session_id}")
        except Exception as e:
            print(f"[Voice] Memory save error: {e}")

    # ── Audio receiver task ────────────────────────────────────────────────

    async def _audio_receiver(self):
        """
        Continuously reads WebSocket messages (bytes = audio, text = control).
        - Bytes: feeds VAD; queues complete utterances for processing.
        - Text {"type":"interrupt"}: sets self.interrupted to stop active TTS.
        - Text {"type":"ping"}: silently ignored.
        """
        try:
            while True:
                msg = await self.ws.receive()
                msg_type = msg.get("type", "")

                if msg_type == "websocket.disconnect":
                    print("[Voice] WebSocket disconnected in receiver")
                    await self.utterance_queue.put(None)  # signal pipeline to stop
                    break

                # ── Text / JSON control message ──
                raw_text = msg.get("text")
                if raw_text:
                    try:
                        ctrl = json.loads(raw_text)
                        ctrl_type = ctrl.get("type", "")

                        if ctrl_type == "interrupt":
                            if self.processing:
                                print("[Voice] Interrupt received")
                                self.interrupted.set()

                        elif ctrl_type == "your_turn":
                            # Force-flush audio buffer as an utterance (user says "your turn")
                            if self.audio_buffer and len(self.audio_buffer) >= MIN_SPEECH_BYTES:
                                utterance = bytes(self.audio_buffer)
                                self.audio_buffer = bytearray()
                                self.is_speaking = False
                                print(f"[Voice] Your-turn: flushing {len(utterance)} bytes")
                                await self.utterance_queue.put(utterance)
                            elif self.audio_buffer:
                                # Buffer too short — discard and notify
                                self.audio_buffer = bytearray()
                                self.is_speaking = False
                                await self.ws.send_json({"type": "status", "status": "listening"})
                                print("[Voice] Your-turn: buffer too short, discarded")
                            else:
                                print("[Voice] Your-turn: no audio buffered")

                        elif ctrl_type == "nudge":
                            # User tapped "still there?" — send reassurance
                            if self.processing:
                                await self.ws.send_json({"type": "transcript", "role": "system", "text": "Still processing — hang on…"})
                                print("[Voice] Nudge acknowledged — still processing")

                        elif ctrl_type == "hold_start":
                            # User started hold-to-talk — ensure we're in listening mode
                            print("[Voice] Hold-to-talk started")

                    except Exception as e:
                        print(f"[Voice] WS text parse warning: {e}")
                    continue

                # ── Audio bytes ──
                raw_bytes = msg.get("bytes")
                if not raw_bytes:
                    continue

                # While processing: check if user starts talking → interrupt
                if self.processing:
                    rms = self._rms(raw_bytes)
                    if (rms > INTERRUPT_THRESHOLD and
                            time.monotonic() - self.last_tts_end > VAD_ECHO_COOLDOWN):
                        print(f"[Voice] Interrupt by audio energy (RMS {rms:.0f})")
                        self.interrupted.set()
                    continue

                # Normal VAD feeding — trigger warmup on speech start
                was_speaking = self.is_speaking
                utterance = self.feed_audio(raw_bytes)
                if self.is_speaking and not was_speaking and not self._warmed_up:
                    asyncio.create_task(self.warmup_llm())
                if utterance:
                    await self.utterance_queue.put(utterance)

        except Exception as e:
            print(f"[Voice] Receiver error: {type(e).__name__}: {e}")
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

            try:
                # 1. STT
                user_text = await self.transcribe(utterance)
                if not user_text:
                    self.processing = False
                    await self.ws.send_json({"type": "status", "status": "listening"})
                    continue

                print(f"[Voice] User: {user_text}")
                await self.ws.send_json({"type": "transcript", "role": "user", "text": user_text})

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
                    print("[Voice] Response interrupted by user")

            except Exception as e:
                print(f"[Voice] Pipeline error: {type(e).__name__}: {e}")

            finally:
                self.interrupted.clear()
                self.processing = False
                await self.ws.send_json({"type": "status", "status": "listening"})

    # ── Main entry point ──────────────────────────────────────────────────

    async def run(self):
        print(f"[Voice] Session started: {self.session_id}")

        # Greeting
        greeting = "Greetings M. Q is online. All systems local. What do you need?"
        self.messages.append({"role": "assistant", "content": greeting})
        await self.ws.send_json({"type": "transcript", "role": "assistant", "text": greeting})
        g_audio = await self.synthesize(greeting)
        if g_audio:
            await self.ws.send_bytes(g_audio)
            self.last_tts_end = time.monotonic()

        # Run both tasks concurrently — receiver feeds queue, pipeline processes
        receiver = asyncio.create_task(self._audio_receiver())
        pipeline = asyncio.create_task(self._pipeline())

        try:
            await asyncio.gather(receiver, pipeline)
        except Exception as e:
            print(f"[Voice] Session error: {type(e).__name__}: {e}")
        finally:
            receiver.cancel()
            pipeline.cancel()
            self.save_to_memory()
            print(f"[Voice] Session ended: {self.session_id}")

    async def close(self):
        try:
            await self._http.aclose()
        except Exception as e:
            print(f"[Voice] HTTP client close warning: {e}")
