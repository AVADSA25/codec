"""Pilot PP-10 — an autonomous run must NOT perform an irreversible/financial browser
action (click "Pay"/"Place order"/"Delete"/"Transfer"…) unless explicitly opted in.
Closes audit P-7 (HITL default-deny) + P-10 (replay re-executing irreversible actions),
in their default-deny core.

Reference: docs/PP10-DESTRUCTIVE-GUARD-DESIGN.md.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # ~/codec on sys.path

from pilot import pilot_agent  # noqa: E402
from pilot.snapshot import IndexedElement  # noqa: E402


def _el(role="button", name=""):
    return IndexedElement(index=1, role=role, name=name, xpath="//x",
                          css_sel="x", bbox={}, attrs={})


def test_classify_flags_financial_and_delete_clicks():
    for name in ("Place order", "Pay now", "Complete purchase", "Delete account",
                 "Transfer funds", "Confirm payment", "Withdraw"):
        assert pilot_agent.classify_destructive({"action": "click", "index": 1}, _el(name=name)), name


def test_classify_ignores_benign_clicks_and_non_clicks():
    assert not pilot_agent.classify_destructive({"action": "click", "index": 1}, _el(name="Read more"))
    assert not pilot_agent.classify_destructive({"action": "click", "index": 1}, _el(role="link", name="Home"))
    # non-click actions aren't classified destructive here (navigate=SSRF-gated, type=secret-gated)
    assert not pilot_agent.classify_destructive({"action": "type", "index": 1}, _el(name="Pay"))


def test_guard_blocks_destructive_by_default(monkeypatch):
    monkeypatch.setattr(pilot_agent, "_destructive_allowed", lambda: False)
    with pytest.raises(pilot_agent.DestructiveActionBlocked):
        pilot_agent.guard_action({"action": "click", "index": 1}, _el(name="Place order"))


def test_guard_allows_destructive_when_opted_in(monkeypatch):
    monkeypatch.setattr(pilot_agent, "_destructive_allowed", lambda: True)
    pilot_agent.guard_action({"action": "click", "index": 1}, _el(name="Place order"))  # no raise


def test_guard_allows_benign_click(monkeypatch):
    monkeypatch.setattr(pilot_agent, "_destructive_allowed", lambda: False)
    pilot_agent.guard_action({"action": "click", "index": 1}, _el(name="Next page"))  # no raise
