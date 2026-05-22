"""Tests for PR-3D-a (A-7) — helpers extracted from codec_agents.Agent.run.

Behavior-preserving extraction of the 230-LOC ReAct loop into named helpers:
- Agent._parse_action(text)        -> (tool|None, final_text|None)   [pure]
- Agent._validate_tool_call(n, i)  -> rejection message | None        [pure]
- Agent._execute_tool_with_hooks(tool, name, input) -> result str     [async]

The full end-to-end loop stays covered by tests/test_agents_crews.py; these
pin the extracted units. Reference: docs/PR3D-MONOLITH-EXTRACT-DESIGN.md.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from codec_agents import Agent, Tool  # noqa: E402


# ── _parse_action (pure protocol parse) ───────────────────────────────────────


def test_parse_action_tool():
    tool, final = Agent._parse_action("TOOL: web_search\nINPUT: weather today")
    assert tool == ("web_search", "weather today")
    assert final is None


def test_parse_action_final():
    tool, final = Agent._parse_action("FINAL: the answer is 42")
    assert tool is None
    assert final == "the answer is 42"


def test_parse_action_both_parsed():
    # Both are returned; run() applies TOOL-before-FINAL precedence with budget.
    tool, final = Agent._parse_action("TOOL: foo\nINPUT: bar\nFINAL: later")
    assert tool == ("foo", "bar")
    assert final == "later"


def test_parse_action_final_last_occurrence():
    # rsplit -> the LAST FINAL (skips quoted/echoed prompt text).
    _, final = Agent._parse_action("FINAL: quoted\nblah\nFINAL: real answer")
    assert final == "real answer"


def test_parse_action_multiline_input():
    tool, _ = Agent._parse_action("TOOL: writer\nINPUT: line1\nline2\nline3")
    assert tool == ("writer", "line1\nline2\nline3")


def test_parse_action_none():
    assert Agent._parse_action("just prose, no markers") == (None, None)


# ── _validate_tool_call (pure validation) ─────────────────────────────────────


def test_validate_empty_name():
    assert "Empty tool name" in Agent._validate_tool_call("", "x")


def test_validate_long_name():
    msg = Agent._validate_tool_call("a" * 101, "x")
    assert msg and "too long" in msg.lower()


def test_validate_invalid_chars():
    msg = Agent._validate_tool_call("foo;rm -rf", "x")
    assert msg and "invalid characters" in msg


def test_validate_long_input():
    msg = Agent._validate_tool_call("web_search", "y" * 50001)
    assert msg and "too long" in msg.lower()


def test_validate_ok():
    assert Agent._validate_tool_call("web_search", "query") is None


def test_validate_dotted_hyphen_name_ok():
    assert Agent._validate_tool_call("my.tool-name_2", "ok") is None


# ── _execute_tool_with_hooks (executor + run_with_hooks happy path) ────────────


def test_execute_tool_with_hooks_runs_tool():
    tool = Tool(name="echo", description="echoes input", fn=lambda s: f"got:{s}")
    agent = Agent(name="T", role="tester", tools=[tool])
    result = asyncio.run(agent._execute_tool_with_hooks(tool, "echo", "hi"))
    assert result == "got:hi"
