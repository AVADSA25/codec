"""Tests for PR-7P (Audit B / B-2 remainder) — permission_gate derives each skill's
resource use from a central SKILL_CAPABILITIES table and OR-upgrades the LLM's
self-declared flags, so a write/network-capable skill can't skip its gate by emitting
touches_path=false / network_call=false. Benign read-only public-data skills (weather) are
intentionally not network-gated.

Reference: docs/PR7P-CAPABILITY-AUTHZ-DESIGN.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path[:] = [p for p in sys.path if p != str(REPO)]
sys.path.insert(0, str(REPO))

import codec_agent_runner as car  # noqa: E402

_GG = {"schema": 1, "version": 0, "skills": [], "read_paths": [],
       "write_paths": [], "network_domains": []}


def _grants(**over):
    g = {"skills": [], "read_paths": [], "write_paths": [], "network_domains": []}
    g.update(over)
    return g


def test_write_capable_skill_gated_despite_false_flag():
    """file_write is write-capable in the table → its write gate fires even when the LLM
    declared touches_path=False (the bypass)."""
    action = car.Action(skill="file_write", task="w", kind="skill_call",
                        touches_path=False, path="/etc/shadow")
    with pytest.raises(car.PermissionViolation):
        car.permission_gate(action, _grants(skills=["file_write"], write_paths=[]), _GG)


def test_network_capable_skill_gated_despite_false_flag():
    """web_fetch is network-capable → its domain gate fires even when network_call=False."""
    action = car.Action(skill="web_fetch", task="x", kind="skill_call",
                        network_call=False, network_domain="evil.example.com")
    with pytest.raises(car.PermissionViolation):
        car.permission_gate(action, _grants(skills=["web_fetch"], network_domains=[]), _GG)


def test_unclassified_skill_unaffected():
    """A no-caps skill with all flags False passes (no behavior change)."""
    action = car.Action(skill="calculator", task="2+2", kind="skill_call")
    car.permission_gate(action, _grants(skills=["calculator"]), _GG)  # must not raise


def test_benign_read_skill_not_network_gated():
    """weather is intentionally NOT network-gated (benign read-only public data) — keeps it
    usable without a domain grant."""
    action = car.Action(skill="weather", task="Paris", kind="skill_call", network_call=False)
    car.permission_gate(action, _grants(skills=["weather"], network_domains=[]), _GG)  # no raise


def test_capabilities_table_covers_dangerous_skills():
    """Guard against an empty/forgotten table: the high-risk resource skills are classified."""
    assert "writes_path" in car._skill_capabilities("file_write")
    assert "network" in car._skill_capabilities("web_fetch")
    for s in ("terminal", "python_exec", "file_ops"):
        assert car._skill_capabilities(s), f"{s} must be classified in SKILL_CAPABILITIES"
    assert car._skill_capabilities("calculator") == set(), "benign compute skill has no caps"
