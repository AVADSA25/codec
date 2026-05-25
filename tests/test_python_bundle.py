"""Tests for PR-5D (Audit E / E-6, W5-4) — bundling a self-contained,
sha256-pinned relocatable Python (python-build-standalone) into the .app.

Hermetic: the manifest + script contract + a no-network --dry-run are tested
here. The real ~30 MB download is validated by hand on macOS (see PR), never in
CI.

Reference: docs/W5-4-PYTHON-BUNDLE-DESIGN.md, docs/audits/PHASE-1-APPLE-APP.md (E-6).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "packaging" / "macos"
MANIFEST = PKG / "python-runtime.json"
BUNDLE = PKG / "bundle_python.sh"
BUILD = PKG / "build_app.sh"
LAUNCHER = PKG / "launcher" / "codec"

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def test_manifest_valid_and_pinned():
    d = json.loads(MANIFEST.read_text())
    assert re.match(r"^3\.\d+\.\d+$", d["python_version"]), "python_version must be X.Y.Z"
    assert str(d["pbs_release"]).isdigit(), "pbs_release must be a date tag"
    assert "{release}" in d["url_template"] and "{arch}" in d["url_template"]
    for arch in ("aarch64", "x86_64"):
        sha = d["assets"][arch]["sha256"]
        assert _HEX64.match(sha), f"{arch} sha256 must be 64 hex chars"


def test_bundler_script_contract():
    assert BUNDLE.exists(), "E-6: bundle_python.sh must exist"
    assert os.access(BUNDLE, os.X_OK), "bundle_python.sh must be executable"
    t = BUNDLE.read_text()
    assert t.splitlines()[0].startswith("#!"), "needs a shebang"
    assert "shasum" in t or "sha256" in t.lower(), "must verify the download's sha256"
    # Python lives in Contents/Resources/python, NOT Frameworks/ — a bare python
    # tree under Frameworks/ is treated as a nested bundle and breaks the
    # code-signing seal (Gatekeeper rejects). Resources/ is the py2app/briefcase
    # convention. Do not revert to Frameworks/.
    assert "Resources/python" in t, "must install into Contents/Resources/python"
    assert "--dry-run" in t, "must support --dry-run"


def test_dry_run_prints_arch_url_and_sha_without_downloading():
    d = json.loads(MANIFEST.read_text())
    expect_sha = d["assets"]["aarch64"]["sha256"]
    r = subprocess.run(
        ["bash", str(BUNDLE), "--app", "/tmp/does-not-exist.app", "--arch", "aarch64", "--dry-run"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, f"dry-run failed: {r.stderr}\n{r.stdout}"
    out = r.stdout
    assert "aarch64-apple-darwin-install_only.tar.gz" in out, "dry-run must show the resolved asset URL"
    assert expect_sha in out, "dry-run must show the pinned sha256"


def test_build_app_has_with_python_flag():
    assert "--with-python" in BUILD.read_text(), "build_app.sh must expose --with-python"


def test_launcher_prefers_bundled_pbs_python():
    # Matches the Resources/ relocation above (Gatekeeper signing fix).
    assert "Resources/python/bin/python3" in LAUNCHER.read_text(), (
        "launcher must prefer the bundled python-build-standalone interpreter"
    )


if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-v"]))
