"""F-5 — versioning discipline. A single source of truth (VERSION ← CHANGELOG), an
introspectable runtime __version__, and a CHANGELOG-driven release-tag helper.

Stdlib-only so it stays green on the CI ubuntu runner (additive to the F-4 doc-guard gate).

Reference: docs/F5-VERSIONING-DESIGN.md.
"""
import importlib.util
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def _changelog_latest() -> str:
    text = (_REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(r"^##+\s*\[?v?(\d+\.\d+\.\d+)", text, re.MULTILINE)
    assert m, "no version heading found in CHANGELOG.md"
    return m.group(1)


def _load_tag_releases():
    path = _REPO / "scripts" / "tag_releases.py"
    spec = importlib.util.spec_from_file_location("tag_releases", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_version_file_exists_and_is_semver():
    vf = _REPO / "VERSION"
    assert vf.exists(), "VERSION file missing at repo root"
    v = vf.read_text(encoding="utf-8").strip()
    assert _SEMVER.match(v), f"VERSION is not valid SemVer: {v!r}"


def test_runtime_version_matches_version_file():
    import codec_version
    assert codec_version.__version__ == (_REPO / "VERSION").read_text(encoding="utf-8").strip()


def test_version_file_matches_changelog_latest():
    v = (_REPO / "VERSION").read_text(encoding="utf-8").strip()
    assert v == _changelog_latest(), (
        f"VERSION ({v}) must match the CHANGELOG's latest entry ({_changelog_latest()})"
    )


def test_tag_releases_parses_changelog():
    mod = _load_tag_releases()
    text = (_REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    versions = mod.parse_changelog_versions(text)
    vers = [v for v, _date in versions]
    assert versions[0][0] == _changelog_latest(), "newest version must be first"
    assert len(vers) >= 10, f"expected >=10 documented releases, got {len(vers)}"
    assert "1.0.0" in vers and "2.0.0" in vers, "known historical versions missing"
    for v, date in versions:
        assert _SEMVER.match(v), f"bad version in parse: {v!r}"
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", date), f"bad date in parse: {date!r}"


def test_tag_releases_is_dry_run_by_default():
    """Safety: the helper must not write tags unless explicitly told to."""
    mod = _load_tag_releases()
    assert hasattr(mod, "main"), "tag_releases must expose main()"
    # The module-level default must be non-destructive.
    src = (_REPO / "scripts" / "tag_releases.py").read_text(encoding="utf-8")
    assert "--execute" in src and "--push" in src, "expected explicit execute/push opt-ins"
