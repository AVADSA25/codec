"""The live voice's speaking worker (codec_voice_speech.py, docs/CODEC-VOICE-02-LIVE-DESIGN.md).

No server, no socket: the stream, the Kokoro synth and the WebSocket sends are fakes. The rules: one piece at a time and in order,
waiting pieces merged up to the limit, the clone streamed as CPCM frames, Kokoro for a piece the clone could not start, two failures
in a row turn the clone off, cancel closes the stream and tells the page once, nothing after an interrupt or a dead socket, and no
text in anything written for the log.
"""
from __future__ import annotations

import asyncio
import struct
import time

import pytest

import codec_tts
import codec_voice_speech as vs

RATE = 24000
LIVE = {"model": "m", "ref_audio": "/r.wav", "ref_text": "words", "lang_code": "auto", "max_chars": 400, "rate": RATE,
        "interval": 0.5, "first_byte_timeout": 8.0, "idle_timeout": 6.0, "chunk_chars": 60, "breaker_failures": 2}
SECOND = b"\x01\x00" * RATE            # one second of PCM


def run(coro):
    return asyncio.run(coro)


class Rig:
    """A LiveSpeech with fakes. `script(text)` returns the steps of the fake stream for that text:
    bytes -> yield it, a number -> sleep that long, an Exception -> raise it."""

    def __init__(self, script=None, kokoro=b"ID3-kokoro", live=None, send_ok=True):
        self.script = script or (lambda text: [SECOND])
        self.requests, self.closed, self.frames, self.jsons = [], [], [], []
        self.pcm_seconds, self.files, self.kokoro_calls = [], [], []
        self.running, self.max_running = 0, 0
        self.send_ok = send_ok
        self.interrupted = asyncio.Event()
        self._kokoro = kokoro
        self.notes = []
        self.ls = vs.LiveSpeech(dict(live or LIVE), http=object(), url="http://x/v1/audio/speech", send_bytes=self.send_bytes,
                                send_json=self.send_json, kokoro=self.kokoro, interrupted=self.interrupted,
                                on_pcm=self.pcm_seconds.append, on_file=self.files.append, stream=self.stream)

    async def send_bytes(self, data):
        if not self.send_ok:
            return False
        self.frames.append(data)
        return True

    async def send_json(self, obj):
        self.jsons.append(obj)
        return True

    async def kokoro(self, text):
        self.kokoro_calls.append(text)
        return self._kokoro

    async def stream(self, http, url, text, live):
        self.requests.append(text)
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        try:
            for step in self.script(text):
                if isinstance(step, Exception):
                    raise step
                if isinstance(step, (int, float)):
                    await asyncio.sleep(step)
                else:
                    yield step
        finally:
            self.running -= 1
            self.closed.append(text)


@pytest.fixture(autouse=True)
def notes(monkeypatch):
    seen = []
    monkeypatch.setattr(codec_tts, "note_fallback", lambda purpose, reason: seen.append((purpose, reason)))
    return seen


def pcm_frames(rig):
    return [f for f in rig.frames if f[:4] == b"CPCM"]


# ── the frame ───────────────────────────────────────────────────────────────

def test_a_pcm_frame_is_the_magic_the_rate_and_the_samples():
    assert vs.pcm_frame(b"\x01\x00\x02\x00", 24000) == b"CPCM" + struct.pack("<I", 24000) + b"\x01\x00\x02\x00"


# ── speaking ────────────────────────────────────────────────────────────────

def test_a_sentence_is_streamed_as_pcm_frames_in_order():
    async def go():
        rig = Rig(lambda t: [b"\x01\x00" * 100, b"\x02\x00" * 100, b"\x03\x00" * 100])
        assert await rig.ls.say("Your note is saved.") is True
        return rig
    rig = run(go())
    assert [f[8:10] for f in rig.frames] == [b"\x01\x00", b"\x02\x00", b"\x03\x00"]
    assert all(f[:4] == b"CPCM" and struct.unpack("<I", f[4:8])[0] == RATE for f in rig.frames)
    assert rig.requests == ["Your note is saved."] and rig.ls.stats["clone"] == 1 and not rig.files


def test_each_frame_reports_its_length_for_the_echo_guard():
    async def go():
        rig = Rig(lambda t: [SECOND, SECOND * 2])
        await rig.ls.say("Hello there.")
        return rig
    assert run(go()).pcm_seconds == [1.0, 2.0]


def test_say_returns_only_after_the_audio_is_sent():
    async def go():
        rig = Rig(lambda t: [0.05, SECOND])
        await rig.ls.say("Hello there.")
        return len(rig.frames)
    assert run(go()) == 1


def test_say_nowait_returns_at_once_and_drain_waits():
    async def go():
        rig = Rig(lambda t: [0.1, SECOND])
        assert rig.ls.say_nowait("Hello there.") is True
        before = len(rig.frames)
        await rig.ls.drain()
        return before, len(rig.frames)
    assert run(go()) == (0, 1)


def test_drain_with_nothing_queued_returns_at_once():
    async def go():
        rig = Rig()
        t = time.monotonic()
        await rig.ls.drain()
        return time.monotonic() - t
    assert run(go()) < 0.2


def test_empty_text_is_accepted_and_ignored():
    async def go():
        rig = Rig()
        assert rig.ls.say_nowait("   ") is True
        await rig.ls.drain()
        return rig
    rig = run(go())
    assert rig.requests == [] and rig.frames == []


def test_pieces_are_spoken_one_at_a_time_and_in_order():
    async def go():
        rig = Rig(lambda t: [0.05, SECOND, 0.05])
        for text in ["First sentence here.", "Second sentence is a bit longer than the first one is.", "Third."]:
            rig.ls.say_nowait(text)
        await rig.ls.drain()
        return rig
    rig = run(go())
    assert rig.max_running == 1
    assert " ".join(rig.requests) == "First sentence here. Second sentence is a bit longer than the first one is. Third."


def test_pieces_that_wait_are_merged_up_to_the_limit():
    async def go():
        rig = Rig(lambda t: [0.2, SECOND])
        rig.ls.say_nowait("First one is here.")                  # taken at once, then B, C, D wait behind it
        await asyncio.sleep(0.05)
        for text in ["Bee.", "Sea.", "Dee."]:
            rig.ls.say_nowait(text)
        await rig.ls.drain()
        return rig
    assert run(go()).requests == ["First one is here.", "Bee. Sea. Dee."]


def test_merging_never_goes_over_the_limit():
    async def go():
        rig = Rig(lambda t: [0.2, SECOND])
        rig.ls.say_nowait("Go.")
        await asyncio.sleep(0.05)
        rig.ls.say_nowait("A" * 30 + ".")
        rig.ls.say_nowait("B" * 30 + ".")                           # 31 + 1 + 31 = 63 > 60
        await rig.ls.drain()
        return rig
    reqs = run(go()).requests
    assert reqs == ["Go.", "A" * 30 + ".", "B" * 30 + "."] and all(len(r) <= 60 for r in reqs)


def test_a_long_text_is_split_into_requests_within_the_limit_without_losing_words():
    text = ("The weekly report is ready, and sales are up four percent on last week. The breakfast service ran two minutes "
            "faster. Two suppliers sent late invoices and one delivery is still missing, so I can draft the reminders now.")

    async def go():
        rig = Rig()
        await rig.ls.say(text)
        return rig
    rig = run(go())
    assert len(rig.requests) > 1 and all(len(r) <= 60 for r in rig.requests)
    assert " ".join(rig.requests).split() == text.split()


def test_on_start_runs_before_the_audio_of_its_text():
    async def go():
        rig = Rig(lambda t: [SECOND])
        order = []

        async def started():
            order.append(("start", len(rig.frames)))
        await rig.ls.say("Hello there.", on_start=started)
        return order
    assert run(go()) == [("start", 0)]


def test_on_start_runs_for_every_text_even_when_texts_are_merged():
    async def go():
        rig = Rig(lambda t: [0.2, SECOND])
        seen = []
        rig.ls.say_nowait("One.", on_start=lambda: seen.append("one"))
        await asyncio.sleep(0.05)
        rig.ls.say_nowait("Two.", on_start=lambda: seen.append("two"))
        rig.ls.say_nowait("Three.", on_start=lambda: seen.append("three"))
        await rig.ls.drain()
        return seen, rig.requests
    seen, reqs = run(go())
    assert seen == ["one", "two", "three"] and reqs == ["One.", "Two. Three."]


def test_a_failing_on_start_does_not_stop_the_speech():
    async def go():
        rig = Rig()

        def boom():
            raise RuntimeError("callback bug")
        await rig.ls.say("Hello there.", on_start=boom)
        return rig
    assert len(pcm_frames(run(go()))) == 1


# ── when the clone cannot do it ─────────────────────────────────────────────

def test_a_piece_the_clone_could_not_start_is_spoken_by_kokoro_once(notes):
    async def go():
        rig = Rig(lambda t: [codec_tts.CloneUnavailable("http_500")])
        await rig.ls.say("Hello there.")
        return rig
    rig = run(go())
    assert rig.kokoro_calls == ["Hello there."] and rig.frames == [b"ID3-kokoro"] and rig.files == [b"ID3-kokoro"]
    assert rig.ls.stats == {"clone": 0, "kokoro": 1, "failures": 1}
    assert notes == [("live", "http_500")]


def test_a_stream_that_breaks_after_audio_drops_the_rest_without_kokoro(notes):
    async def go():
        rig = Rig(lambda t: [SECOND, codec_tts.CloneBroke("timeout")])
        await rig.ls.say("Hello there, this goes on.")
        return rig
    rig = run(go())
    assert len(pcm_frames(rig)) == 1 and rig.kokoro_calls == [] and notes == [("live", "timeout")]


def test_two_failures_in_a_row_turn_the_clone_off_for_the_call(notes):
    async def go():
        rig = Rig(lambda t: [codec_tts.CloneUnavailable("timeout")])
        await rig.ls.say("One.")
        await rig.ls.say("Two.")
        assert rig.ls.clone_off is True
        await rig.ls.say("Three.")
        return rig
    rig = run(go())
    assert rig.requests == ["One.", "Two."], "the third piece never asked the clone"
    assert rig.kokoro_calls == ["One.", "Two.", "Three."]
    assert notes == [("live", "timeout"), ("live", "timeout"), ("live", "breaker_open")]


def test_a_good_piece_resets_the_failure_count():
    async def go():
        calls = {"n": 0}

        def script(text):
            calls["n"] += 1
            return [codec_tts.CloneUnavailable("timeout")] if calls["n"] in (1, 3) else [SECOND]
        rig = Rig(script)
        for text in ["One.", "Two.", "Three.", "Four."]:
            await rig.ls.say(text)
        return rig
    rig = run(go())
    assert rig.ls.clone_off is False and rig.ls.stats["failures"] == 2 and rig.requests == ["One.", "Two.", "Three.", "Four."]


def test_a_breaker_limit_of_one_turns_it_off_at_the_first_failure():
    async def go():
        rig = Rig(lambda t: [codec_tts.CloneUnavailable("x")], live=dict(LIVE, breaker_failures=1))
        await rig.ls.say("One.")
        return rig.ls.clone_off
    assert run(go()) is True


def test_an_unexpected_error_in_the_stream_counts_as_a_failure_and_falls_back(notes):
    async def go():
        rig = Rig(lambda t: [ValueError("surprise")])
        await rig.ls.say("Hello there.")
        return rig
    rig = run(go())
    assert rig.kokoro_calls == ["Hello there."] and notes == [("live", "ValueError")]


def test_when_kokoro_fails_too_nothing_is_sent_and_nothing_is_raised():
    async def go():
        rig = Rig(lambda t: [codec_tts.CloneUnavailable("down")], kokoro=None)
        ok = await rig.ls.say("Hello there.")
        return ok, rig
    ok, rig = run(go())
    assert ok is True and rig.frames == []


def test_nothing_that_is_logged_carries_the_text(notes):
    async def go():
        rig = Rig(lambda t: [codec_tts.CloneUnavailable("http_500")])
        await rig.ls.say("My secret sentence.")
    run(go())
    assert all("secret" not in str(n) for n in notes)


# ── stopping ────────────────────────────────────────────────────────────────

def test_cancel_closes_the_stream_drops_the_queue_and_tells_the_page_once():
    async def go():
        rig = Rig(lambda t: [SECOND, 5.0, SECOND])
        rig.ls.say_nowait("A long reply that is being spoken.")
        rig.ls.say_nowait("And more text that is waiting behind it.")
        await asyncio.sleep(0.1)
        await rig.ls.cancel()
        await rig.ls.drain()
        return rig
    rig = run(go())
    assert rig.closed == ["A long reply that is being spoken."], "the stream was closed, and the waiting text never started"
    assert rig.jsons == [{"type": "audio_reset"}] and len(pcm_frames(rig)) == 1


def test_cancel_while_still_waiting_for_the_first_byte_is_quick():
    async def go():
        rig = Rig(lambda t: [30.0, SECOND])
        rig.ls.say_nowait("Hello there.")
        await asyncio.sleep(0.1)
        t = time.monotonic()
        await rig.ls.cancel()
        return time.monotonic() - t, rig
    took, rig = run(go())
    assert took < 0.5 and rig.closed == ["Hello there."] and rig.frames == []


def test_after_a_cancel_the_next_text_is_spoken():
    async def go():
        rig = Rig(lambda t: [5.0, SECOND] if t.startswith("Old") else [SECOND])
        rig.ls.say_nowait("Old reply.")
        await asyncio.sleep(0.05)
        await rig.ls.cancel()
        await rig.ls.say("New reply.")
        return rig
    rig = run(go())
    assert rig.requests == ["Old reply.", "New reply."] and len(pcm_frames(rig)) == 1


def test_cancel_with_nothing_speaking_still_resets_the_page():
    async def go():
        rig = Rig()
        await rig.ls.cancel()
        return rig.jsons
    assert run(go()) == [{"type": "audio_reset"}]


def test_cancel_can_skip_the_reset():
    async def go():
        rig = Rig()
        await rig.ls.cancel(reset=False)
        return rig.jsons
    assert run(go()) == []


def test_reset_page_only_sends_the_message():
    async def go():
        rig = Rig(lambda t: [0.2, SECOND])
        rig.ls.say_nowait("Hello there.")
        await rig.ls.reset_page()
        await rig.ls.drain()
        return rig
    rig = run(go())
    assert rig.jsons == [{"type": "audio_reset"}] and len(pcm_frames(rig)) == 1


def test_text_is_refused_while_the_call_is_interrupted():
    async def go():
        rig = Rig()
        rig.interrupted.set()
        ok = rig.ls.say_nowait("Hello there.")
        okay_say = await rig.ls.say("Hello there.")
        return ok, okay_say, rig
    ok, okay_say, rig = run(go())
    assert ok is False and okay_say is False and rig.requests == []


def test_waiting_text_is_dropped_when_the_interrupt_flag_is_set():
    async def go():
        rig = Rig(lambda t: [0.3, SECOND])
        rig.ls.say_nowait("First reply here.")
        await asyncio.sleep(0.05)
        rig.ls.say_nowait("Second reply waits.")
        rig.interrupted.set()
        await rig.ls.drain()
        return rig
    rig = run(go())
    assert rig.requests == ["First reply here."] and not rig.kokoro_calls


def test_the_interrupt_flag_stops_a_piece_between_chunks():
    async def go():
        rig = Rig(lambda t: [SECOND, SECOND, SECOND])
        rig.ls._on_pcm = lambda s: rig.interrupted.set()           # the owner speaks as soon as the first chunk is out
        await rig.ls.say("Hello there.")
        return rig
    rig = run(go())
    assert len(pcm_frames(rig)) == 1 and rig.closed == ["Hello there."]


def test_a_closed_socket_stops_the_stream_and_the_worker():
    async def go():
        rig = Rig(lambda t: [SECOND] * 5, send_ok=False)
        ok = await rig.ls.say("Hello there.")
        again = rig.ls.say_nowait("More text.")
        return ok, again, rig
    ok, again, rig = run(go())
    assert ok is False and again is False and rig.closed == ["Hello there."] and rig.frames == []


def test_a_closed_socket_during_a_kokoro_piece_is_noticed():
    async def go():
        rig = Rig(lambda t: [codec_tts.CloneUnavailable("x")], send_ok=False)
        await rig.ls.say("Hello there.")
        return rig.ls.say_nowait("More.")
    assert run(go()) is False


def test_close_stops_the_worker_and_refuses_new_text():
    async def go():
        rig = Rig(lambda t: [5.0, SECOND])
        rig.ls.say_nowait("Hello there.")
        await asyncio.sleep(0.05)
        await rig.ls.close()
        return rig.ls.say_nowait("More."), rig.closed, rig.ls._task.done()
    again, closed, done = run(go())
    assert again is False and closed == ["Hello there."] and done is True


def test_a_clean_finish_leaves_nothing_running():
    async def go():
        rig = Rig()
        await rig.ls.say("Hello there.")
        await asyncio.sleep(0.05)
        return rig.running, rig.ls._current
    assert run(go()) == (0, None)
