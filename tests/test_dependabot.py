"""Tests for PR-6I (Audit F / F-4 partial) — Dependabot config exists and is
sane, and CI gates the deterministic readiness doc-guard tests. String-based
validation only (no PyYAML dependency in the test env).

Reference: docs/audits/PHASE-1-INVESTOR-READINESS.md F-4.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_dependabot_config_present_and_sane():
    f = REPO / ".github" / "dependabot.yml"
    assert f.exists(), "F-4: .github/dependabot.yml must exist"
    t = f.read_text()
    assert "version: 2" in t, "Dependabot config must declare version: 2"
    assert "pip" in t, "must track Python (pip) dependencies"
    assert "github-actions" in t, "must track GitHub Actions versions"
    assert "weekly" in t, "must set an update cadence"
    assert "open-pull-requests-limit" in t, "must cap open PRs so it stays signal, not noise"


def test_ci_gates_the_readiness_doc_guards():
    t = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    # F-4: the investor/Apple readiness artifacts must be protected by CI.
    for guard in (
        "test_repo_health",
        "test_privacy_doc",
        "test_readme_investor",
        "test_one_pager",
        "test_dependabot",
    ):
        assert guard in t, f"F-4: CI must run {guard}.py"
