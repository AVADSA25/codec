"""Tests for PR-6H (Audit F / F-13) — docs/ONE-PAGER.md, the investor-facing
one-pager. Regression guard that the doc exists and carries the standard
problem→solution→why-now→market→traction→team→ask narrative an investor /
enterprise reader expects, and that the moat (local-first + MCP) is named.

Reference: docs/audits/PHASE-1-INVESTOR-READINESS.md F-13.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _doc() -> str:
    f = REPO / "docs" / "ONE-PAGER.md"
    assert f.exists(), "F-13: docs/ONE-PAGER.md must exist"
    return f.read_text()


def test_one_pager_has_investor_narrative_sections():
    t = _doc().lower()
    for section in ("problem", "solution", "why now", "market", "traction", "team"):
        assert f"## {section}" in t, f"F-13: ONE-PAGER must have a '{section}' section"
    # The fundraising/next-step ask, however it's phrased.
    assert ("## the ask" in t) or ("## ask" in t), "F-13: ONE-PAGER must state the ask"


def test_one_pager_names_the_moat():
    t = _doc()
    assert "MCP" in t, "F-13: the MCP-as-server moat must be named"
    assert "local-first" in t.lower(), "F-13: local-first positioning must be present"


def test_one_pager_is_grounded_and_not_inventing_personal_data():
    t = _doc()
    # Public org identity is fine; personal founder bio + raise amount must be
    # left as explicit placeholders for Mickael (repo is public).
    assert "AVA Digital" in t, "F-13: should reference the public org (AVA Digital LLC)"
    assert "[Mickael" in t or "_[" in t, "F-13: confidential bits must be clearly-marked placeholders"
