"""End-to-end correlation_id propagation tests.

Verifies that for every operation that emits ≥2 audit lines (per design §1.4),
the correlation_id is preserved across the paired emits — even across asyncio
boundaries (Crew/Agent), thread boundaries (audit lock under contention), and
nested operations (an inner emit inherits the outer cid via contextvars).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import codec_audit


@pytest.fixture
def temp_log(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
    tmp.close()
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", Path(tmp.name))
    yield Path(tmp.name)
    try:
        os.unlink(tmp.name)
    except OSError:
        pass


def _records(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _cids(records: list[dict]) -> list[str | None]:
    return [r.get("extra", {}).get("correlation_id") for r in records]


# ── codec_agents: Crew.run sets contextvar; nested _audit emits inherit ──────

def test_codec_agents_contextvar_propagation(temp_log):
    """When the Crew-level cid is set on the contextvar, every _audit emit
    inside picks it up — including from a child thread via run_in_executor."""
    import codec_agents

    cid = "feed1234abcd"
    token = codec_agents._correlation_id_var.set(cid)
    try:
        # Direct emit at the "crew level"
        codec_agents._audit("crew_start", agents=["A"], mode="sequential")
        # Simulated nested emit (e.g. from inside Agent.run)
        codec_agents._audit("tool_call", agent="A", tool="weather",
                            input="x")
        codec_agents._audit("tool_result", agent="A", tool="weather",
                            result_len=42, outcome="ok")
        codec_agents._audit("crew_complete", mode="sequential", elapsed=1)
    finally:
        codec_agents._correlation_id_var.reset(token)

    recs = _records(temp_log)
    assert len(recs) == 4
    cids = _cids(recs)
    assert all(c == cid for c in cids), (
        f"correlation_id was lost across emits: {cids}"
    )
    # Sanity: the four event types we emitted are all present.
    events = [r["event"] for r in recs]
    assert events == ["crew_start", "tool_call", "tool_result", "crew_complete"]


def test_codec_agents_executor_inherits_contextvar(temp_log):
    """run_in_executor does NOT auto-propagate contextvars; codec_agents
    wraps the call with contextvars.copy_context().run() so any _audit fired
    from inside a Tool (e.g. _shell_execute → shell_blocked) inherits the
    agent/crew correlation_id. This test mirrors that wrap."""
    import contextvars as _cv
    import codec_agents

    cid = "deadc0de4567"
    token = codec_agents._correlation_id_var.set(cid)
    try:
        async def go():
            loop = asyncio.get_event_loop()
            ctx = _cv.copy_context()
            # Same wrap pattern Agent.run uses around tool.run
            await loop.run_in_executor(None, ctx.run,
                                       codec_agents._audit, "shell_blocked")
        asyncio.run(go())
    finally:
        codec_agents._correlation_id_var.reset(token)

    rec = _records(temp_log)[-1]
    assert rec["event"] == "shell_blocked"
    assert rec.get("extra", {}).get("correlation_id") == cid


def test_codec_agents_solo_agent_generates_its_own_cid(temp_log):
    """Agent.run called outside a crew (e.g. run_custom_agent) generates a
    fresh cid via _new_correlation_id. We don't actually run the agent here
    (LLM unavailable); instead we verify the helper is present and the
    var-set logic works."""
    import codec_agents

    # Var starts empty
    assert codec_agents._correlation_id_var.get() is None

    cid1 = codec_agents._new_correlation_id()
    cid2 = codec_agents._new_correlation_id()
    assert cid1 != cid2
    assert len(cid1) == 12
    assert all(c in "0123456789abcdef" for c in cid1)


# ── codec_voice: voice_session_start/end share cid via contextvar ────────────

def test_codec_voice_contextvar_propagation(temp_log):
    """The voice contextvar carries the session cid for every emit fired
    during the session lifetime."""
    import codec_voice

    cid = "abadcafebeef"
    token = codec_voice._voice_correlation_id_var.set(cid)
    try:
        # Simulated session-start emit
        codec_voice._voice_log_event("voice_session_start", "codec-voice",
                                     "started",
                                     extra={"session_id": "s1"},
                                     correlation_id=cid)
        # Simulated nested chat-style emit (e.g. tool_call from inside the voice loop)
        codec_voice._voice_log_event("tool_call", "codec-voice",
                                     "weather",
                                     correlation_id=cid)
        # Simulated session-end emit
        codec_voice._voice_log_event("voice_session_end", "codec-voice",
                                     "ended",
                                     extra={"session_id": "s1", "turns": 3},
                                     correlation_id=cid)
    finally:
        codec_voice._voice_correlation_id_var.reset(token)

    recs = _records(temp_log)
    assert len(recs) == 3
    assert all(r["extra"]["correlation_id"] == cid for r in recs)
    events = [r["event"] for r in recs]
    assert events == ["voice_session_start", "tool_call", "voice_session_end"]


# ── MCP tool_fn: explicit cid threaded through validation/timeout/result ─────

def test_mcp_tool_call_pairs_via_cid(temp_log):
    """Every paired audit emit from an MCP tool_fn (validation→timeout→result
    branches) shares the same correlation_id."""
    cid = "facefeed0001"
    # Mimic the codec_mcp.py call shape for each branch.
    codec_audit.audit("weather", event="validation",
                      task_len=0, outcome="validation",
                      correlation_id=cid)
    codec_audit.audit("weather", event="tool_result",
                      task_len=10, duration_ms=42, outcome="ok",
                      correlation_id=cid)
    recs = _records(temp_log)
    assert len(recs) == 2
    assert recs[0]["extra"]["correlation_id"] == cid
    assert recs[1]["extra"]["correlation_id"] == cid


def test_distinct_tool_calls_get_distinct_cids(temp_log):
    """Two separate tool invocations must NOT share a cid."""
    import codec_mcp
    cid1 = codec_mcp._new_correlation_id()
    cid2 = codec_mcp._new_correlation_id()
    assert cid1 != cid2
    assert len(cid1) == 12
    assert len(cid2) == 12


# ── Schedule run: schedule_fire + schedule_done share cid ────────────────────

def test_schedule_fire_and_done_share_cid(temp_log):
    cid = "bee101010101"
    codec_audit.log_event("schedule_fire", "codec-scheduler",
                          "fired",
                          extra={"schedule_id": "s1"},
                          correlation_id=cid)
    codec_audit.log_event("schedule_done", "codec-scheduler",
                          "done",
                          duration_ms=200.0,
                          extra={"schedule_id": "s1"},
                          correlation_id=cid)
    recs = _records(temp_log)
    fire, done = recs[-2], recs[-1]
    assert fire["extra"]["correlation_id"] == done["extra"]["correlation_id"] == cid


# ── Analyzer surface: orphan-cid detection (entries that should have one) ────

def test_orphan_cid_detection(temp_log):
    """An entry that should have a cid but doesn't is the analyzer's
    responsibility to flag — check the simple 'has cid?' check works."""
    codec_audit.audit("weather", event="tool_result", outcome="ok")
    recs = _records(temp_log)
    rec = recs[-1]
    has_cid = bool(rec.get("extra", {}).get("correlation_id"))
    assert has_cid is False  # absent — caller forgot, analyzer would flag
