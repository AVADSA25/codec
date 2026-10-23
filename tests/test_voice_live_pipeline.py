"""Voice 02 in the live call (codec_voice.py with codec_voice_speech.py, docs/CODEC-VOICE-02-LIVE-DESIGN.md).

The pipeline runs for real here (typed turns, so no speech to text); the model, the clone stream and the Kokoro synth are fakes, and
the socket records everything it is sent in order. The rules: without "live" in tts_clone.enabled_for every frame and message is
what it was before; with it the model is not held up by speech, the text of a sentence goes out just before its audio, an interrupt
stops the stream and tells the page once, and any trouble falls back to the Kokoro blend.
"""
from __future__ import annotations

import asyncio
import json
import math
import struct
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import codec_tts  # noqa: E402

CLONE_MODEL = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16"
WAV = b"RIFF" + b"\x24\x00\x00\x00" + b"WAVE" + b"\x00" * 200
SECOND = b"\x01\x00" * 24000
KOKORO = b"ID3-kokoro"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    (hub / ("models--" + CLONE_MODEL.replace("/", "--")) / "snapshots" / "abc").mkdir(parents=True)
    monkeypatch.setenv("HF_HUB_CACHE", str(hub))
    ref = tmp_path / "reference.wav"
    ref.write_bytes(WAV)
    words = tmp_path / "reference.txt"
    words.write_text("Good evening. This is the reference.\n")
    cfg = {"tts_clone": {"enabled_for": ["speak"], "model": CLONE_MODEL, "ref_audio": str(ref), "ref_text_file": str(words)}}
    state = {"cfg": cfg}
    monkeypatch.setattr(codec_tts, "load_config", lambda: state["cfg"])
    monkeypatch.setattr(codec_tts, "note_fallback", lambda purpose, reason: state.setdefault("notes", []).append((purpose, reason)))
    return state


def live_on(state, **live):
    state["cfg"]["tts_clone"]["enabled_for"] = ["speak", "live"]
    if live:
        state["cfg"]["tts_clone"]["live"] = live


class FakeTTS:
    """Stands in for codec_tts.astream_clone. script(text) -> steps: bytes (yield), number (sleep), Exception (raise)."""

    def __init__(self, script=None):
        self.requests = []
        self.script = script or (lambda text: [SECOND])

    async def __call__(self, http, url, text, live):
        self.requests.append(text)
        for step in self.script(text):
            if isinstance(step, Exception):
                raise step
            if isinstance(step, (int, float)):
                await asyncio.sleep(step)
            else:
                yield step


class Call:
    """A VoicePipeline with a recording socket, a fake model and the fake clone."""

    def __init__(self, state, monkeypatch, tts=None, tokens=("Hello there. ", "How are you?")):
        ws = AsyncMock()
        self.events = []

        async def send_json(obj):
            self.events.append(("json", obj))

        async def send_bytes(data):
            self.events.append(("bytes", data))
        ws.send_json, ws.send_bytes = send_json, send_bytes
        self.ws = ws
        self.tts = tts or FakeTTS()
        monkeypatch.setattr(codec_tts, "astream_clone", self.tts)
        with patch("codec_voice.VoicePipeline._load_skills"), patch("codec_voice._build_system_prompt", return_value="sys"):
            from codec_voice import VoicePipeline
            self.p = VoicePipeline(ws)
        p = self.p
        p.skills, p.mode, p._awaiting_ask_user, p._disconnect_reason = {}, "default", None, "user"
        p._poll_pending_question_for_voice = AsyncMock(return_value=None)
        p.synthesize = AsyncMock(return_value=KOKORO)
        self.llm_done_at = None
        self.started = time.monotonic()
        self.tokens = list(tokens)
        p.generate_response = self._generate

    async def _generate(self, user_text):
        for t in self.tokens:
            if isinstance(t, Exception):
                raise t
            yield t
        self.llm_done_at = time.monotonic() - self.started

    async def turn(self, text="what is on today?"):
        await self.p.utterance_queue.put(text)
        await self.p.utterance_queue.put(None)
        await asyncio.wait_for(self.p._pipeline(), 30)

    def kinds(self):
        return [(k, (d.get("type") if k == "json" else ("pcm" if d[:4] == b"CPCM" else "file"))) for k, d in self.events]

    def frames(self, kind):
        return [d for k, d in self.events if k == "bytes" and ((d[:4] == b"CPCM") == (kind == "pcm"))]

    def jsons(self, type_):
        return [d for k, d in self.events if k == "json" and d.get("type") == type_]


# ── nothing changes without "live" ──────────────────────────────────────────

def test_without_live_the_old_path_is_byte_for_byte_the_same(isolated, monkeypatch):
    async def go():
        c = Call(isolated, monkeypatch)
        await c.turn()
        return c
    c = asyncio.run(go())
    assert c.p._live_speech() is None and c.tts.requests == []
    after_hint = c.events[2:]
    assert after_hint == [("json", {"type": "transcript_chunk", "text": "Hello there."}), ("bytes", KOKORO),
                          ("json", {"type": "transcript_chunk", "text": " How are you?"}), ("bytes", KOKORO),
                          ("json", {"type": "transcript", "role": "assistant", "text": "Hello there. How are you?"}),
                          ("json", {"type": "status", "status": "listening"})]
    assert c.p.synthesize.await_args_list[0].args == ("Hello there.",) and c.p.synthesize.await_args_list[1].args == ("How are you?",)


def test_without_live_greeting_and_crew_lines_are_still_one_file_each(isolated, monkeypatch):
    async def go():
        c = Call(isolated, monkeypatch)
        await c.p._speak_outside_a_turn("Greetings.", greeting=True)
        g_end = c.p.last_tts_end
        await c.p._speak_outside_a_turn("Starting a crew.")
        return c, g_end
    c, g_end = asyncio.run(go())
    assert c.frames("file") == [KOKORO, KOKORO] and c.frames("pcm") == []
    assert g_end <= time.monotonic() and c.p.last_tts_end > time.monotonic()      # a greeting marks "now", a line marks the end of playback


def test_without_live_an_interrupt_message_only_gets_the_audio_reset_answer(isolated, monkeypatch):
    async def go():
        c = Call(isolated, monkeypatch)
        c.ws.receive = AsyncMock(side_effect=[{"type": "websocket.receive", "text": json.dumps({"type": "interrupt"})},
                                              {"type": "websocket.disconnect"}])
        await c.p._audio_receiver()
        return c
    c = asyncio.run(go())
    assert c.jsons("audio_reset") == [{"type": "audio_reset"}] and c.p._speech is None and not c.p.interrupted.is_set()


def test_without_live_loud_audio_while_speaking_still_sets_the_flag_and_sends_no_reset(isolated, monkeypatch):
    async def go():
        c = Call(isolated, monkeypatch)
        c.p.processing, c.p.last_tts_end = True, 0.0
        loud = struct.pack("<4096h", *[int(12000 * math.sin(i / 3)) for i in range(4096)])
        c.ws.receive = AsyncMock(side_effect=[{"type": "websocket.receive", "bytes": loud},
                                              {"type": "websocket.receive", "bytes": loud},
                                              {"type": "websocket.disconnect"}])
        await c.p._audio_receiver()
        return c
    c = asyncio.run(go())
    assert c.p.interrupted.is_set() and c.jsons("audio_reset") == []


# ── with "live" ─────────────────────────────────────────────────────────────

def test_with_live_a_reply_is_streamed_and_its_text_comes_first(isolated, monkeypatch):
    live_on(isolated)

    async def go():
        c = Call(isolated, monkeypatch)
        await c.turn()
        return c
    c = asyncio.run(go())
    kinds = c.kinds()
    first_pcm = kinds.index(("bytes", "pcm"))
    assert ("json", "transcript_chunk") in kinds[:first_pcm], "the text of the sentences goes out before their audio"
    assert kinds[-2:] == [("json", "transcript"), ("json", "status")]
    assert all(k != ("bytes", "file") for k in kinds)
    assert c.tts.requests == ["Hello there. How are you?"], "the two waiting sentences are one request"
    assert c.jsons("audio_reset") == [], "a normal turn does not reset the page"
    assert not c.p.processing


def test_the_model_is_not_held_up_by_slow_speech(isolated, monkeypatch):
    live_on(isolated, chunk_chars=40)

    async def go():
        tts = FakeTTS(lambda text: [0.3, SECOND])
        c = Call(isolated, monkeypatch, tts=tts, tokens=("One sentence is here. ", "Another sentence follows it. ",
                                                         "And a third one ends the reply."))
        t0 = time.monotonic()
        await c.turn()
        return c, time.monotonic() - t0
    c, total = asyncio.run(go())
    assert c.llm_done_at < 0.2, "the model finished while the first sentence was still being made"
    assert total > 0.5 and len(c.tts.requests) >= 2


def test_the_final_text_comes_after_the_last_frame_and_the_status_after_that(isolated, monkeypatch):
    live_on(isolated, chunk_chars=40)

    async def go():
        tts = FakeTTS(lambda text: [0.05, SECOND])
        c = Call(isolated, monkeypatch, tts=tts, tokens=("First sentence goes here. ", "Second sentence goes here."))
        await c.turn()
        return c
    kinds = asyncio.run(go()).kinds()
    last_pcm = max(i for i, k in enumerate(kinds) if k == ("bytes", "pcm"))
    assert kinds.index(("json", "transcript")) > last_pcm and kinds[-1] == ("json", "status")


def test_a_typed_turn_and_a_long_skill_result_use_the_same_worker(isolated, monkeypatch):
    live_on(isolated, chunk_chars=80)
    text = ("The weekly report is ready, and sales are up four percent on last week. The breakfast service ran two minutes "
            "faster. Two suppliers sent late invoices and one delivery is still missing, so I can draft the reminders.")

    async def go():
        c = Call(isolated, monkeypatch)
        ok = await c.p._speak(text)
        return c, ok
    c, ok = asyncio.run(go())
    assert ok is True and len(c.tts.requests) > 1 and all(len(r) <= 80 for r in c.tts.requests)
    assert " ".join(c.tts.requests).split() == text.split()


def test_speak_refuses_while_interrupted_and_the_worker_stays_quiet(isolated, monkeypatch):
    live_on(isolated)

    async def go():
        c = Call(isolated, monkeypatch)
        c.p.interrupted.set()
        return c, await c.p._speak("Hello there.")
    c, ok = asyncio.run(go())
    assert ok is False and c.tts.requests == [] and c.frames("pcm") == []


def test_greeting_and_crew_lines_are_queued_for_the_worker(isolated, monkeypatch):
    live_on(isolated)

    async def go():
        c = Call(isolated, monkeypatch)
        await c.p._speak_outside_a_turn("Greetings. CODEC is online.", greeting=True)
        await c.p._speak_outside_a_turn("Starting a crew.")
        await c.p._speech_drain()
        return c
    c = asyncio.run(go())
    assert c.tts.requests == ["Greetings. CODEC is online. Starting a crew."] or c.tts.requests == ["Greetings. CODEC is online.", "Starting a crew."]
    assert c.frames("file") == [] and len(c.frames("pcm")) >= 1


def test_the_echo_guard_covers_the_whole_streamed_reply(isolated, monkeypatch):
    live_on(isolated)

    async def go():
        tts = FakeTTS(lambda text: [SECOND, SECOND])
        c = Call(isolated, monkeypatch, tts=tts)
        t0 = time.monotonic()
        await c.p._speak("Hello there.")
        return c, t0
    c, t0 = asyncio.run(go())
    import codec_voice
    assert c.p.last_tts_end >= t0 + 2.0 + codec_voice.TTS_BROWSER_DECODE_LAG - 0.05, "two seconds of speech plus the browser lag"


def test_a_kokoro_file_after_streamed_speech_extends_the_guard_instead_of_replacing_it(isolated, monkeypatch):
    async def go():
        c = Call(isolated, monkeypatch)
        c.p._extend_tts_end(5.0)
        before = c.p.last_tts_end
        c.p._note_file_sent(b"x" * 8000)        # one second of the old MP3 size assumption
        return before, c.p.last_tts_end
    before, after = asyncio.run(go())
    assert abs((after - before) - 1.0) < 0.01


def test_trouble_with_the_clone_is_spoken_by_kokoro_inside_the_call(isolated, monkeypatch):
    live_on(isolated)

    async def go():
        tts = FakeTTS(lambda text: [codec_tts.CloneUnavailable("http_500")])
        c = Call(isolated, monkeypatch, tts=tts)
        await c.turn()
        return c
    c = asyncio.run(go())
    assert c.frames("pcm") == [] and c.frames("file") == [KOKORO]
    assert isolated["notes"] == [("live", "http_500")]
    assert c.jsons("transcript")[-1]["text"] == "Hello there. How are you?"


def test_a_worker_that_cannot_be_made_leaves_the_old_path(isolated, monkeypatch):
    live_on(isolated)

    async def go():
        c = Call(isolated, monkeypatch)
        with patch("codec_voice_speech.LiveSpeech", side_effect=RuntimeError("no worker")):
            await c.turn()
        return c
    c = asyncio.run(go())
    assert c.p._speech is None and c.frames("file") == [KOKORO, KOKORO] and c.frames("pcm") == []


def test_closing_the_call_closes_the_worker(isolated, monkeypatch):
    live_on(isolated)

    async def go():
        c = Call(isolated, monkeypatch)
        await c.p._speak("Hello there.")
        task = c.p._speech._task
        await c.p.close()
        return task.done(), c.p._speech.say_nowait("More.")
    done, again = asyncio.run(go())
    assert done is True and again is False


# ── interrupting ────────────────────────────────────────────────────────────

def test_an_interrupt_by_loud_audio_stops_the_stream_and_the_page_hears_about_it_once(isolated, monkeypatch):
    live_on(isolated)

    async def go():
        tts = FakeTTS(lambda text: [SECOND, 5.0, SECOND])
        c = Call(isolated, monkeypatch, tts=tts)
        turn = asyncio.ensure_future(c.turn())
        for _ in range(100):
            await asyncio.sleep(0.02)
            if c.frames("pcm"):
                break
        c.p.processing, c.p.last_tts_end = True, 0.0
        loud = struct.pack("<4096h", *[int(12000 * math.sin(i / 3)) for i in range(4096)])
        c.ws.receive = AsyncMock(side_effect=[{"type": "websocket.receive", "bytes": loud}] * 3 + [{"type": "websocket.disconnect"}])
        await c.p._audio_receiver()
        await asyncio.wait_for(turn, 10)
        return c
    c = asyncio.run(go())
    assert len(c.frames("pcm")) == 1, "nothing was sent after the interrupt"
    assert c.jsons("audio_reset") == [{"type": "audio_reset"}], "three loud frames, one reset"
    assert c.jsons("transcript")[-1]["text"].endswith("[interrupted]")


def test_an_interrupt_message_stops_the_stream_and_is_answered_once(isolated, monkeypatch):
    live_on(isolated)

    async def go():
        tts = FakeTTS(lambda text: [SECOND, 5.0, SECOND])
        c = Call(isolated, monkeypatch, tts=tts)
        turn = asyncio.ensure_future(c.turn())
        for _ in range(100):
            await asyncio.sleep(0.02)
            if c.frames("pcm"):
                break
        c.p.processing = True
        c.ws.receive = AsyncMock(side_effect=[{"type": "websocket.receive", "text": json.dumps({"type": "interrupt"})},
                                              {"type": "websocket.disconnect"}])
        await c.p._audio_receiver()
        await asyncio.wait_for(turn, 10)
        return c
    c = asyncio.run(go())
    assert len(c.frames("pcm")) == 1 and c.jsons("audio_reset") == [{"type": "audio_reset"}]


def test_an_interrupt_message_when_nothing_is_speaking_is_still_answered(isolated, monkeypatch):
    live_on(isolated)

    async def go():
        c = Call(isolated, monkeypatch)
        c.ws.receive = AsyncMock(side_effect=[{"type": "websocket.receive", "text": json.dumps({"type": "interrupt"})},
                                              {"type": "websocket.disconnect"}])
        await c.p._audio_receiver()
        return c
    c = asyncio.run(go())
    assert c.jsons("audio_reset") == [{"type": "audio_reset"}] and not c.p.interrupted.is_set()


def test_after_an_interrupt_the_next_turn_is_spoken(isolated, monkeypatch):
    live_on(isolated)

    async def go():
        script = {"first": [SECOND, 5.0, SECOND]}
        tts = FakeTTS(lambda text: script["first"] if text.startswith("Hello") else [SECOND])
        c = Call(isolated, monkeypatch, tts=tts)
        turn = asyncio.ensure_future(c.turn())
        for _ in range(100):
            await asyncio.sleep(0.02)
            if c.frames("pcm"):
                break
        c.p.processing = True
        c.p.interrupted.set()
        await c.p._speech_interrupted(page_asked=True)
        await asyncio.wait_for(turn, 10)
        c.tokens = ["Fine, thanks."]
        c.p.interrupted.clear()
        await c.turn("and now?")
        return c
    c = asyncio.run(go())
    assert any(r.startswith("Fine") for r in c.tts.requests) and len(c.frames("pcm")) == 2


def test_an_error_in_the_model_leaves_nothing_queued_for_the_next_turn(isolated, monkeypatch):
    live_on(isolated)

    async def go():
        tts = FakeTTS(lambda text: [0.2, SECOND])
        c = Call(isolated, monkeypatch, tts=tts, tokens=("Hello there. ", RuntimeError("model fell over")))
        await c.turn()
        return c
    c = asyncio.run(go())
    assert c.p._speech._queue.__len__() == 0 and not c.p.processing and not c.p.interrupted.is_set()
    assert c.jsons("audio_reset") == [], "an error does not reset the page"
