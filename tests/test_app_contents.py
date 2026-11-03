"""The buyer app ships the Cookbook and the Touch ID helper, and no stdio MCP service (P-012).

Reference: docs/APP-CONTENTS-DESIGN.md. The script checks run everywhere; the checker is
tested against small fake apps; a macOS-only build into a temp folder proves the real
build passes the checker, with the helper compiled for the app's macOS floor.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "packaging" / "macos"
BUILD = PKG / "build_app.sh"
CHECKER = PKG / "verify" / "app_contents.py"
APP_NAME = "Sovereign AI Workstation.app"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _check(app: Path, *extra: str):
    return subprocess.run([sys.executable, str(CHECKER), "--app", str(app), "--python", sys.executable, *extra],
                          capture_output=True, text=True, timeout=120)


# ── the build script ────────────────────────────────────────────────────────

def test_build_copies_the_cookbook_package():
    src = BUILD.read_text()
    assert 'cp -R "$REPO/codec_cookbook" "$CONTENTS/Resources/app/codec_cookbook"' in src
    assert 'rm -rf "$CONTENTS/Resources/app/codec_cookbook/__pycache__"' in src


def test_build_compiles_the_touchid_helper_from_source_for_the_floor():
    src = BUILD.read_text()
    assert 'AUTH_SRC="$REPO/codec_auth/main.swift"' in src
    assert 'AUTH_OUT="$CONTENTS/Resources/app/codec_auth/codec_auth"' in src
    assert re.search(r'swiftc -O -target "\$\{LAUNCHER_ARCH\}-apple-macos\$\{MIN_MACOS\}" -o "\$AUTH_OUT" "\$AUTH_SRC"', src)
    assert "-framework LocalAuthentication" in src
    # the checked-in binary (built for the developer's own macOS) must never be copied in
    assert not re.search(r'^\s*cp\b[^\n]*codec_auth/codec_auth', src, re.M)


def test_the_helper_lands_where_the_dashboard_looks_for_it():
    shared = (REPO / "routes" / "_shared.py").read_text()
    assert 'AUTH_BINARY = os.path.join(DASHBOARD_DIR, "codec_auth", "codec_auth")' in shared


def test_build_drops_only_the_stdio_mcp_server_from_the_fleet():
    src = BUILD.read_text()
    assert 'const DEV_ONLY = new Set(["codec-watchdog", "codec-mcp"]);' in src
    assert "codec-mcp-http" not in re.search(r"const DEV_ONLY = [^\n]*", src).group(0)


# ── the checker, on fake apps ───────────────────────────────────────────────

def _fake_app(root: Path) -> Path:
    app = root / APP_NAME
    res = app / "Contents" / "Resources"
    code = res / "app"
    (code / "skills").mkdir(parents=True)
    shutil.copytree(REPO / "codec_cookbook", code / "codec_cookbook",
                    ignore=shutil.ignore_patterns("__pycache__"))
    for f in ("codec_compare.py", "codec_jsonstore.py"):
        shutil.copy(REPO / f, code / f)
    shutil.copy(REPO / "skills" / "cookbook_scan.py", code / "skills" / "cookbook_scan.py")
    helper = code / "codec_auth" / "codec_auth"
    helper.parent.mkdir()
    helper.write_bytes(b"\xcf\xfa\xed\xfe" + b"\0" * 28)   # a Mach-O header is all the checker reads
    helper.chmod(0o755)
    services = [{"name": n} for n in ("codec", "codec-dashboard", "codec-mcp-http")]
    (res / "services.json").write_text(json.dumps(services))
    return app


def test_checker_passes_a_complete_app(tmp_path):
    r = _check(_fake_app(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "APP-CONTENTS-PASS" in r.stdout


def _no_cookbook(app):
    shutil.rmtree(app / "Contents/Resources/app/codec_cookbook")


def _broken_cookbook_module(app):
    (app / "Contents/Resources/app/codec_cookbook/serve.py").write_text("import not_a_real_module_p012\n")


def _no_compare(app):
    (app / "Contents/Resources/app/codec_compare.py").unlink()


def _no_helper(app):
    (app / "Contents/Resources/app/codec_auth/codec_auth").unlink()


def _script_helper(app):
    (app / "Contents/Resources/app/codec_auth/codec_auth").write_text("#!/bin/sh\necho {}\n")


def _ships_codec_mcp(app):
    p = app / "Contents/Resources/services.json"
    p.write_text(json.dumps(json.loads(p.read_text()) + [{"name": "codec-mcp"}]))


def _no_codec_mcp_http(app):
    (app / "Contents/Resources/services.json").write_text(json.dumps([{"name": "codec"}]))


@pytest.mark.parametrize("breakage, words", [
    (_no_cookbook, "codec_cookbook/__init__.py is missing"),
    (_broken_cookbook_module, "cannot import codec_cookbook.serve"),
    (_no_compare, "cannot import codec_compare"),
    (_no_helper, "codec_auth/codec_auth is missing"),
    (_script_helper, "is not a Mach-O file"),
    (_ships_codec_mcp, "ships the dev-only service codec-mcp"),
    (_no_codec_mcp_http, "missing codec-mcp-http"),
])
def test_checker_fails_on_each_missing_piece(tmp_path, breakage, words):
    app = _fake_app(tmp_path)
    breakage(app)
    r = _check(app)
    assert r.returncode == 1 and "APP-CONTENTS-PASS" not in r.stdout
    assert words in r.stderr, r.stderr


def test_checker_writes_no_bytecode_into_the_app(tmp_path):
    app = _fake_app(tmp_path)
    assert _check(app).returncode == 0
    assert not list(app.rglob("__pycache__")), "the probe must run with -B: a .pyc breaks the signature"


def test_checker_needs_an_interpreter(tmp_path):
    app = _fake_app(tmp_path)
    r = subprocess.run([sys.executable, str(CHECKER), "--app", str(app)], capture_output=True, text=True)
    assert r.returncode == 1 and "no Python to import with" in r.stderr


# ── a real build (macOS: swiftc, file, plutil) ──────────────────────────────

@pytest.fixture(scope="module")
def built_app(tmp_path_factory):
    out = tmp_path_factory.mktemp("p012-build")
    r = subprocess.run(["bash", str(BUILD), "--out", str(out), "--clean"],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, f"build_app.sh failed: {r.stderr}\n{r.stdout}"
    return out / APP_NAME


@pytest.mark.skipif(sys.platform != "darwin" or shutil.which("swiftc") is None,
                    reason="builds a macOS .app bundle")
def test_the_built_app_passes_the_checker(built_app):
    r = _check(built_app, "--touchid")
    assert r.returncode == 0, r.stderr
    assert "APP-CONTENTS-PASS" in r.stdout and "codec_auth --check: available=" in r.stdout
    code = built_app / "Contents" / "Resources" / "app"
    assert os.listdir(code / "codec_auth") == ["codec_auth"]
    assert not (code / "codec_cookbook" / "__pycache__").exists()


@pytest.mark.skipif(sys.platform != "darwin" or shutil.which("swiftc") is None,
                    reason="builds a macOS .app bundle")
def test_the_built_helper_runs_on_the_app_floor(built_app):
    minos = _load(PKG / "verify" / "macho_minos.py", "macho_minos_p012")
    floor = minos.parse_version(minos.floor_from_info_plist())
    helper = built_app / "Contents" / "Resources" / "app" / "codec_auth" / "codec_auth"
    assert minos.macho_minos(helper) == floor


@pytest.mark.skipif(sys.platform != "darwin" or shutil.which("swiftc") is None,
                    reason="builds a macOS .app bundle")
def test_the_dashboard_answers_from_the_shipped_helper(built_app, monkeypatch):
    """GET /api/auth/check reports exactly what the shipped helper says (true on a Touch ID Mac)."""
    from fastapi.testclient import TestClient

    import codec_config
    import routes._shared as shared
    import routes.auth as auth
    from codec_dashboard import app

    helper = str(built_app / "Contents" / "Resources" / "app" / "codec_auth" / "codec_auth")
    own = json.loads(subprocess.run([helper, "--check"], capture_output=True, text=True, timeout=10).stdout)
    monkeypatch.setattr(codec_config, "get_dashboard_token", lambda: "")
    monkeypatch.setattr(shared, "AUTH_BINARY", helper)
    monkeypatch.setattr(auth, "AUTH_BINARY", helper)
    monkeypatch.setattr(auth, "AUTH_TOUCHID_REMOTE", False)
    body = TestClient(app, client=("127.0.0.1", 5000)).get("/api/auth/check").json()
    assert body["touchid_available"] is own["available"]
