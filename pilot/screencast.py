"""
CODEC Pilot — Phase 3: Screencast
===================================

Captures JPEG frames from PilotChrome at a configurable interval and
writes them to a trace directory.  Used by pilot_runner.py to record
agent runs for replay / debugging.

Usage:
    async with Screencast(pilot, trace_dir) as sc:
        # frames are captured in the background
        ...
    # sc.frames contains list of (timestamp, path) tuples
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .pilot_chrome import PilotChrome
from .config import PILOT_TRACES_DIR


@dataclass
class Frame:
    index: int
    ts: float          # epoch seconds
    path: Path
    size_bytes: int = 0


class Screencast:
    """
    Background JPEG frame capturer.

    async with Screencast(pilot, trace_id="run_xyz", fps=2) as sc:
        await pilot.navigate("https://example.com")
        await pilot.click_xpath("//a")
    # sc.frames: list[Frame]
    """

    def __init__(
        self,
        pilot: PilotChrome,
        trace_id: str,
        fps: float = 2.0,
        quality: int = 60,
        traces_dir: Path = PILOT_TRACES_DIR,
    ) -> None:
        self.pilot = pilot
        self.trace_id = trace_id
        self.fps = fps
        self.quality = quality
        self.frames: list[Frame] = []
        self._dir = traces_dir / trace_id
        self._task: Optional[asyncio.Task] = None
        self._running = False

    @property
    def trace_dir(self) -> Path:
        return self._dir

    async def __aenter__(self) -> "Screencast":
        self._dir.mkdir(parents=True, exist_ok=True)
        self._running = True
        self._task = asyncio.create_task(self._capture_loop())
        return self

    async def __aexit__(self, *exc) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Capture one final frame
        await self._capture_one()

    async def _capture_loop(self) -> None:
        interval = 1.0 / max(self.fps, 0.1)
        while self._running:
            await self._capture_one()
            await asyncio.sleep(interval)

    async def _capture_one(self) -> None:
        try:
            idx = len(self.frames)
            path = self._dir / f"frame_{idx:05d}.jpg"
            data = await self.pilot.screenshot(
                path=str(path), quality=self.quality
            )
            self.frames.append(Frame(
                index=idx,
                ts=time.time(),
                path=path,
                size_bytes=len(data),
            ))
        except Exception:
            pass  # browser may be navigating; skip frame silently

    def manifest(self) -> dict:
        """Return JSON-serialisable manifest of all captured frames."""
        return {
            "trace_id": self.trace_id,
            "trace_dir": str(self._dir),
            "fps": self.fps,
            "frame_count": len(self.frames),
            "frames": [
                {"index": f.index, "ts": f.ts, "path": str(f.path), "size": f.size_bytes}
                for f in self.frames
            ],
        }
