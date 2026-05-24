"""F-15 — pyproject.toml provides modern, introspectable project metadata.

CODEC is an application run via PM2 from a checkout (57 flat codec_*.py modules; skills/ is
runtime-loaded, not a package), so pyproject is a metadata + dependency declaration, not a
distributable wheel of the flat modules. These tests pin the metadata shape + tie the version
to the F-5 single source of truth (VERSION). Stdlib-only (tomllib) → green on the CI runner.

Reference: docs/F15-PYPROJECT-DESIGN.md.
"""
import sys
import tomllib
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))


def _load():
    with open(_REPO / "pyproject.toml", "rb") as f:
        return tomllib.load(f)


def test_pyproject_has_project_metadata():
    d = _load()
    p = d["project"]
    assert p["name"] == "codec"
    assert p["description"]
    assert "MIT" in str(p["license"])
    assert p["requires-python"]
    assert p["authors"]
    assert p["urls"]["Repository"].endswith("/codec")


def test_pyproject_has_valid_build_system():
    d = _load()
    assert d["build-system"]["build-backend"] == "setuptools.build_meta"
    assert any("setuptools" in r for r in d["build-system"]["requires"])


def test_pyproject_version_is_dynamic_from_VERSION_file():
    d = _load()
    assert "version" in d["project"]["dynamic"], "version must be dynamic"
    assert d["tool"]["setuptools"]["dynamic"]["version"]["file"] == "VERSION"
    # VERSION must exist and match the F-5 single source of truth
    assert (_REPO / "VERSION").read_text(encoding="utf-8").strip() == "2.3.0"


def test_pyproject_declares_core_runtime_dependencies():
    d = _load()
    deps = " ".join(d["project"]["dependencies"])
    for pkg in ("pynput", "httpx", "fastmcp", "requests", "numpy"):
        assert pkg in deps, f"core dependency {pkg} not declared"


def test_pyproject_requires_python_matches_readme_claim():
    """README claims 3.10+; pyproject must agree."""
    d = _load()
    assert ">=3.10" in d["project"]["requires-python"]
