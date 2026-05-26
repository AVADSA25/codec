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
    """F-4: the investor/Apple readiness artifacts must be protected by CI.

    Originally asserted that each guard's filename appeared verbatim in
    ci.yml (the old enumerate-files pattern). Post-F-3-closure (this PR),
    ci.yml runs the full `pytest tests/` tree, which covers the F-4 guards
    *and every other test file* — stronger coverage than enumeration ever
    provided. New assertion: ci.yml invokes the full suite AND each guard
    file physically exists in tests/ (so removing one is caught here, not
    just by missing-from-ci which the F-3 test now owns).
    """
    import re
    t = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    # Verify CI runs the full pytest suite (F-3 closure invariant).
    assert re.search(r"pytest\s+tests/?\b", t) or re.search(
        r"-m\s+pytest\s+tests/?\b", t
    ), "F-3 regression: CI must invoke the full `pytest tests/` suite"
    # Verify each F-4 guard file is actually present in tests/ (so the
    # full-suite run reaches them). Removing a guard is a real regression.
    for guard in (
        "test_repo_health",
        "test_privacy_doc",
        "test_readme_investor",
        "test_one_pager",
        "test_dependabot",
    ):
        assert (REPO / "tests" / f"{guard}.py").exists(), (
            f"F-4: tests/{guard}.py must exist (would not run if missing)"
        )
