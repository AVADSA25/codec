# PP-12 — Async robustness (unbounded waits)

**Closes:** Pilot audit **P-14** (async robustness) — its two unbounded-wait cases.
**Repo:** `~/codec/`.

## What & Fix
Two loops could pin the shared browser + a run slot (PP-7's `_MAX_RUNS`) forever:

### 1. HITL pause gate (`hitl.py`)
`execute()`'s loop opened each step with `await self._pause_event.wait()`. If the operator
paused and never resumed (tab closed, network drop, walked away), the agent coroutine blocked
forever — holding the browser and a run slot with no recovery.

- New `HitlController(pause_timeout_s=None)` — `None` → `config.HITL_PAUSE_TIMEOUT_S` (default
  600s, matching the parent `ask_user` default).
- New `_await_resume_or_timeout(run)` helper wraps the wait in `asyncio.wait_for`. Returns True
  if the agent may proceed (not paused, or resumed in time); on `TimeoutError` it finalizes the
  run as `status="paused_timeout"` and returns False. The loop returns the run, freeing the
  browser + run slot.
- The gate is the first thing in the loop (before the snapshot), so an abandoned pause times out
  without ever touching the page.

### 2. MJPEG stream (`pilot_runner.py`)
`/screenshot/stream`'s `while True` caught every `screenshot()` exception with a bare `except`
and slept 0.25s — a dead browser produced no frames but spun the loop forever, never closing the
stream.

- Extracted module-level `_mjpeg_frames(get_pilot, *, max_consecutive_failures=None, sleep_s)`.
- After `config.MJPEG_MAX_CONSECUTIVE_FAILURES` (default 20 ≈ 5s) consecutive screenshot
  failures, the generator returns — closing the stream so the client reconnects against a healthy
  state. A successful frame resets the counter, so transient blips don't tear down a working feed.
- A `None` pilot (stream opened before a run starts) stays a benign wait — it doesn't count
  toward the failure bound (preserves the old "open the feed early" affordance).

## Tests (`tests/test_phase18_async_robustness.py`)
HITL: gate True when unpaused / resumed-in-time, False+`paused_timeout` on expiry, and `execute()`
returns `paused_timeout` end-to-end. MJPEG: closes after N consecutive failures, yields frames
when healthy, recovers from a transient failure below the bound. 8 tests (sync, drive async via
`asyncio.run`); native `test_phase6` `test_pause_resume` still green against real chromium.
