"""The speaking worker of the live voice call (docs/CODEC-VOICE-02-LIVE-DESIGN.md).

One LiveSpeech per call. Text goes in (`say_nowait` / `say`), audio frames go out over the call's WebSocket, one piece at a time,
because the TTS server does one thing at a time. A piece is streamed from the clone (voice 02) as raw PCM; if the clone cannot
start, that piece is spoken by the Kokoro blend as one whole-file frame, exactly as before. Two clone failures in a row turn the
clone off for the rest of the call. Cancelling (the owner interrupts) closes the stream, which makes the server stop generating,
drops what is waiting and tells the page to drop the audio that is already on its way (`audio_reset`).

Wire format of a streamed frame: b"CPCM" + the sample rate (4 bytes, little endian) + signed 16-bit little-endian mono samples.
A whole-file frame (the Kokoro MP3) is unchanged, so the page tells them apart by the first four bytes.
"""
from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import struct
from collections import deque
from typing import Awaitable, Callable, Optional

import codec_tts

log = logging.getLogger("codec.voice.speech")

PCM_MAGIC = b"CPCM"
DRAIN_LIMIT = 600.0     # seconds: a drain never waits longer than this, whatever happens to a piece


def pcm_frame(pcm: bytes, rate: int) -> bytes:
    """One WebSocket frame of streamed speech."""
    return PCM_MAGIC + struct.pack("<I", int(rate)) + pcm


class LiveSpeech:
    """`live` is codec_tts.live_settings(...). The callables are the call's own:
    send_bytes(bytes) -> bool and send_json(dict) -> bool (False when the socket is gone), kokoro(text) -> bytes | None (one
    whole MP3), interrupted (the call's asyncio.Event), on_pcm(seconds) and on_file(audio) (called after a frame was sent, for
    the echo guard). `stream` is codec_tts.astream_clone unless a test passes another."""

    def __init__(self, live: dict, *, http, url: str,
                 send_bytes: Callable[[bytes], Awaitable[bool]],
                 send_json: Callable[[dict], Awaitable[bool]],
                 kokoro: Callable[[str], Awaitable[Optional[bytes]]],
                 interrupted: asyncio.Event,
                 on_pcm: Optional[Callable[[float], None]] = None,
                 on_file: Optional[Callable[[bytes], None]] = None,
                 stream=None):
        self._live = live
        self._http = http
        self._url = url
        self._send_bytes = send_bytes
        self._send_json = send_json
        self._kokoro = kokoro
        self._interrupted = interrupted
        self._on_pcm = on_pcm
        self._on_file = on_file
        self._stream = stream or codec_tts.astream_clone
        self._queue: deque = deque()                 # (text, on_start)
        self._wake = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._task: Optional[asyncio.Task] = None
        self._current: Optional[asyncio.Future] = None
        self._closed = False
        self._alive = True
        self._failures = 0                           # clone failures in a row
        self._clone_off = False
        self.stats = {"clone": 0, "kokoro": 0, "failures": 0}

    # ── what the call uses ──────────────────────────────────────────────────

    @property
    def clone_off(self) -> bool:
        return self._clone_off

    def say_nowait(self, text: str, on_start=None) -> bool:
        """Queue `text` and return at once. `on_start` (sync or async) runs just before its audio starts. False when the call
        is interrupted, closed or its socket is gone: nothing was queued."""
        if self._closed or not self._alive or self._interrupted.is_set():
            return False
        pieces = codec_tts.split_for_speech(text, self._live["chunk_chars"])
        for i, piece in enumerate(pieces):
            self._queue.append((piece, on_start if i == 0 else None))
        if pieces:
            self._idle.clear()
            self._wake.set()
            self._ensure_task()
        return True

    async def say(self, text: str, on_start=None) -> bool:
        """Queue `text` and wait until everything queued has been sent (or cancelled). False if it could not be spoken
        because the call was interrupted or closed."""
        if not self.say_nowait(text, on_start):
            return False
        await self.drain()
        return self._alive and not self._closed and not self._interrupted.is_set()

    async def drain(self) -> None:
        """Wait until nothing is queued and no piece is being spoken."""
        if self._idle.is_set():
            return
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._idle.wait(), DRAIN_LIMIT)

    async def cancel(self, *, reset: bool = True) -> None:
        """Stop speaking now: drop what is waiting, close the stream being read, and (by default) tell the page to drop the
        audio already on its way. Safe to call when nothing is being spoken."""
        self._queue.clear()
        cur = self._current
        if cur is not None and not cur.done():
            cur.cancel()
            await asyncio.wait({cur})
        if reset:
            await self.reset_page()

    async def reset_page(self) -> None:
        await self._send_json({"type": "audio_reset"})

    async def close(self) -> None:
        self._closed = True
        self._queue.clear()
        self._wake.set()
        cur, task = self._current, self._task
        if cur is not None and not cur.done():
            cur.cancel()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait({task})
        self._idle.set()

    # ── the worker ──────────────────────────────────────────────────────────

    def _ensure_task(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run(), name="live-speech")

    def _take_batch(self) -> list:
        """The first waiting piece plus the ones behind it that fit in one request."""
        batch = [self._queue.popleft()]
        size = len(batch[0][0])
        limit = self._live["chunk_chars"]
        while self._queue and size + 1 + len(self._queue[0][0]) <= limit:
            item = self._queue.popleft()
            size += 1 + len(item[0])
            batch.append(item)
        return batch

    async def _run(self) -> None:
        try:
            while not self._closed:
                if not self._queue:
                    self._idle.set()
                    self._wake.clear()
                    await self._wake.wait()
                    continue
                if self._interrupted.is_set():
                    self._queue.clear()
                    continue
                batch = self._take_batch()
                piece = asyncio.ensure_future(self._speak_piece(" ".join(t for t, _ in batch), [cb for _, cb in batch]))
                self._current = piece
                try:
                    await piece
                except asyncio.CancelledError:
                    task = asyncio.current_task()
                    if self._closed or (task is not None and task.cancelling()) or not piece.cancelled():
                        raise
                except Exception:
                    log.exception("[voice] a speech piece failed")
                finally:
                    self._current = None
        finally:
            self._idle.set()

    async def _speak_piece(self, text: str, callbacks: list) -> None:
        for cb in callbacks:
            if cb is None:
                continue
            try:
                result = cb()
                if inspect.isawaitable(result):
                    await result
            except asyncio.CancelledError:
                raise
            except Exception:
                log.debug("[voice] on_start callback failed", exc_info=True)
        if not self._clone_off and await self._clone_piece(text):
            return
        await self._kokoro_piece(text)

    async def _clone_piece(self, text: str) -> bool:
        """Stream `text` in voice 02. True: handled (spoken, cut short, or stopped on purpose). False: nothing was heard, so the
        caller speaks it with Kokoro."""
        rate = self._live["rate"]
        started = False
        agen = self._stream(self._http, self._url, text, self._live)
        try:
            async with contextlib.aclosing(agen):
                async for pcm in agen:
                    if self._interrupted.is_set():
                        return True
                    if not await self._send_bytes(pcm_frame(pcm, rate)):
                        self._alive = False
                        return True
                    started = True
                    if self._on_pcm is not None:
                        self._on_pcm(len(pcm) / 2.0 / rate)
            self._failures = 0
            self.stats["clone"] += 1
            return True
        except codec_tts.CloneUnavailable as e:
            self._failed(str(e))
            return False
        except codec_tts.CloneBroke as e:
            self._failed(str(e))
            return True
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._failed(type(e).__name__)
            return started

    def _failed(self, reason: str) -> None:
        self._failures += 1
        self.stats["failures"] += 1
        codec_tts.note_fallback("live", reason)
        if self._failures >= self._live["breaker_failures"] and not self._clone_off:
            self._clone_off = True
            codec_tts.note_fallback("live", "breaker_open")
            log.warning("[voice] voice 02 is off for the rest of this call after %d failures in a row", self._failures)

    async def _kokoro_piece(self, text: str) -> None:
        try:
            audio = await self._kokoro(text)
        except asyncio.CancelledError:
            raise
        except Exception:
            audio = None
        if not audio or self._interrupted.is_set():
            return
        if await self._send_bytes(audio):
            self.stats["kokoro"] += 1
            if self._on_file is not None:
                self._on_file(audio)
        else:
            self._alive = False
