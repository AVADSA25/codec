"""
CODEC Pilot Phase 4 — Agent Loop test suite
============================================

Uses StubLLM (offline, no Qwen needed) to verify the ReAct loop machinery:

  1. navigate action — agent navigates to example.com
  2. done action — agent marks task complete
  3. budget exhaustion — agent hits step limit correctly
  4. error action — agent returns error status
  5. full stub run — navigate + done in 2 steps, AgentRun populated correctly
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from pilot.pilot_chrome import pilot_session
from pilot.pilot_agent import PilotAgent, AgentRun


# ─── Test helpers ─────────────────────────────────────────────────────────────

def _run_agent(pilot, task: str, budget: int = 10, stub: bool = True) -> AgentRun:
    """Synchronous helper — runs agent in current event loop."""
    agent = PilotAgent(
        pilot,
        task=task,
        step_budget=budget,
        use_stub=stub,
        record_screencast=False,
    )
    return asyncio.get_event_loop().run_until_complete(agent.execute())


# ─── Tests ────────────────────────────────────────────────────────────────────

async def test_navigate_action(pilot):
    print("│ [1/5] navigate action — StubLLM navigates to URL in task...")
    agent = PilotAgent(
        pilot,
        task="Go to https://example.com and read the page",
        step_budget=5,
        use_stub=True,
        record_screencast=False,
    )
    run = await agent.execute()
    assert run.status in ("done", "budget_exhausted"), f"Unexpected status: {run.status}"
    assert len(run.steps) >= 1, "Expected ≥1 step"
    # First step should be a navigate
    first = run.steps[0]
    assert first.action.get("action") == "navigate", (
        f"Expected first action=navigate, got {first.action}"
    )
    assert "example.com" in first.action.get("url", "")
    print(f"│       ✓ first action=navigate, url=example.com, {len(run.steps)} steps")


async def test_done_action(pilot):
    print("│ [2/5] done action — StubLLM completes task, status=done...")
    # Navigate first so we have a real page, then run an agent with a non-URL task
    # StubLLM will skip navigate (no URL in task) and return done on step 1
    await pilot.navigate("https://example.com")
    agent = PilotAgent(
        pilot,
        task="Tell me the title of the current page",
        step_budget=5,
        use_stub=True,
        record_screencast=False,
    )
    run = await agent.execute()
    assert run.status == "done", f"Expected done, got {run.status}"
    assert run.result is not None
    last = run.steps[-1]
    assert last.action.get("action") == "done"
    print(f"│       ✓ status=done, result='{run.result[:50]}'")


async def test_budget_exhaustion(pilot):
    print("│ [3/5] budget exhaustion — budget=1 exhausted correctly...")
    await pilot.navigate("https://example.com")

    # Custom stub that always returns a scroll (never done) to exhaust budget
    from pilot.pilot_agent import PilotAgent, StubLLM

    class LoopStub(StubLLM):
        async def next_action(self, snapshot_text):
            return {"action": "scroll", "direction": "down", "amount": 100}

    agent = PilotAgent(
        pilot,
        task="scroll forever",
        step_budget=2,
        use_stub=True,
        record_screencast=False,
    )
    agent._use_stub = False   # bypass normal stub flag
    # Inject our loop stub by monkey-patching _call_llm in the execute call
    import pilot.pilot_agent as pa_mod
    _orig = pa_mod._call_llm

    call_count = {"n": 0}
    async def _mock_llm(messages):
        call_count["n"] += 1
        return '{"action":"scroll","direction":"down","amount":100}'
    pa_mod._call_llm = _mock_llm
    try:
        run = await agent.execute()
    finally:
        pa_mod._call_llm = _orig

    assert run.status == "budget_exhausted", f"Expected budget_exhausted, got {run.status}"
    assert len(run.steps) == 2, f"Expected 2 steps, got {len(run.steps)}"
    print(f"│       ✓ status=budget_exhausted after {len(run.steps)} steps")


async def test_error_action(pilot):
    print("│ [4/5] error action — agent returns error status...")
    await pilot.navigate("https://example.com")
    import pilot.pilot_agent as pa_mod
    _orig = pa_mod._call_llm
    async def _mock_error(messages):
        return '{"action":"error","reason":"element not found"}'
    pa_mod._call_llm = _mock_error
    try:
        agent = PilotAgent(
            pilot, task="find invisible element",
            step_budget=5, use_stub=False, record_screencast=False,
        )
        run = await agent.execute()
    finally:
        pa_mod._call_llm = _orig

    assert run.status == "error", f"Expected error, got {run.status}"
    assert "not found" in (run.error or "")
    print(f"│       ✓ status=error, reason='{run.error}'")


async def test_full_stub_run(pilot):
    print("│ [5/5] full stub run — navigate→done, AgentRun populated...")
    agent = PilotAgent(
        pilot,
        task="Go to https://example.com and confirm the page title",
        step_budget=10,
        use_stub=True,
        record_screencast=False,
        run_id="test_phase4_full",
    )
    run = await agent.execute()

    assert run.run_id == "test_phase4_full"
    assert run.task == "Go to https://example.com and confirm the page title"
    assert run.status in ("done", "budget_exhausted")
    assert run.ended_at is not None
    assert run.ended_at > run.started_at
    assert len(run.steps) >= 1

    d = run.to_dict()
    assert d["run_id"] == "test_phase4_full"
    assert isinstance(d["steps"], list)

    print(f"│       ✓ run_id=test_phase4_full, {len(run.steps)} steps, "
          f"status={run.status}, to_dict ✓")


# ─── runner ───────────────────────────────────────────────────────────────────

async def main():
    print("┌─ CODEC Pilot Phase 4 — Agent Loop test ──────────────────────")
    print("│ Launching Pilot Chromium (headless=True)...")

    failed = []
    async with pilot_session(headless=True) as pilot:
        tests = [
            test_navigate_action,
            test_done_action,
            test_budget_exhaustion,
            test_error_action,
            test_full_stub_run,
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
        print(f"└─ ✗ Phase 4 FAILED — {len(failed)} test(s): {', '.join(failed)}")
        sys.exit(1)
    else:
        print("└─ ✓ Phase 4 PASSED — agent loop machinery green")


if __name__ == "__main__":
    asyncio.run(main())
