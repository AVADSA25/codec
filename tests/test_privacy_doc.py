"""Tests for PR-6F (Audit F / F-2) — PRIVACY.md exists and answers the EU
enterprise questions (what data, legal basis, retention) + discloses the
data flows that leave the machine. Regression guard for the privacy statement.

Reference: docs/audits/PHASE-1-INVESTOR-READINESS.md F-2.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_privacy_doc_present_and_complete():
    f = REPO / "docs" / "PRIVACY.md"
    assert f.exists(), "F-2: docs/PRIVACY.md must exist"
    t = f.read_text()
    assert "local-first" in t.lower(), "must state the local-first default"
    assert "GDPR" in t, "must cover GDPR (paid tier, EU market)"
    assert "AI Act" in t, "must cover the EU AI Act transparency obligation"
    assert "privacy@avadigital.ai" in t, "must give a data-subject contact"


def test_privacy_doc_discloses_cloud_flows():
    t = (REPO / "docs" / "PRIVACY.md").read_text()
    # The data flows that actually leave the machine must be named.
    for processor in ("Anthropic", "Cloudflare", "Google", "Telegram"):
        assert processor in t, f"PRIVACY.md must disclose the {processor} data flow"
    assert "leaves your Mac" in t, "must have an explicit 'what leaves the machine' section"
