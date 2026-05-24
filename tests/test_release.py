"""Tests for PR-5H (Audit E, W5 capstone) — the release orchestrator + DMG.

The contract + the orchestrator's --dry-run plan are tested portably; a
darwin-only smoke proves make_dmg.sh actually produces a .dmg.

Reference: docs/W5-RELEASE-DMG-DESIGN.md.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "packaging" / "macos"
DMG = PKG / "make_dmg.sh"
RELEASE = PKG / "release_macos.sh"


def test_scripts_present_executable():
    for s in (DMG, RELEASE):
        assert s.exists(), f"{s.name} must exist"
        assert os.access(s, os.X_OK), f"{s.name} must be executable"
        assert s.read_text().splitlines()[0].startswith("#!"), f"{s.name} needs a shebang"


def test_make_dmg_contract():
    t = DMG.read_text()
    assert "hdiutil" in t, "must build the dmg with hdiutil"
    assert "/Applications" in t, "must add a drag-to-install /Applications symlink"
    assert "--dry-run" in t


def test_release_dry_run_names_all_stages_in_order():
    r = subprocess.run(
        ["bash", str(RELEASE), "--identity", "TEST", "--keychain-profile", "p", "--dry-run"],
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, f"{r.stderr}\n{r.stdout}"
    out = r.stdout
    for stage in ("build_app.sh", "sign_app.sh", "notarize_app.sh", "make_dmg.sh"):
        assert stage in out, f"release plan must include {stage}"
    # ordering: build < sign < notarize < dmg
    assert out.index("build_app.sh") < out.index("sign_app.sh") < out.index("notarize_app.sh") < out.index("make_dmg.sh")


def test_release_honors_skips():
    r = subprocess.run(
        ["bash", str(RELEASE), "--identity", "TEST", "--skip-notarize", "--skip-dmg", "--dry-run"],
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, f"{r.stderr}\n{r.stdout}"
    assert "build_app.sh" in r.stdout and "sign_app.sh" in r.stdout
    assert "notarize_app.sh" not in r.stdout, "--skip-notarize must drop notarization"
    assert "make_dmg.sh" not in r.stdout, "--skip-dmg must drop the dmg step"


@pytest.mark.skipif(sys.platform != "darwin", reason="hdiutil is macOS-only")
def test_make_dmg_produces_dmg(tmp_path):
    app = tmp_path / "Fixture.app"
    (app / "Contents").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_text("<plist/>")
    out = tmp_path / "out.dmg"
    r = subprocess.run(
        ["bash", str(DMG), "--app", str(app), "--out", str(out), "--volname", "Fixture"],
        capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, f"make_dmg failed: {r.stderr}\n{r.stdout}"
    assert out.exists() and out.stat().st_size > 0, "a non-empty .dmg must be produced"
