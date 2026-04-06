"""CODEC LLM Priority Queue — ensures interactive requests aren't starved by background tasks.

Usage (async context manager — preferred):
    async with llm_queue.slot(Priority.CRITICAL):
        response = await httpx_client.post(url, json=payload)

Usage (manual acquire/release):
    await llm_queue.acquire(Priority.MEDIUM)
    try:
        response = await httpx_client.post(url, json=payload)
    finally:
        await llm_queue.release(Priority.MEDIUM)

Usage (sync wrapper for subprocess callers):
    with llm_queue_sync(Priority.MEDIUM):
        response = requests.post(url, json=payload)
"""
import asyncio
import logging
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from enum import IntEnum

log = logging.getLogger(__name__)


class Priority(IntEnum):
    CRITICAL = 0   # Voice pipeline — user waiting for response
    HIGH = 1       # Dashboard chat — user facing
    MEDIUM = 2     # Agent crews — background
    LOW = 3        # Compaction — background, deferrable


class LLMQueue:
    """Priority-based semaphore for LLM access.

    Limits concurrent LLM requests and prioritizes interactive over background.
    Does NOT proxy HTTP — callers still make their own requests.
    They just acquire/release a slot first.
    """

    def __init__(self, max_concurrent: int = 2):
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
        self._lock = asyncio.Lock()
        self._waiting = {p: 0 for p in Priority}
        self._total_requests = {p: 0 for p in Priority}
        self._total_wait_ms = {p: 0.0 for p in Priority}

    async def acquire(self, priority: Priority = Priority.MEDIUM):
        """Wait for an LLM slot. Higher priority requests are served first."""
        start = time.monotonic()

        # If a CRITICAL or HIGH request is waiting, MEDIUM/LOW must yield
        if priority >= Priority.MEDIUM:
            while True:
                async with self._lock:
                    if self._waiting.get(Priority.CRITICAL, 0) == 0 and \
                       self._waiting.get(Priority.HIGH, 0) == 0:
                        break
                await asyncio.sleep(0.1)

        async with self._lock:
            self._waiting[priority] += 1

        await self._semaphore.acquire()

        async with self._lock:
            self._waiting[priority] -= 1
            self._active[priority.name] += 1
            self._total_requests[priority] += 1
            self._total_wait_ms[priority] += (time.monotonic() - start) * 1000

    async def release(self, priority: Priority = Priority.MEDIUM):
        """Release an LLM slot."""
        async with self._lock:
            self._active[priority.name] = max(0, self._active[priority.name] - 1)
        self._semaphore.release()

    @asynccontextmanager
    async def slot(self, priority: Priority = Priority.MEDIUM):
        """Async context manager for LLM slot acquisition."""
        await self.acquire(priority)
        try:
            yield
        finally:
            await self.release(priority)

    @property
    def stats(self) -> dict:
        """Current queue state for debugging / dashboard."""
        return {
            "active": dict(self._active),
            "waiting": dict(self._waiting),
            "total_requests": {p.name: self._total_requests[p] for p in Priority},
            "avg_wait_ms": {
                p.name: (self._total_wait_ms[p] / self._total_requests[p])
                if self._total_requests[p] > 0 else 0
                for p in Priority
            },
        }


# Singleton
llm_queue = LLMQueue(max_concurrent=2)


# ── Sync wrapper for subprocess/thread callers ─────────────────────────

class _SyncLLMSlot:
    """Thread-safe semaphore for synchronous callers (codec_session, skills)."""

    def __init__(self, max_concurrent: int = 2):
        self._sem = threading.Semaphore(max_concurrent)
        self._high_waiting = threading.Event()
        self._high_waiting.set()  # initially no high-priority waiting

    @contextmanager
    def __call__(self, priority: Priority = Priority.MEDIUM):
        if priority >= Priority.MEDIUM:
            self._high_waiting.wait(timeout=5)
        self._sem.acquire()
        try:
            yield
        finally:
            self._sem.release()


llm_queue_sync = _SyncLLMSlot(max_concurrent=2)
