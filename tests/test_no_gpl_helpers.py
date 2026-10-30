"""The built-app check for GPL helper programs works (audit P-033)."""
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "packaging/macos/verify/no_gpl_helpers.mjs"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def _run(app):
    return subprocess.run(["node", str(SCRIPT), str(app)], capture_output=True, text=True)


def _app(tmp_path, *files):
    app = tmp_path / "Test.app"
    for f in ("Contents/Resources/python/bin/python3", "Contents/Resources/app/codec.py",
              "Contents/Resources/python/lib/python3.12/site-packages/soundfile.py") + files:
        p = app / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    return app


def test_a_clean_app_passes(tmp_path):
    r = _run(_app(tmp_path))
    assert r.returncode == 0 and "NO-GPL-HELPERS-PASS" in r.stdout


@pytest.mark.parametrize("bad", ["Contents/MacOS/sox", "Contents/Resources/bin/rec", "Contents/Resources/bin/pdftotext",
                                 "Contents/Frameworks/libsox.3.dylib",
                                 "Contents/Resources/python/lib/python3.12/site-packages/av/.dylibs/libx264.165.dylib"])
def test_each_gpl_helper_is_caught(tmp_path, bad):
    r = _run(_app(tmp_path, bad))
    assert r.returncode == 1 and bad.split("/")[-1] in r.stderr


def test_lookalikes_are_not_flagged(tmp_path):
    # libsoxr is the LGPL resampler; numpy/rec is a folder, not the SoX program
    r = _run(_app(tmp_path, "Contents/Frameworks/libsoxr.0.1.2.dylib", "Contents/Resources/numpy/rec/__init__.py"))
    assert r.returncode == 0, r.stderr


def test_a_missing_app_fails(tmp_path):
    assert _run(tmp_path / "nope.app").returncode == 1
