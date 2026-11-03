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


# P-019 (audit 25 Oct 2026): the audit log rotates daily and keeps 30 days
# (codec_audit._rotate_if_needed, _RETAIN_DAYS), the Audit view has a fixed set
# of filter groups (codec_dashboard.html _auditAllCategories), and CODEC makes
# two requests on its own (codec_update.DEFAULT_FEED_URL, the paid app's licence
# key). README and the live demo script are read aloud; they must say the same.
_PUBLIC = ("README.md", "DEMO_SCRIPT.md")


def _flat(doc: str) -> str:
    return " ".join((REPO / doc).read_text().split())


def _audit_src() -> str:
    return (REPO / "codec_audit.py").read_text()


def _audit_page_groups() -> list:
    m = re.search(r"var _auditAllCategories = \[([^\]]*)\]", (REPO / "codec_dashboard.html").read_text())
    assert m, "the Audit view no longer lists its filter groups"
    return re.findall(r"'([a-z]+)'", m.group(1))


@pytest.mark.parametrize("doc", _PUBLIC)
def test_docs_do_not_claim_size_based_audit_rotation(doc):
    rotate = re.search(r"def _rotate_if_needed\(\):.*?\n\n\n", _audit_src(), re.S).group(0)
    assert "st_size" not in rotate and "st_mtime" in rotate, "codec_audit now rotates by size; update the docs"
    text = _flat(doc)
    assert not re.search(r"50\s?MB rotat", text), f"{doc} still says the audit log rotates at 50MB"


def test_readme_states_daily_audit_rotation_and_retention():
    m = re.search(r"^_RETAIN_DAYS = (\d+)$", _audit_src(), re.M)
    assert m and '"""Rotate audit.log daily.' in _audit_src()
    text = _flat("README.md")
    assert f"a new file each day and {m.group(1)} days kept" in text
    assert f"daily rotation, {m.group(1)} days kept" in text


@pytest.mark.parametrize("doc", _PUBLIC)
def test_audit_category_count_matches_the_audit_view(doc):
    groups = _audit_page_groups()
    counts = re.findall(r"(\d+)[ -](?:event |filter )?categor", _flat(doc))
    assert counts, f"{doc} no longer states an audit category count"
    assert {int(c) for c in counts} == {len(groups)}, f"{doc} says {counts}; the Audit view has {len(groups)}"
    if doc == "README.md":
        listed = re.search(r"into \d+ categories: ([a-z, ]+)\.", _flat(doc)).group(1)
        assert [g.strip() for g in listed.split(",")] == groups


@pytest.mark.parametrize("doc", _PUBLIC)
def test_docs_do_not_claim_zero_telemetry_or_every_call_logged(doc):
    text = _flat(doc).lower()
    for claim in ("no telemetry", "zero hidden telemetry", "zero telemetry", "every action tracked",
                  "every action codec takes is logged", "llm call logged",
                  "no data leaves unless you explicitly route a request through a cloud model"):
        assert claim not in text, f"{doc} still says {claim!r}"
    for rel in ("codec_update.py", "codec_license.py", "codec_llm.py"):
        src = (REPO / rel).read_text()
        assert "audit(" not in src and "log_event(" not in src, f"{rel} now writes audit lines; recheck the docs"
    assert "model calls have no line" in text


def test_readme_names_the_requests_codec_makes_on_its_own():
    feed = re.search(r'^DEFAULT_FEED_URL\s*=\s*"https://([a-z.]+)/', (REPO / "codec_update.py").read_text(), re.M)
    assert feed and feed.group(1) == "github.com"
    text = _flat("README.md")
    assert "Two requests go out on their own: the update check (GitHub" in text
    assert "the licence public key (AVA Digital)" in text
    search_src = (REPO / "codec_search.py").read_text()
    assert "duckduckgo.com" in search_src and "google.serper.dev" in search_src
    external = re.search(r"\*\*External services:\*\*([^\n]*)", (REPO / "README.md").read_text()).group(1)
    for name in ("DuckDuckGo", "Serper", "GitHub", "PRIVACY.md"):
        assert name in external, f"README 'External services' leaves out {name}"
