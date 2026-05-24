"""Tests for PR-5B (Audit E / E-1, W5-2) — the macOS .app bundle wrapper +
Python launcher. Portable structure/contract checks run everywhere; a darwin-only
smoke actually assembles the bundle and runs the entry-point self-test.

Reference: docs/W5-2-APP-BUNDLE-DESIGN.md, docs/audits/PHASE-1-APPLE-APP.md (E-1).
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "packaging" / "macos"
BUILD = PKG / "build_app.sh"
LAUNCHER = PKG / "launcher" / "codec"
ENTRY = PKG / "launcher" / "codec_app_main.py"


# ---- portable contract checks --------------------------------------------

def test_build_script_present_and_executable():
    assert BUILD.exists(), "E-1: packaging/macos/build_app.sh must exist"
    assert os.access(BUILD, os.X_OK), "build_app.sh must be executable"


def test_launcher_present_executable_with_shebang():
    assert LAUNCHER.exists(), "E-1: launcher Contents/MacOS/codec source must exist"
    assert os.access(LAUNCHER, os.X_OK), "launcher must be executable"
    first = LAUNCHER.read_text().splitlines()[0]
    assert first.startswith("#!"), "launcher must have a shebang"
    assert "codec_app_main.py" in LAUNCHER.read_text(), "launcher must exec the Python entry point"


def test_entry_point_has_selftest_and_is_stdlib_only():
    assert ENTRY.exists(), "E-1: codec_app_main.py entry point must exist"
    t = ENTRY.read_text()
    assert "--selftest" in t, "entry point must expose a safe --selftest"
    # Must not import the codec engine (it has to run before the venv is wired).
    for bad in ("import codec\n", "from codec ", "import codec_"):
        assert bad not in t, f"entry point must stay stdlib-only (found {bad!r})"


def test_build_script_wires_w51_metadata():
    t = BUILD.read_text()
    assert "Info.plist" in t, "build must copy the W5-1 Info.plist"
    assert "CFBundleExecutable" in BUILD.read_text() or "MacOS/codec" in t, (
        "build must place the launcher at the CFBundleExecutable path (MacOS/codec)"
    )


# ---- darwin-only end-to-end assembly smoke --------------------------------

@pytest.mark.skipif(sys.platform != "darwin", reason="builds a macOS .app bundle")
def test_build_assembles_bundle_and_selftest_passes():
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run(
            ["bash", str(BUILD), "--out", td, "--clean"],
            capture_output=True, text=True, timeout=120,
        )
        assert r.returncode == 0, f"build_app.sh failed: {r.stderr}\n{r.stdout}"
        app = Path(td) / "Sovereign AI Workstation.app"
        assert (app / "Contents" / "Info.plist").exists(), "Info.plist not copied into bundle"
        launcher = app / "Contents" / "MacOS" / "codec"
        assert launcher.exists() and os.access(launcher, os.X_OK), "bundle launcher missing/not exec"
        assert (app / "Contents" / "Resources" / "codec_app_main.py").exists(), "entry not in Resources"
        assert (app / "Contents" / "PkgInfo").exists(), "PkgInfo missing"
        # The safe self-test must pass against the assembled bundle.
        st = subprocess.run(
            [sys.executable, str(app / "Contents" / "Resources" / "codec_app_main.py"), "--selftest"],
            capture_output=True, text=True, timeout=60,
        )
        assert st.returncode == 0, f"--selftest failed: {st.stderr}\n{st.stdout}"
