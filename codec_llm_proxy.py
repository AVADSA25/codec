"""CODEC LLM Priority Queue — ensures interactive requests aren't starved by background tasks."""
import asyncio
import logging
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

    async def acquire(self, priority: Priority = Priority.MEDIUM):
        """Wait for an LLM slot. Higher priority requests are served first."""
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

    async def release(self, priority: Priority = Priority.MEDIUM):
        """Release an LLM slot."""
        async with self._lock:
            self._active[priority.name] = max(0, self._active[priority.name] - 1)
        self._semaphore.release()

    @property
    def stats(self) -> dict:
        """Current queue state for debugging / dashboard."""
        return {"active": dict(self._active), "waiting": dict(self._waiting)}


# Singleton
llm_queue = LLMQueue(max_concurrent=2)
