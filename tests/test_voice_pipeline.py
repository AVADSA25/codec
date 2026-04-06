"""Voice pipeline E2E tests -- mock STT/LLM/TTS services, test the full chain."""
import asyncio
import json
import struct
import sys
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure repo root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Guard against missing deps
try:
    import numpy as np
    import httpx
except ImportError:
    pytest.skip("numpy or httpx not available", allow_module_level=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run an async coroutine synchronously (no pytest-asyncio needed)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_pcm_silence(duration_s: float = 0.5, sample_rate: int = 16000) -> bytes:
    """Generate silent PCM16 audio (all zeros)."""
    n_samples = int(sample_rate * duration_s)
    return b"\x00\x00" * n_samples


def _make_pcm_loud(duration_s: float = 0.5, sample_rate: int = 16000, amplitude: int = 5000) -> bytes:
    """Generate loud PCM16 audio (sine wave) that will pass VAD threshold."""
    n_samples = int(sample_rate * duration_s)
    t = np.linspace(0, duration_s, n_samples, endpoint=False)
    samples = (amplitude * np.sin(2 * np.pi * 440 * t)).astype(np.int16)
    return samples.tobytes()


def _make_pipeline():
    """Create a VoicePipeline with a mock websocket, bypassing skill loading."""
    ws = AsyncMock()
    ws.send_bytes = AsyncMock()
    ws.send_json = AsyncMock()

    with patch("codec_voice.VoicePipeline._load_skills"):
        with patch("codec_voice._build_system_prompt", return_value="You are a test assistant."):
            from codec_voice import VoicePipeline
            pipeline = VoicePipeline(ws)

    pipeline.skills = {}
    pipeline._skill_registry = MagicMock()
    pipeline._http = AsyncMock(spec=httpx.AsyncClient)
    return pipeline


def _mock_stream(sse_lines):
    """Build a mock async context manager that yields SSE lines from a list."""
    async def mock_aiter_lines():
        for line in sse_lines:
            yield line

    mock_resp = AsyncMock()
    mock_resp.aiter_lines = mock_aiter_lines

    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    return mock_cm


# ---------------------------------------------------------------------------
# 1. VAD tests
# ---------------------------------------------------------------------------

class TestVAD:

    def test_vad_detects_speech(self):
        """Feed audio bytes above RMS threshold, verify VAD triggers and buffers audio."""
        pipeline = _make_pipeline()

        loud = _make_pcm_loud(duration_s=0.5, amplitude=5000)
        result = pipeline.feed_audio(loud)
        # First chunk starts buffering -- no utterance yet (need silence gap to flush)
        assert result is None
        assert pipeline.is_speaking is True
        assert len(pipeline.audio_buffer) > 0

    def test_vad_ignores_silence(self):
        """Feed silent audio (zeros), verify nothing is queued."""
        pipeline = _make_pipeline()

        silence = _make_pcm_silence(duration_s=0.5)
        result = pipeline.feed_audio(silence)
        assert result is None
        assert pipeline.is_speaking is False
        assert len(pipeline.audio_buffer) == 0

    def test_vad_echo_cooldown(self):
        """Set last_tts_end to recent time, feed loud audio, verify it is ignored during cooldown."""
        pipeline = _make_pipeline()

        # Simulate TTS just finished
        pipeline.last_tts_end = time.monotonic()

        loud = _make_pcm_loud(duration_s=0.5, amplitude=5000)
        result = pipeline.feed_audio(loud)
        assert result is None
        # Buffer should remain empty -- echo cooldown rejected the audio
        assert len(pipeline.audio_buffer) == 0
        assert pipeline.is_speaking is False

    def test_vad_flushes_after_silence(self):
        """Feed loud audio, then enough silence to trigger flush, verify utterance returned."""
        pipeline = _make_pipeline()

        # Feed enough loud audio to exceed MIN_SPEECH_BYTES
        loud = _make_pcm_loud(duration_s=0.6, amplitude=5000)
        pipeline.feed_audio(loud)
        assert pipeline.is_speaking is True

        # Artificially age the last_speech_time to simulate silence duration elapsed
        from codec_voice import VAD_SILENCE_DURATION
        pipeline.last_speech_time = time.monotonic() - VAD_SILENCE_DURATION - 0.1

        # Feed silent chunk to trigger the flush path
        silence = _make_pcm_silence(duration_s=0.1)
        result = pipeline.feed_audio(silence)
        # Should return the buffered utterance
        assert result is not None
        assert len(result) > 0


# ---------------------------------------------------------------------------
# 2. STT (Whisper) tests
# ---------------------------------------------------------------------------

class TestTranscribe:

    def test_transcribe_filters_noise(self):
        """Mock Whisper HTTP to return 'um', verify transcribe() returns empty string."""
        pipeline = _make_pipeline()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"text": "um"}
        pipeline._http.post = AsyncMock(return_value=mock_response)

        pcm = _make_pcm_loud(duration_s=0.5)
        result = _run(pipeline.transcribe(pcm))
        assert result == ""

    def test_transcribe_returns_text(self):
        """Mock Whisper HTTP to return real text, verify transcribe() returns it."""
        pipeline = _make_pipeline()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"text": "What is the weather in Marbella today"}
        pipeline._http.post = AsyncMock(return_value=mock_response)

        # Patch clean_transcript to pass through
        mock_config = MagicMock()
        mock_config.clean_transcript = lambda t: t
        with patch.dict("sys.modules", {"codec_config": mock_config}):
            pcm = _make_pcm_loud(duration_s=0.5)
            result = _run(pipeline.transcribe(pcm))

        assert result == "What is the weather in Marbella today"

    def test_transcribe_handles_http_error(self):
        """Mock Whisper returning 500, verify transcribe() returns empty string."""
        pipeline = _make_pipeline()

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        pipeline._http.post = AsyncMock(return_value=mock_response)

        pcm = _make_pcm_loud(duration_s=0.5)
        result = _run(pipeline.transcribe(pcm))
        assert result == ""


# ---------------------------------------------------------------------------
# 3. LLM (Qwen) streaming tests
# ---------------------------------------------------------------------------

class TestGenerateResponse:

    def test_generate_response_streams(self):
        """Mock Qwen HTTP to return streaming SSE response, verify generate_response() accumulates tokens."""
        pipeline = _make_pipeline()

        # Build fake SSE lines
        tokens = ["Hello", " there", ", how", " can", " I", " help", "?"]
        sse_lines = []
        for tok in tokens:
            chunk = {"choices": [{"delta": {"content": tok}}]}
            sse_lines.append(f"data: {json.dumps(chunk)}")
        sse_lines.append("data: [DONE]")

        pipeline._http.stream = MagicMock(return_value=_mock_stream(sse_lines))

        async def _collect():
            accumulated = ""
            async for chunk in pipeline.generate_response("Hello"):
                accumulated += chunk
            return accumulated

        accumulated = _run(_collect())

        assert accumulated == "Hello there, how can I help?"
        # Verify messages were appended (user + assistant)
        assert any(m["role"] == "user" and m["content"] == "Hello" for m in pipeline.messages)
        assert any(m["role"] == "assistant" and accumulated in m["content"] for m in pipeline.messages)


# ---------------------------------------------------------------------------
# 4. TTS (Kokoro) tests
# ---------------------------------------------------------------------------

class TestSynthesize:

    def test_synthesize_returns_audio(self):
        """Mock Kokoro HTTP to return PCM bytes, verify synthesize() returns them."""
        pipeline = _make_pipeline()

        fake_audio = b"\x00\x01" * 8000  # fake PCM data
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = fake_audio
        pipeline._http.post = AsyncMock(return_value=mock_response)

        result = _run(pipeline.synthesize("Hello world"))
        assert result == fake_audio

    def test_synthesize_empty_text_returns_none(self):
        """Verify synthesize() returns None for empty input."""
        pipeline = _make_pipeline()
        result = _run(pipeline.synthesize(""))
        assert result is None

    def test_synthesize_handles_error(self):
        """Mock Kokoro returning error, verify synthesize() returns None."""
        pipeline = _make_pipeline()

        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.text = "Service Unavailable"
        pipeline._http.post = AsyncMock(return_value=mock_response)

        result = _run(pipeline.synthesize("Hello world"))
        assert result is None


# ---------------------------------------------------------------------------
# 5. Full chain integration test
# ---------------------------------------------------------------------------

class TestFullChain:

    def test_full_chain_audio_to_speech(self):
        """Integration: feed audio -> mock STT returns text -> mock LLM returns response -> mock TTS returns audio bytes."""
        pipeline = _make_pipeline()

        # -- Step 1: VAD -- feed loud audio and force a flush
        loud = _make_pcm_loud(duration_s=0.6, amplitude=5000)
        pipeline.feed_audio(loud)
        assert pipeline.is_speaking is True

        from codec_voice import VAD_SILENCE_DURATION
        pipeline.last_speech_time = time.monotonic() - VAD_SILENCE_DURATION - 0.1
        silence = _make_pcm_silence(duration_s=0.1)
        utterance = pipeline.feed_audio(silence)
        assert utterance is not None, "VAD should have flushed an utterance"

        # -- Step 2: Mock Whisper (STT)
        mock_whisper = MagicMock()
        mock_whisper.status_code = 200
        mock_whisper.json.return_value = {"text": "What time is it in Madrid"}

        # -- Step 3: Mock Qwen (LLM streaming)
        sse_lines = [
            'data: {"choices":[{"delta":{"content":"It is "}}]}',
            'data: {"choices":[{"delta":{"content":"currently 3 PM in Madrid."}}]}',
            'data: [DONE]',
        ]

        # -- Step 4: Mock Kokoro (TTS)
        fake_tts_audio = b"\xff\x01" * 4000
        mock_tts = MagicMock()
        mock_tts.status_code = 200
        mock_tts.content = fake_tts_audio

        # Wire up the HTTP mock: post is called for Whisper then TTS, stream for LLM
        pipeline._http.post = AsyncMock(side_effect=[mock_whisper, mock_tts])
        pipeline._http.stream = MagicMock(return_value=_mock_stream(sse_lines))

        async def _run_chain():
            # STT
            mock_config = MagicMock()
            mock_config.clean_transcript = lambda t: t
            with patch.dict("sys.modules", {"codec_config": mock_config}):
                text = await pipeline.transcribe(utterance)
            assert text == "What time is it in Madrid"

            # LLM
            response_text = ""
            async for chunk in pipeline.generate_response(text):
                response_text += chunk
            assert "3 PM" in response_text

            # TTS
            audio_out = await pipeline.synthesize(response_text)
            assert audio_out == fake_tts_audio
            return response_text, audio_out

        response_text, audio_out = _run(_run_chain())
        assert audio_out == fake_tts_audio

    def test_full_chain_noise_produces_no_output(self):
        """Feed audio that transcribes to noise -- verify chain stops early."""
        pipeline = _make_pipeline()

        mock_whisper = MagicMock()
        mock_whisper.status_code = 200
        mock_whisper.json.return_value = {"text": "uh"}
        pipeline._http.post = AsyncMock(return_value=mock_whisper)

        pcm = _make_pcm_loud(duration_s=0.5)
        text = _run(pipeline.transcribe(pcm))
        assert text == "", "Noise should be filtered out"
        # Chain stops here -- no LLM or TTS calls needed
