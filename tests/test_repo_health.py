"""Tests for PR-6A (Wave 6 / Audit F) — OSS-health files exist with the required
content, the stray root file is gone, and the handoff doc is present.

Regression guards (analog of source-invariants) so a later edit can't silently
drop a required section or re-introduce the garbage file.

Reference: docs/audits/PHASE-1-INVESTOR-READINESS.md (F-1/F-6/F-7/F-16).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


# ── F-1 SECURITY.md ───────────────────────────────────────────────────────────


def test_security_md_present_and_complete():
    f = REPO / "SECURITY.md"
    assert f.exists(), "F-1: SECURITY.md must exist at repo root"
    t = f.read_text()
    assert "Reporting a vulnerability" in t
    assert "Supported versions" in t
    assert "Private Vulnerability Reporting" in t, "must point researchers at the private channel"
    assert "## Scope" in t, "enterprise questionnaires expect an explicit scope"


# ── F-6 CODE_OF_CONDUCT.md ────────────────────────────────────────────────────


def test_code_of_conduct_present():
    f = REPO / "CODE_OF_CONDUCT.md"
    assert f.exists(), "F-6: CODE_OF_CONDUCT.md must exist at repo root"
    t = f.read_text()
    assert "Contributor Covenant" in t, "should adopt the Contributor Covenant"
    assert "Enforcement" in t and "@" in t, "must give an enforcement contact"


# ── F-7 FUNDING.yml ───────────────────────────────────────────────────────────


def test_funding_yml_present():
    f = REPO / ".github" / "FUNDING.yml"
    assert f.exists(), "F-7: .github/FUNDING.yml must exist"
    t = f.read_text()
    assert "github:" in t and "custom:" in t, "FUNDING.yml must declare funding surfaces"


# ── F-16 stray file removed ───────────────────────────────────────────────────


def test_no_stray_garbage_file():
    # Must be gone from the working tree AND from git's index.
    assert not (REPO / "authlib google-auth-httplib2 --break-system-packages").exists()
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True
    ).stdout
    assert "authlib google-auth-httplib2" not in tracked, "F-16: stray file must be untracked"


# ── handoff doc ───────────────────────────────────────────────────────────────


def test_handoff_doc_present():
    f = REPO / "docs" / "HANDOFF-MICKAEL.md"
    assert f.exists(), "the running Mickael action-items doc must exist"
    t = f.read_text()
    assert "merge" in t.lower() and "Apple" in t, "handoff must track the merge queue + Apple items"
