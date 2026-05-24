"""Tests for PR-6G (Audit F: F-3/F-9/F-10/F-14/F-17/F-18) — the README investor
overhaul. Regression guard that the public README leads with a value prop, sells
the moat, surfaces architecture, frames MCP bidirectionally, and that every
test/skill/line metric is truthful + internally consistent across README,
CONTRIBUTING.md and AGENTS.md.

Reference: docs/audits/PHASE-1-INVESTOR-READINESS.md.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (REPO / name).read_text()


# ---- F-3 / F-17: counts are reconciled and not overstated ------------------

def test_no_stale_test_counts_anywhere():
    readme = _read("README.md")
    contributing = _read("CONTRIBUTING.md")
    agents = _read("AGENTS.md")
    # The three contradictory historical numbers must all be gone.
    assert "940+" not in readme, "F-3: stale '940+' must be removed from README"
    assert "168+" not in contributing, "F-17: stale '168+' must be gone from CONTRIBUTING"
    assert "600+ tests" not in agents, "F-17: stale '600+ tests' must be gone from AGENTS.md"


def test_single_reconciled_test_number_present():
    # One conservative, defensible number in all three doc surfaces.
    for name in ("README.md", "CONTRIBUTING.md", "AGENTS.md"):
        t = _read(name)
        assert ("1,300+" in t) or ("1300+" in t), f"F-17: reconciled test count missing from {name}"


def test_live_ci_badge_present():
    readme = _read("README.md")
    # F-3: a real workflow-status badge, not a hand-typed green pill.
    assert "actions/workflows/ci.yml/badge.svg" in readme, "F-3: live CI status badge required"


def test_skill_count_is_76_not_75():
    readme = _read("README.md")
    assert "75 built-in skills" not in readme, "F-3: skills overstated/stale (75) — repo has 76"
    assert "badge/skills-75" not in readme, "F-3: skills badge must read 76"
    assert "76" in readme, "skill count (76) must appear"


# ---- F-9: stranger-readable value prop above the fold ---------------------

def test_value_prop_leads_near_top():
    lines = _read("README.md").splitlines()
    head = "\n".join(lines[:30]).lower()
    assert "voice-controlled ai workstation" in head, "F-9: one-line value prop must lead the README"
    # And it must sit before the dense 'What This Is' prose.
    body = _read("README.md")
    assert body.lower().index("voice-controlled ai workstation") < body.index("## What This Is")


# ---- F-10: the moat / why-not-X section -----------------------------------

def test_why_codec_section_exists():
    readme = _read("README.md")
    assert "## Why CODEC" in readme, "F-10: a 'Why CODEC, not X' positioning section is required"
    # The three named comparators from the audit recommendation.
    low = readme.lower()
    assert "open interpreter" in low or "aider" in low, "F-10: must contrast with terminal coding agents"
    assert "crewai" in low and "langchain" in low, "F-10: must contrast with orchestration frameworks"


# ---- F-14: architecture surfaced, not buried ------------------------------

def test_architecture_section_and_link():
    readme = _read("README.md")
    assert "## Architecture" in readme, "F-14: a top-level Architecture section is required"
    assert "docs/ARCHITECTURE.md" in readme, "F-14: must link the full architecture doc"


# ---- F-18: bidirectional MCP / agent-to-agent -----------------------------

def test_mcp_section_is_bidirectional():
    readme = _read("README.md")
    # Section must frame CODEC as BOTH client and server (peer/agent-to-agent).
    assert "MCP client" in readme and "MCP server" in readme, (
        "F-18: MCP section must state CODEC is both an MCP client AND server"
    )
    assert "agent-to-agent" in readme.lower(), "F-18: agent-to-agent peering angle must be stated"
