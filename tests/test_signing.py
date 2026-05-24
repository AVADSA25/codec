"""Tests for PR-5F (Audit E / E-2, E-3, W5-7/8) — the codesign + notarize
pipeline. Real signing needs a Developer ID cert (handoff); here we verify the
script contracts and that sign_app.sh's --dry-run enumerates nested code
inside-out (dylibs before the .app) without invoking codesign.

Reference: docs/W5-7-8-SIGN-NOTARIZE-DESIGN.md, docs/audits/PHASE-1-APPLE-APP.md (E-2/E-3).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "packaging" / "macos"
SIGN = PKG / "sign_app.sh"
NOTARIZE = PKG / "notarize_app.sh"


def _fake_app(tmp_path: Path) -> Path:
    app = tmp_path / "Sovereign AI Workstation.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Frameworks" / "python" / "lib").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_text("<plist/>")
    (app / "Contents" / "MacOS" / "codec").write_text("#!/bin/sh\n")
    # a nested dylib that inside-out signing must handle before the .app
    (app / "Contents" / "Frameworks" / "python" / "lib" / "fake.dylib").write_text("x")
    return app


def test_scripts_present_executable():
    for s in (SIGN, NOTARIZE):
        assert s.exists(), f"{s.name} must exist"
        assert os.access(s, os.X_OK), f"{s.name} must be executable"
        assert s.read_text().splitlines()[0].startswith("#!"), f"{s.name} needs a shebang"


def test_sign_script_contract():
    t = SIGN.read_text()
    assert "codesign" in t
    assert "--options runtime" in t, "must enable hardened runtime"
    assert "--timestamp" in t, "must use a secure timestamp"
    assert "--entitlements" in t, "must apply the W5-1 entitlements"
    assert "codec.entitlements" in t, "must default to the project entitlements"
    assert "--dry-run" in t
    assert "--verify" in t or "codesign --verify" in t, "must verify after signing"


def test_notarize_script_contract():
    t = NOTARIZE.read_text()
    assert "notarytool" in t, "must submit via notarytool"
    assert "stapler" in t, "must staple the ticket"
    assert "ditto" in t or "zip" in t, "must package the app for submission"
    assert "--dry-run" in t


def test_sign_dry_run_enumerates_inside_out(tmp_path):
    app = _fake_app(tmp_path)
    r = subprocess.run(
        ["bash", str(SIGN), "--app", str(app), "--identity", "TEST-IDENTITY", "--dry-run"],
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, f"dry-run failed: {r.stderr}\n{r.stdout}"
    out = r.stdout
    assert "fake.dylib" in out, "must enumerate nested dylibs"
    assert "[dry-run]" in out, "must not actually sign in dry-run"
    # inside-out: the nested dylib is planned before the final .app seal.
    assert "finally" in out.lower(), "must sign the .app last"
    assert out.index("fake.dylib") < out.lower().index("finally"), "dylib must be signed before the .app"
