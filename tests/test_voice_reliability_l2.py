"""L2 — regression tests for codec_voice.py reliability fixes (review sweep).

  - _resolve_voice_option_choice prefers the LONGEST matching label
  - _enqueue_utterance is non-blocking + drops oldest on overflow (no HOL block)
  - feed_audio force-flushes a runaway utterance (no unbounded buffer)
  - feed_audio keeps capturing a barge-in mid-utterance during echo cooldown
  - save_to_memory is idempotent (the run() + route double-call no longer 2x)
  - _stream_qwen flags an error so the consumer skips persisting the sentinel
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _make_pipeline():
    ws = AsyncMock()
    ws.send_bytes = AsyncMock()
    ws.send_json = AsyncMock()
    with patch("codec_voice.VoicePipeline._load_skills"):
        with patch("codec_voice._build_system_prompt", return_value="sys"):
            from codec_voice import VoicePipeline
            p = VoicePipeline(ws)
    p.skills = {}
    p._http = AsyncMock(spec=httpx.AsyncClient)
    return p


# ── 1. longest-match option resolver ───────────────────────────────────────
class TestOptionResolver:
    def test_longest_label_wins(self):
        from codec_voice import _resolve_voice_option_choice
        opts = ["yes", "yes and notify"]
        # "yes" appears first in the list but "yes and notify" is the real intent
        got = _resolve_voice_option_choice("okay, yes and notify me", opts)
        assert got == "yes and notify"

    def test_plain_yes_still_matches(self):
        from codec_voice import _resolve_voice_option_choice
        got = _resolve_voice_option_choice("yes", ["yes", "yes and notify"])
        assert got == "yes"

    def test_strict_mode_bypassed(self):
        from codec_voice import _resolve_voice_option_choice
        raw = _resolve_voice_option_choice("delete", ["delete", "cancel"], strict=True)
        assert raw == "delete"  # returned verbatim for the strict-consent gate


# ── 2. non-blocking enqueue drops oldest on overflow ───────────────────────
class TestEnqueueUtterance:
    def test_overflow_drops_oldest_keeps_newest(self):
        p = _make_pipeline()
        # maxsize is 3
        for i in range(3):
            p.utterance_queue.put_nowait(bytes([i]))
        assert p.utterance_queue.full()
        p._enqueue_utterance(b"\xff")  # 4th — must not block, drops oldest
        assert p.utterance_queue.qsize() == 3
        items = [p.utterance_queue.get_nowait() for _ in range(3)]
        assert items[0] == bytes([1]), "oldest (0) should have been dropped"
        assert items[-1] == b"\xff", "newest should be present"

    def test_normal_enqueue_when_space(self):
        p = _make_pipeline()
        p._enqueue_utterance(b"hi")
        assert p.utterance_queue.qsize() == 1


# ── 3. feed_audio bounds + barge-in capture ─────────────────────────────────
class TestFeedAudio:
    def test_runaway_utterance_force_flushes(self):
        import codec_voice as cv
        p = _make_pipeline()
        # a single chunk above the cap → must flush immediately, not buffer
        big = b"\x10\x10" * (cv.MAX_UTTERANCE_BYTES // 2 + 100)
        out = p.feed_audio(big)
        assert out is not None, "utterance over the cap must force-flush"
        assert len(p.audio_buffer) == 0, "buffer must be reset after force-flush"

    def test_barge_in_during_cooldown_still_captures(self):
        import time as _t
        import codec_voice as cv
        p = _make_pipeline()
        # simulate: user already mid-utterance, CODEC's TTS cooldown active
        p.is_speaking = True
        p.last_tts_end = _t.monotonic() + cv.VAD_ECHO_COOLDOWN  # cooldown in effect
        before = len(p.audio_buffer)
        loud = (b"\x00\x40") * 800   # above VAD threshold
        p.feed_audio(loud)
        # mid-utterance audio must be appended, not dropped by the cooldown
        assert len(p.audio_buffer) > before, "barge-in audio was dropped during cooldown"

    def test_new_start_suppressed_during_cooldown(self):
        import time as _t
        import codec_voice as cv
        p = _make_pipeline()
        p.is_speaking = False  # NOT already speaking
        p.last_tts_end = _t.monotonic() + cv.VAD_ECHO_COOLDOWN
        loud = (b"\x00\x40") * 800
        out = p.feed_audio(loud)
        # a fresh start during cooldown is still suppressed (echo guard intact)
        assert out is None
        assert len(p.audio_buffer) == 0


# ── 4. save_to_memory idempotency ───────────────────────────────────────────
def test_save_to_memory_is_idempotent():
    p = _make_pipeline()
    p.messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    mem = MagicMock()
    with patch("codec_memory.CodecMemory", return_value=mem):
        p.save_to_memory()
        p.save_to_memory()  # second call (route + run double-finally) must no-op
    assert mem.save.call_count == 2, (
        f"expected 2 saves (one per message, once), got {mem.save.call_count}"
    )


# ── 5. _stream_qwen flags error so consumer can skip persisting sentinel ────
def test_stream_qwen_sets_error_flag_on_failure():
    p = _make_pipeline()

    async def _boom(*a, **k):
        raise RuntimeError("qwen down")
        yield  # pragma: no cover - makes this an async generator

    async def _drive():
        import codec_llm
        with patch.object(codec_llm, "astream", _boom):
            chunks = [c async for c in p._stream_qwen([{"role": "user", "content": "x"}])]
        return chunks

    chunks = asyncio.run(_drive())
    assert p._stream_error is True
    assert any("processing error" in c.lower() for c in chunks), "user should still hear the error"
