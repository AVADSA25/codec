"""Every public claim must be true of the shipped product.

2026-07-10 buyer-journey audit: four public surfaces advertised four different
products. FEATURES.md claimed 76 skills while the CI-gated manifest had 86;
README sold a "€10/month" plan that has never existed in Stripe. Marketing
numbers drift silently because nothing checks them.

These tests pin the claims we make about ourselves to the artifacts that prove
them. If you add a skill, bump the docs. If you change the price, change it here
and on the site in the same PR.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _manifest_skill_count() -> int:
    data = json.loads((REPO / "skills" / ".manifest.json").read_text())
    skills = data.get("skills", data) if isinstance(data, dict) else data
    return len(skills)


def _features_md() -> str:
    return (REPO / "FEATURES.md").read_text()


def test_features_md_skill_count_matches_the_manifest():
    """FEATURES.md is the product's own claim list; the manifest is the CI gate."""
    claimed = re.search(r"(\d+)\s+skills", _features_md())
    assert claimed, "FEATURES.md no longer states a skill count"
    assert int(claimed.group(1)) == _manifest_skill_count(), (
        f"FEATURES.md claims {claimed.group(1)} skills but skills/.manifest.json "
        f"has {_manifest_skill_count()}. Regenerate the manifest and update the docs "
        f"in the same commit."
    )


def test_features_md_version_matches_VERSION():
    version = (REPO / "VERSION").read_text().strip()          # e.g. 3.2.0
    major_minor = ".".join(version.split(".")[:2])            # -> 3.2
    assert f"CODEC v{major_minor}" in _features_md(), (
        f"FEATURES.md does not advertise the shipped version v{major_minor} (VERSION={version})"
    )


def test_features_md_test_count_claim_is_not_overstated():
    """'2000+ tests' must be backed by at least that many real test functions."""
    claimed = re.search(r"(\d[\d,]*)\+?\s+tests", _features_md())
    assert claimed, "FEATURES.md no longer states a test count"
    claimed_n = int(claimed.group(1).replace(",", ""))
    actual = sum(
        len(re.findall(r"^\s*def test_", p.read_text(errors="ignore"), re.M))
        for p in (REPO / "tests").glob("*.py")
    )
    assert actual >= claimed_n, f"FEATURES.md claims {claimed_n}+ tests; only {actual} exist"


# The paid Mac app has exactly one price in Stripe: $99/year. Prices that were
# advertised but never existed (notably a €10/month plan) must not come back.
_NEVER_EXISTED = (
    "€10/month",
    "€10 / month",
    "10 EUR/month",
)


@pytest.mark.parametrize("doc", ["README.md", "FEATURES.md"])
def test_docs_do_not_advertise_a_price_that_does_not_exist(doc):
    text = (REPO / doc).read_text()
    for phantom in _NEVER_EXISTED:
        assert phantom not in text, (
            f"{doc} advertises {phantom!r}, which has never existed in Stripe. "
            f"The paid Mac app is $99/year."
        )
