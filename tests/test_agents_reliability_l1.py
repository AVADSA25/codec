"""L1 — regression tests for codec_agents.py reliability fixes (review sweep).

  - parallel crews degrade gracefully (one agent's exception no longer sinks all)
  - a hanging tool is bounded by a per-tool wall-clock budget (no infinite hang)
  - the Crew allowlist still strips out-of-allowlist tools (tautology simplification)
  - the dead eager SERPER_API_KEY module global is gone
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import codec_agents  # noqa: E402
from codec_agents import Agent, Crew, Tool  # noqa: E402


# ── parallel crew graceful degradation ─────────────────────────────────────
def test_parallel_crew_survives_one_agent_exception():
    good = Agent(name="Good", role="r", tools=[])
    bad = Agent(name="Bad", role="r", tools=[])

    async def _good_run(task, context="", callback=None):
        return "good-result"

    async def _bad_run(task, context="", callback=None):
        raise RuntimeError("boom")

    good.run = _good_run
    bad.run = _bad_run

    crew = Crew(agents=[good, bad], tasks=["t1", "t2"], mode="parallel")
    final = asyncio.run(crew.run())

    # the good agent's result survives; the bad one becomes an error marker
    assert "good-result" in final
    assert "Bad failed" in final
    assert "RuntimeError" in final


def test_parallel_crew_all_succeed_still_joins():
    a = Agent(name="A", role="r", tools=[])
    b = Agent(name="B", role="r", tools=[])

    async def _ra(task, context="", callback=None):
        return "ra"

    async def _rb(task, context="", callback=None):
        return "rb"

    a.run, b.run = _ra, _rb
    crew = Crew(agents=[a, b], tasks=["t", "t"], mode="parallel")
    final = asyncio.run(crew.run())
    assert "ra" in final and "rb" in final
    assert "failed" not in final


# ── per-tool timeout ───────────────────────────────────────────────────────
def test_hanging_tool_is_bounded(monkeypatch):
    # shrink the budget so the test is fast; the tool blocks longer than that
    monkeypatch.setattr(codec_agents, "_TOOL_CALL_TIMEOUT_SECONDS", 0.3)

    def _slow(_s):
        time.sleep(2.0)   # exceeds the 0.3s budget
        return "never"

    tool = Tool(name="slow", description="blocks", fn=_slow)
    agent = Agent(name="T", role="r", tools=[tool])

    # Measure the COROUTINE's own return time, not asyncio.run() — the latter
    # also waits for the abandoned (non-killable) executor thread to finish its
    # 2s sleep at loop teardown. The fix's contract is that the *agent* regains
    # control at the budget, which is what run_until_complete returning proves.
    loop = asyncio.new_event_loop()
    try:
        t0 = time.time()
        out = loop.run_until_complete(agent._execute_tool_with_hooks(tool, "slow", "x"))
        elapsed = time.time() - t0  # captured BEFORE loop.close() joins the thread
    finally:
        loop.close()

    assert "timed out" in out.lower(), f"expected timeout message, got: {out!r}"
    assert elapsed < 1.5, f"wait_for did not bound the hang (took {elapsed:.1f}s)"


def test_fast_tool_returns_normally(monkeypatch):
    monkeypatch.setattr(codec_agents, "_TOOL_CALL_TIMEOUT_SECONDS", 5)
    tool = Tool(name="echo", description="echo", fn=lambda s: f"got:{s}")
    agent = Agent(name="T", role="r", tools=[tool])
    out = asyncio.run(agent._execute_tool_with_hooks(tool, "echo", "hi"))
    assert out == "got:hi"


# ── Crew allowlist still strips (tautology simplification) ──────────────────
def test_crew_allowlist_strips_out_of_scope_tools():
    keep = Tool(name="keep", description="", fn=lambda s: s)
    drop = Tool(name="drop", description="", fn=lambda s: s)
    agent = Agent(name="A", role="r", tools=[keep, drop])
    Crew(agents=[agent], tasks=["t"], allowed_tools=["keep"])
    names = {t.name for t in agent.tools}
    assert names == {"keep"}, f"allowlist scoping broken: {names}"


def test_crew_no_allowlist_keeps_all_tools():
    a_tool = Tool(name="a", description="", fn=lambda s: s)
    b_tool = Tool(name="b", description="", fn=lambda s: s)
    agent = Agent(name="A", role="r", tools=[a_tool, b_tool])
    Crew(agents=[agent], tasks=["t"], allowed_tools=None)
    assert {t.name for t in agent.tools} == {"a", "b"}


# ── dead global removed ─────────────────────────────────────────────────────
def test_dead_serper_global_removed():
    assert not hasattr(codec_agents, "SERPER_API_KEY"), (
        "the eager SERPER_API_KEY module global should be gone (it was unused "
        "and did a Keychain shellout at import)"
    )
    # the live getter stays
    assert hasattr(codec_agents, "_serper_api_key")
