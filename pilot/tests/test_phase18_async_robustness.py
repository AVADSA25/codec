"""Pilot PP-12 — async robustness (audit P-14). Two unbounded waits that could pin the
browser + a run slot forever:

  1. HITL pause gate: `await self._pause_event.wait()` blocks the agent loop forever if the
     operator pauses and never resumes (tab closed, network drop). Bound it with a timeout;
     on expiry finalize the run as `paused_timeout` and free the browser/run slot.
  2. MJPEG stream: the `/screenshot/stream` `while True` swallowed every screenshot exception
     and spun at 4fps forever — a dead browser yields no frames but never closes the stream.
     Bound consecutive failures; close the generator so the client reconnects against a
     healthy state. A transient failure below the bound must NOT close the stream.

Sync tests driving the async code via asyncio.run (repo has no pytest-asyncio).

Reference: docs/PP12-ASYNC-ROBUSTNESS-DESIGN.md.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # ~/codec on sys.path

from pilot import pilot_runner  # noqa: E402
from pilot.hitl import HitlController  # noqa: E402
from pilot.pilot_agent import AgentRun  # noqa: E402


# ─── HITL pause-timeout ────────────────────────────────────────────────────────

def test_await_resume_true_when_not_paused():
    """Controller starts unpaused — the gate returns True immediately."""
    ctrl = HitlController(object(), task="t")
    run = AgentRun(task="t", run_id="r")
    assert asyncio.run(ctrl._await_resume_or_timeout(run)) is True


def test_await_resume_times_out_when_never_resumed():
    async def _run():
        ctrl = HitlController(object(), task="t", pause_timeout_s=0.05)
        await ctrl.pause("operator walked away")
        run = AgentRun(task="t", run_id="r")
        ok = await ctrl._await_resume_or_timeout(run)
        return ok, run

    ok, run = asyncio.run(_run())
    assert ok is False
    assert run.status == "paused_timeout"
    assert run.ended_at is not None


def test_await_resume_true_when_resumed_in_time():
    async def _run():
        ctrl = HitlController(object(), task="t", pause_timeout_s=5.0)
        await ctrl.pause("brief pause")

        async def _resume_soon():
            await asyncio.sleep(0.02)
            await ctrl.resume()

        asyncio.create_task(_resume_soon())
        run = AgentRun(task="t", run_id="r")
        return await ctrl._await_resume_or_timeout(run)

    assert asyncio.run(_run()) is True


def test_execute_returns_paused_timeout_without_resume():
    """End-to-end: a paused-and-abandoned run ends as paused_timeout (never touches the
    browser, because the pause gate is the first thing in the loop)."""
    async def _run():
        ctrl = HitlController(object(), task="t", pause_timeout_s=0.05, use_stub=True)
        await ctrl.pause("abandoned")
        return await ctrl.execute()

    run = asyncio.run(_run())
    assert run.status == "paused_timeout"


# ─── MJPEG consecutive-failure bound ───────────────────────────────────────────

def test_mjpeg_closes_after_consecutive_failures():
    class DeadPilot:
        async def screenshot(self, quality=70):
            raise RuntimeError("browser gone")

    async def _collect():
        out = []
        async for chunk in pilot_runner._mjpeg_frames(
            lambda: DeadPilot(), max_consecutive_failures=3, sleep_s=0
        ):
            out.append(chunk)
        return out

    frames = asyncio.run(_collect())
    assert frames == []  # terminated (didn't hang); no frame ever produced


def test_mjpeg_healthy_yields_frames():
    class GoodPilot:
        async def screenshot(self, quality=70):
            return b"IMGBYTES"

    async def _two():
        gen = pilot_runner._mjpeg_frames(lambda: GoodPilot(), max_consecutive_failures=3, sleep_s=0)
        a = await gen.__anext__()
        b = await gen.__anext__()
        await gen.aclose()
        return a, b

    a, b = asyncio.run(_two())
    assert b"IMGBYTES" in a and b"IMGBYTES" in b


def test_mjpeg_transient_failure_recovers():
    """Two failures (below the bound of 3) then a success — stream must not close, and the
    consecutive-failure counter resets on the successful frame."""
    calls = {"n": 0}

    class FlakyPilot:
        async def screenshot(self, quality=70):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise RuntimeError("transient")
            return b"RECOVERED"

    async def _first_frame():
        gen = pilot_runner._mjpeg_frames(lambda: FlakyPilot(), max_consecutive_failures=3, sleep_s=0)
        chunk = await gen.__anext__()
        await gen.aclose()
        return chunk

    chunk = asyncio.run(_first_frame())
    assert b"RECOVERED" in chunk
