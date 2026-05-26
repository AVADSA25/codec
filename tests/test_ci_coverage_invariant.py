"""Guard against future CI-coverage drift.

The 2026-05 audit (F-3) found that .github/workflows/ci.yml ran 23 of the
134 test files in tests/ — leaving the Wave-1+2 hardening tests
(D-7 / D-9 / D-12 / D-13 / D-18 / D-19 / D-21 / D-22) without regression
protection on PRs. This file's tests fail loudly if anyone re-introduces
that pattern by enumerating individual test files in ci.yml instead of
running the full tests/ tree.

Removing this file or weakening its assertions REGRESSES F-3 closure.
"""
from pathlib import Path
import re

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def test_ci_workflow_exists() -> None:
    assert CI_WORKFLOW.exists(), f"ci.yml not found at {CI_WORKFLOW}"


def test_ci_runs_full_pytest_suite() -> None:
    """ci.yml must invoke `pytest tests/` (or equivalent full-tree form),
    not an enumerated subset of files."""
    content = CI_WORKFLOW.read_text()

    pytest_invocations = re.findall(r"pytest[^\n]*", content)
    # Filter out the matches that are just comments mentioning pytest.
    invocations = [
        line for line in pytest_invocations
        if not line.lstrip().startswith("#")
    ]
    assert invocations, "No non-comment pytest invocation found in ci.yml"

    full_suite_patterns = [
        r"pytest\s+tests/?\b",         # `pytest tests` or `pytest tests/`
        r"-m\s+pytest\s+tests/?\b",    # `python -m pytest tests/`
    ]
    matches_full_suite = any(
        any(re.search(p, inv) for p in full_suite_patterns)
        for inv in invocations
    )
    assert matches_full_suite, (
        "ci.yml does not run the full tests/ suite. "
        f"Found pytest invocations: {invocations}. "
        f"Expected one to match one of {full_suite_patterns}."
    )


def test_ci_does_not_enumerate_individual_test_files() -> None:
    """ci.yml should NOT enumerate >= 5 individual test files.

    A small number of explicit `tests/test_*.py` references are allowed
    (e.g. a hand-rolled smoke script invoked via `python` not `pytest`).
    A large number indicates the F-3 pattern reappearing — drift caught.
    """
    content = CI_WORKFLOW.read_text()
    # Match `test_<name>.py` whether wrapped in `tests/`, quoted, or bare.
    explicit_files = re.findall(r"test_[a-zA-Z0-9_]+\.py", content)
    # De-duplicate (some files may appear in multiple steps).
    unique_files = set(explicit_files)
    assert len(unique_files) < 5, (
        f"ci.yml references {len(unique_files)} distinct test files "
        f"explicitly: {sorted(unique_files)}. This regresses F-3 closure. "
        f"Switch to `pytest tests/` to run the full suite."
    )
