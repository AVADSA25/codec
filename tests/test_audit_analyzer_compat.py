"""Tests that codec_audit_analyzer.run / .analyze tolerates both the unified
schema:1 envelope and pre-merge legacy entries (no schema, no event field).

Migration plan (§3) is leave-as-is + age-out: the analyzer must produce
identical-shape report dicts whether fed unified or legacy entries.
"""
from __future__ import annotations

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
import codec_audit_analyzer as analyzer


def _unified(tool: str, *, event: str, outcome: str = "ok",
             duration_ms: float | None = 10.0,
             client_id: str | None = None) -> dict:
    return {
        "ts": "2026-04-30T10:00:00.000+00:00",
        "schema": 1,
        "event": event,
        "source": "codec-mcp-http",
        "tool": tool,
        "task_len": 0,
        "context_len": 0,
        "duration_ms": duration_ms,
        "outcome": outcome,
        "error_type": None,
        "client_id": client_id,
        "transport": "http",
    }


def _legacy_crew(tool: str = "google_docs_create", *, event: str = "tool_call",
                 outcome: str | None = None) -> dict:
    """Pre-merge codec_agents._audit shape — has `event`, no `schema`,
    no `outcome` for crew_start/crew_complete."""
    rec = {
        "ts": "2026-04-30T09:00:00",  # naive local — also a legacy quirk
        "event": event,
        "tool": tool,
        "agent": "Writer",
    }
    if outcome is not None:
        rec["outcome"] = outcome
    return rec


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


def _run_analyzer(records: list[dict]):
    """Pass records straight through analyzer.analyze() — the dict-returning API."""
    return analyzer.analyze(records)


def test_analyzer_handles_unified_entries():
    records = [
        _unified("weather", event="tool_result", outcome="ok", duration_ms=42),
        _unified("weather", event="tool_result", outcome="error", duration_ms=10),
        _unified("notes", event="tool_result", outcome="ok", duration_ms=22),
    ]
    out = _run_analyzer(records)
    assert out["total"] == 3
    assert out["errors"] == 1
    tool_names = [name for (name, _count) in out["top_used"]]
    assert "weather" in tool_names
    assert "notes" in tool_names


def test_analyzer_handles_legacy_crew_entries():
    """Legacy entries with no `outcome` don't count as errors and don't crash."""
    records = [
        _legacy_crew(event="crew_start"),
        _legacy_crew(event="tool_call", tool="google_docs_create"),
        _legacy_crew(event="crew_complete"),
    ]
    out = _run_analyzer(records)
    assert out["total"] == 3
    assert out["errors"] == 0


def test_analyzer_mixed_unified_and_legacy():
    """A 50/50 mix totals correctly and computes error rate over what's marked."""
    records = [
        _unified("weather", event="tool_result", outcome="ok", duration_ms=10),
        _unified("weather", event="tool_result", outcome="error", duration_ms=10),
        _legacy_crew(event="tool_call"),
        _legacy_crew(event="crew_complete"),
    ]
    out = _run_analyzer(records)
    assert out["total"] == 4
    # Only the unified-error record is counted as an error.
    assert out["errors"] == 1


def test_analyzer_unique_clients_still_works():
    records = [
        _unified("weather", event="tool_result", client_id="claude-ai"),
        _unified("weather", event="tool_result", client_id="claude-desktop"),
        _unified("notes", event="tool_result", client_id="claude-ai"),
    ]
    out = _run_analyzer(records)
    assert "unique_clients" in out
    assert out["unique_clients"] == 2
