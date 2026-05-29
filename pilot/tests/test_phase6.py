"""
CODEC Pilot Phase 6 — HITL Takeover test suite
===============================================

All tests use StubLLM (no real Qwen needed).

  1. pause/resume  — agent pauses at step boundary, resumes on signal
  2. inject action — human-injected navigate executes before LLM resumes
  3. takeover/handback — state flags set correctly
  4. status() dict — serialisable HITL state
  5. full run with pause/inject/resume — AgentRun records injected step
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from pilot.pilot_chrome import pilot_session
from pilot.hitl import HitlController


# ─── Tests ────────────────────────────────────────────────────────────────────

async def test_pause_resume(pilot):
    print("│ [1/5] pause/resume — agent pauses at step boundary...")
    ctrl = HitlController(
        pilot, task="Go to https://example.com",
        step_budget=5, use_stub=True,
    )

    # Pause immediately before starting
    await ctrl.pause("test pause")
    assert ctrl.state.paused is True
    assert ctrl._pause_event.is_set() is False

    # Resume after 50ms
    async def _resume_later():
        await asyncio.sleep(0.05)
        await ctrl.resume()

    asyncio.create_task(_resume_later())
    t0 = time.time()
    run = await ctrl.execute()
    elapsed = time.time() - t0

    assert elapsed >= 0.04, f"Agent didn't wait for resume (elapsed={elapsed:.3f}s)"
    assert ctrl.state.paused is False
    assert run.status in ("done", "budget_exhausted")
    print(f"│       ✓ paused, resumed after {elapsed*1000:.0f}ms, status={run.status}")


async def test_inject_action(pilot):
    print("│ [2/5] inject action — human navigate executes before LLM step...")
    await pilot.navigate("about:blank")

    ctrl = HitlController(
        pilot, task="read the page",
        step_budget=10, use_stub=True,
    )

    # Pause, inject a navigate, then resume
    await ctrl.pause("inject test")
    await ctrl.inject({"action": "navigate", "url": "https://example.com"})
    await ctrl.resume()

    run = await ctrl.execute()

    # The injected navigate should appear in steps
    injected = [s for s in run.steps if s.action.get("_injected")]
    assert len(injected) >= 1, f"No injected steps found in {[s.action for s in run.steps]}"
    assert injected[0].action.get("action") == "navigate"
    assert "example.com" in injected[0].action.get("url", "")
    assert len(ctrl.state.injected_steps) >= 1

    print(f"│       ✓ injected navigate found, {len(injected)} injected step(s)")


async def test_takeover_handback(pilot):
    print("│ [3/5] takeover/handback — state flags correct...")
    ctrl = HitlController(pilot, task="test", use_stub=True)

    snap_text = await ctrl.takeover()
    assert ctrl.state.paused is True
    assert ctrl.state.human_in_control is True
    assert ctrl.state.pause_reason == "human takeover"
    assert "URL:" in snap_text     # returns render_for_llm output

    await ctrl.handback()
    assert ctrl.state.paused is False
    assert ctrl.state.human_in_control is False
    assert ctrl.state.resumed_at is not None

    print(f"│       ✓ takeover=True, handback → human_in_control=False, snapshot len={len(snap_text)}")


async def test_status_dict(pilot):
    print("│ [4/5] status() dict — serialisable HITL state...")
    ctrl = HitlController(pilot, task="test", run_id="test_hitl_status", use_stub=True)
    s = ctrl.status()

    assert s["run_id"] == "test_hitl_status"
    assert isinstance(s["paused"], bool)
    assert isinstance(s["human_in_control"], bool)
    assert isinstance(s["inject_queue_size"], int)
    assert isinstance(s["injected_steps"], int)

    await ctrl.pause("checking status")
    s2 = ctrl.status()
    assert s2["paused"] is True
    assert s2["pause_reason"] == "checking status"
    await ctrl.resume()

    print(f"│       ✓ status dict keys present, paused/resume cycle verified")


async def test_full_hitl_run(pilot):
    print("│ [5/5] full run with pause/inject/resume — injected in AgentRun...")
    await pilot.navigate("about:blank")

    ctrl = HitlController(
        pilot,
        task="Go to https://example.com and summarise",
        step_budget=10,
        use_stub=True,
        run_id="test_hitl_full",
    )

    # Pre-pause so execute() must wait, inject a step, then resume from a task.
    # This is deterministic — no race condition with StubLLM completing in <20ms.
    await ctrl.pause("pre-execute human setup")
    await ctrl.inject({"action": "navigate", "url": "https://example.com"})

    async def _resume_after_inject():
        await asyncio.sleep(0.03)   # give inject queue time to be picked up
        await ctrl.resume()

    asyncio.create_task(_resume_after_inject())
    run = await ctrl.execute()

    assert len(ctrl.state.injected_steps) >= 1, "No injected steps recorded"
    assert run.status in ("done", "budget_exhausted")

    injected_in_run = [s for s in run.steps if s.action.get("_injected")]
    assert len(injected_in_run) >= 1, (
        f"Injected step not in AgentRun.steps. Steps: {[s.action for s in run.steps]}"
    )

    print(f"│       ✓ full run complete, {len(run.steps)} total steps, "
          f"{len(injected_in_run)} injected, status={run.status}")


# ─── runner ───────────────────────────────────────────────────────────────────

async def main():
    print("┌─ CODEC Pilot Phase 6 — HITL Takeover test ───────────────────")
    print("│ Launching Pilot Chromium (headless=True)...")

    failed = []
    async with pilot_session(headless=True) as pilot:
        tests = [
            test_pause_resume,
            test_inject_action,
            test_takeover_handback,
            test_status_dict,
            test_full_hitl_run,
        ]
        for test_fn in tests:
            try:
                await test_fn(pilot)
            except AssertionError as exc:
                name = test_fn.__name__
                print(f"│       ✗ FAIL {name}: {exc}")
                failed.append(name)
            except Exception as exc:
                import traceback
                name = test_fn.__name__
                print(f"│       ✗ ERROR {name}: {type(exc).__name__}: {exc}")
                traceback.print_exc()
                failed.append(name)

    print("│")
    if failed:
        print(f"└─ ✗ Phase 6 FAILED — {len(failed)} test(s): {', '.join(failed)}")
        sys.exit(1)
    else:
        print("└─ ✓ Phase 6 PASSED — HITL takeover green")


if __name__ == "__main__":
    asyncio.run(main())
