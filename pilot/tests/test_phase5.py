"""
CODEC Pilot Phase 5 — Trace + Compiler + Replay test suite
===========================================================

  1. save_trace / load_trace round-trip — data survives disk serialisation
  2. list_traces — returns summary including saved run
  3. compile_trace — produces valid Python with correct structure
  4. save_script — writes script.py to trace directory
  5. replay_trace — replays navigate+done steps against live browser
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from pilot.pilot_chrome import pilot_session
from pilot.pilot_agent import PilotAgent, AgentRun, AgentStep
from pilot.trace import save_trace, load_trace, list_traces
from pilot.compiler import compile_trace, save_script, replay_trace


# ─── Helper: build a minimal AgentRun without running the browser ─────────────

def _fake_run(run_id: str = "test_phase5_run") -> AgentRun:
    run = AgentRun(
        task="Open example.com and confirm it loaded",
        run_id=run_id,
        status="done",
        result="Page loaded successfully",
        started_at=time.time() - 5,
        ended_at=time.time(),
    )
    run.steps = [
        AgentStep(
            step=1,
            action={"action": "navigate", "url": "https://example.com"},
            snapshot_before="URL: about:blank\nTITLE: \nELEMENTS (0):\n",
            result="navigated to https://example.com",
        ),
        AgentStep(
            step=2,
            action={"action": "done", "result": "Page loaded"},
            snapshot_before="URL: https://example.com/\nTITLE: Example Domain\nELEMENTS (1):\n[1] link \"More information...\"",
            result="task complete",
        ),
    ]
    return run


# ─── Tests ────────────────────────────────────────────────────────────────────

async def test_save_load_roundtrip(_pilot):
    print("│ [1/5] save_trace / load_trace round-trip...")
    run = _fake_run("test_phase5_rt")
    path = save_trace(run)
    assert path.exists(), f"Trace file not created: {path}"

    run2 = load_trace("test_phase5_rt")
    assert run2.run_id    == run.run_id
    assert run2.task      == run.task
    assert run2.status    == run.status
    assert run2.result    == run.result
    assert len(run2.steps) == len(run.steps)
    assert run2.steps[0].action["url"] == "https://example.com"
    print(f"│       ✓ saved to {path.name}, loaded back, {len(run2.steps)} steps intact")


async def test_list_traces(_pilot):
    print("│ [2/5] list_traces — saved run appears in list...")
    save_trace(_fake_run("test_phase5_ls"))
    traces = list_traces()
    ids = [t["run_id"] for t in traces]
    assert "test_phase5_ls" in ids, f"Expected test_phase5_ls in {ids}"
    t = next(t for t in traces if t["run_id"] == "test_phase5_ls")
    assert t["status"] == "done"
    assert t["step_count"] == 2
    print(f"│       ✓ {len(traces)} trace(s) listed, test_phase5_ls found")


async def test_compile_trace(_pilot):
    print("│ [3/5] compile_trace — produces valid Python script...")
    run = _fake_run("test_phase5_ct")
    script = compile_trace(run)

    # Must be valid Python
    compile(script, "<test>", "exec")

    # Must contain key structural elements
    assert "import asyncio" in script
    assert "from pilot.pilot_chrome import pilot_session" in script
    assert "async def run():" in script
    assert "pilot_session" in script
    assert "navigate" in script
    assert "example.com" in script
    assert "# DONE:" in script or "# done" in script.lower()

    print(f"│       ✓ {len(script)} chars, valid Python, navigate+done present")


async def test_save_script(_pilot):
    print("│ [4/5] save_script — writes script.py...")
    run = _fake_run("test_phase5_ss")
    script = compile_trace(run)
    path = save_script("test_phase5_ss", script)
    assert path.exists()
    assert path.name == "script.py"
    content = path.read_text()
    assert "navigate" in content
    print(f"│       ✓ script.py written ({path.stat().st_size} bytes)")


async def test_replay_trace(pilot):
    print("│ [5/5] replay_trace — navigate+done against live browser...")
    run = _fake_run("test_phase5_replay")
    results = await replay_trace(run, pilot)

    assert len(results) >= 2, f"Expected ≥2 results, got {results}"
    assert any("navigated" in r for r in results), f"No navigate result in {results}"
    assert any("DONE" in r for r in results), f"No DONE result in {results}"

    # Browser should now be on example.com
    assert "example.com" in pilot.page.url

    print(f"│       ✓ {len(results)} steps replayed: " + " | ".join(r[:40] for r in results[:3]))


# ─── runner ───────────────────────────────────────────────────────────────────

async def main():
    print("┌─ CODEC Pilot Phase 5 — Trace + Compiler + Replay test ───────")
    print("│ Launching Pilot Chromium (headless=True)...")

    failed = []
    async with pilot_session(headless=True) as pilot:
        tests = [
            test_save_load_roundtrip,
            test_list_traces,
            test_compile_trace,
            test_save_script,
            test_replay_trace,
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
        print(f"└─ ✗ Phase 5 FAILED — {len(failed)} test(s): {', '.join(failed)}")
        sys.exit(1)
    else:
        print("└─ ✓ Phase 5 PASSED — trace/compiler/replay green")


if __name__ == "__main__":
    asyncio.run(main())
