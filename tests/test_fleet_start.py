"""The shipped .app must actually start the CODEC fleet (W5-2 x W5-3 wiring).

2026-07 buyer-journey audit, finding D2: a paying customer's app launched, logged
"fleet start deferred to W5-3 (launchd); no services started", and exited 0.

The cause was never missing engineering. `launchd/generate_launchagents.py`,
`launchd/install_launchagents.sh` and `first_run.py` all existed and were tested,
and first_run even invoked the installer. But:

  1. `codec_app_main.py:main()` never called first_run — nothing did.
  2. `build_app.sh` never copied first_run.py or launchd/ into the bundle, so the
     files a buyer received did not contain them.
  3. `install_launchagents.sh` read the service list from ecosystem.config.js via
     `node`, which a buyer's Mac does not have.

Each test below pins one of those three, plus the dev-machine safety property
that made W5-2 defer in the first place: outside a bundle, start NOTHING (the
fleet runs under PM2 there and double-running it is destructive).
"""
from __future__ import annotations

import importlib.util
import json
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "packaging" / "macos"
ENTRY = PKG / "launcher" / "codec_app_main.py"
BUILD = PKG / "build_app.sh"
INSTALL_SH = PKG / "launchd" / "install_launchagents.sh"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def entry():
    return _load(ENTRY, "codec_app_main_under_test")


@pytest.fixture
def first_run():
    return _load(PKG / "first_run.py", "first_run_under_test")


def _fake_bundle(tmp_path: Path) -> Path:
    """A minimal .app skeleton: Contents/{Info.plist,MacOS/codec,Resources/...}"""
    contents = tmp_path / "Sovereign AI Workstation.app" / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "MacOS" / "codec").write_text("#!/bin/sh\n")
    res = contents / "Resources"
    (res / "launchd").mkdir(parents=True)
    (res / "app").mkdir()
    (res / "python" / "bin").mkdir(parents=True)
    (res / "python" / "bin" / "python3").write_text("#!/bin/sh\n")
    (res / "first_run.py").write_text("#\n")
    (res / "launchd" / "install_launchagents.sh").write_text("#!/bin/bash\n")
    (res / "services.json").write_text("[]")
    with (contents / "Info.plist").open("wb") as fh:
        plistlib.dump({"CFBundleName": "Sovereign AI Workstation"}, fh)
    return contents


# ── 1. main() must start the fleet inside a bundle ──────────────────────────

def test_main_runs_first_run_on_first_launch(entry, tmp_path, monkeypatch):
    contents = _fake_bundle(tmp_path)
    home = tmp_path / "codec_home"
    home.mkdir()
    calls = []

    monkeypatch.setattr(entry, "_bundle_contents", lambda: contents)
    monkeypatch.setattr(entry, "_codec_dir", lambda: home)
    monkeypatch.setattr(entry, "_log", lambda *_a: None)
    monkeypatch.setattr(entry, "_fleet_loaded", lambda: 15)
    monkeypatch.setattr(entry, "_run_first_run", lambda res: calls.append(res) or 0)

    assert entry.main([]) == 0
    assert calls, "first launch must run first_run.py — this is the bug that shipped"


def test_main_does_not_rerun_first_run_once_complete(entry, tmp_path, monkeypatch):
    contents = _fake_bundle(tmp_path)
    home = tmp_path / "codec_home"
    home.mkdir()
    (home / entry.SENTINEL).write_text("ok\n")

    monkeypatch.setattr(entry, "_bundle_contents", lambda: contents)
    monkeypatch.setattr(entry, "_codec_dir", lambda: home)
    monkeypatch.setattr(entry, "_log", lambda *_a: None)
    monkeypatch.setattr(entry, "_fleet_loaded", lambda: 15)
    monkeypatch.setattr(entry, "_run_first_run",
                        lambda res: pytest.fail("must not re-run first_run"))

    assert entry.main([]) == 0


def test_main_rebootstraps_when_launchd_dropped_the_fleet(entry, tmp_path, monkeypatch):
    contents = _fake_bundle(tmp_path)
    home = tmp_path / "codec_home"
    home.mkdir()
    (home / entry.SENTINEL).write_text("ok\n")
    loaded = {"n": 0}
    boots = []

    monkeypatch.setattr(entry, "_bundle_contents", lambda: contents)
    monkeypatch.setattr(entry, "_codec_dir", lambda: home)
    monkeypatch.setattr(entry, "_log", lambda *_a: None)
    monkeypatch.setattr(entry, "_fleet_loaded", lambda: loaded["n"])

    def boot(res):
        boots.append(res)
        loaded["n"] = 15
        return 0

    monkeypatch.setattr(entry, "_bootstrap_fleet", boot)
    assert entry.main([]) == 0
    assert boots, "an empty launchd fleet must be re-bootstrapped"


def test_main_fails_when_no_services_come_up(entry, tmp_path, monkeypatch):
    """Silence is not success: if nothing is loaded after setup, say so."""
    contents = _fake_bundle(tmp_path)
    home = tmp_path / "codec_home"
    home.mkdir()

    monkeypatch.setattr(entry, "_bundle_contents", lambda: contents)
    monkeypatch.setattr(entry, "_codec_dir", lambda: home)
    monkeypatch.setattr(entry, "_log", lambda *_a: None)
    monkeypatch.setattr(entry, "_run_first_run", lambda res: 0)
    monkeypatch.setattr(entry, "_fleet_loaded", lambda: 0)

    assert entry.main([]) == 1


# ── 2. dev-machine safety: outside a bundle, start nothing ──────────────────

def test_source_tree_starts_nothing(entry, monkeypatch):
    """PM2 owns the fleet on a developer's machine; launchd must not double-run it."""
    monkeypatch.setattr(entry, "_bundle_contents", lambda: None)
    monkeypatch.setattr(entry, "_log", lambda *_a: None)
    monkeypatch.setattr(entry, "start_fleet",
                        lambda c: pytest.fail("must never start the fleet from the source tree"))
    assert entry.main([]) == 0


def test_selftest_still_starts_nothing(entry, monkeypatch):
    monkeypatch.setattr(entry, "start_fleet",
                        lambda c: pytest.fail("--selftest must not start the fleet"))
    assert entry.main(["--selftest"]) in (0, 1)


# ── 3. the bundle must actually contain what main() calls ──────────────────

@pytest.mark.parametrize("copy_cmd", [
    'cp "$PKG_DIR/first_run.py"',
    'cp "$PKG_DIR/fetch_models.py"',
    'cp "$PKG_DIR/models.json"',
    'cp -R "$PKG_DIR/launchd"',
])
def test_build_app_bundles_the_fleet_installer(copy_cmd):
    """Assert the actual copy COMMAND, not just the filename appearing somewhere:
    the filenames also occur in comments, so a substring check passes even after
    the cp line is deleted (caught by mutation-testing this very test)."""
    src = BUILD.read_text()
    assert copy_cmd in src, (
        f"build_app.sh must run `{copy_cmd}`; without it the shipped app has "
        f"nothing to start"
    )


def test_build_app_generates_services_json_at_build_time():
    src = BUILD.read_text()
    assert "services.json" in src, "build must emit Resources/services.json"
    assert "ecosystem.config.js" in src and "node -e" in src, \
        "services.json must be dumped from the PM2 ecosystem while node is available"
    assert "FATAL: node not found" in src, \
        "a build without node must fail loudly, not ship an app with no fleet"


def test_build_app_strips_the_developers_repo_path():
    """Every service in ecosystem.config.js has cwd = the build machine's repo.
    Shipping that verbatim makes all 15 services fail on a buyer's Mac, because
    the directory does not exist there. Caught during the 2026-07 fleet wiring."""
    src = BUILD.read_text()
    assert "delete a.cwd" in src, "the repo-root cwd must be dropped so --workdir applies"
    assert 'grep -q "$REPO"' in src and "FATAL: services.json still contains" in src, \
        "the build must FAIL if a build-machine path survives into services.json"


def test_generator_substitutes_workdir_when_cwd_is_the_repo_root():
    sys.path.insert(0, str(PKG / "launchd"))
    import generate_launchagents as gen

    # cwd absent (build_app.sh deleted it) -> the bundle's workdir is used
    _lbl, plist = gen.pm2_app_to_launchd(
        {"name": "svc", "script": "x.py"}, default_workdir="/App/Resources/app")
    assert plist["WorkingDirectory"] == "/App/Resources/app"

    # cwd == repo root and repo_root known -> also substituted
    _lbl, plist = gen.pm2_app_to_launchd(
        {"name": "svc", "script": "x.py", "cwd": "/repo"},
        default_workdir="/App/Resources/app", repo_root="/repo")
    assert plist["WorkingDirectory"] == "/App/Resources/app"

    # an unrelated absolute cwd is preserved
    _lbl, plist = gen.pm2_app_to_launchd(
        {"name": "svc", "script": "x.py", "cwd": "/opt/other"},
        default_workdir="/App/Resources/app", repo_root="/repo")
    assert plist["WorkingDirectory"] == "/opt/other"


# ── 4. the buyer's Mac has no node ─────────────────────────────────────────

def test_install_script_accepts_a_services_json():
    src = INSTALL_SH.read_text()
    assert "--services-json" in src and "--from-json" in src, \
        "installer must read a prebuilt services.json (a buyer's Mac has no node)"


def test_install_script_prefers_bundled_services_json_over_node():
    src = INSTALL_SH.read_text()
    i_json = src.index("--from-json")
    i_eco = src.index("--from-ecosystem", src.index("GEN_ARGS"))
    assert i_json < i_eco, "the node-free path must be tried before the node path"


def test_install_script_errors_clearly_when_node_is_missing():
    src = INSTALL_SH.read_text()
    assert "node is required" in src and "--services-json" in src


def test_install_script_is_valid_bash():
    r = subprocess.run(["bash", "-n", str(INSTALL_SH)], capture_output=True, text=True)
    assert r.returncode == 0, f"syntax error: {r.stderr}"


def test_build_app_is_valid_bash():
    r = subprocess.run(["bash", "-n", str(BUILD)], capture_output=True, text=True)
    assert r.returncode == 0, f"syntax error: {r.stderr}"


# ── 5. first_run passes the bundle's own interpreter / workdir / services ──

def test_first_run_detects_a_bundle(first_run, tmp_path, monkeypatch):
    contents = _fake_bundle(tmp_path)
    monkeypatch.setattr(first_run, "HERE", str(contents / "Resources"))
    assert first_run.bundle_contents() == str(contents)


def test_first_run_in_source_tree_is_not_a_bundle(first_run):
    assert first_run.bundle_contents() is None


def test_first_run_passes_bundle_paths_to_the_installer(first_run, tmp_path):
    contents = _fake_bundle(tmp_path)
    args = first_run.launchagent_args(str(contents))
    assert "--interpreter" in args, "must use the bundled python, not one on PATH"
    assert "--workdir" in args, "services must run from Resources/app"
    assert "--services-json" in args, "must use the prebuilt service list (no node)"
    interp = args[args.index("--interpreter") + 1]
    assert interp.endswith("Resources/python/bin/python3")


def test_first_run_passes_nothing_extra_outside_a_bundle(first_run):
    assert first_run.launchagent_args(None) == []


# ── 6. the generator really can build plists from that JSON, no node ───────

def test_generator_builds_plists_from_services_json(tmp_path):
    sys.path.insert(0, str(PKG / "launchd"))
    import generate_launchagents as gen

    services = [{"name": "dashboard", "script": "codec_dashboard.py",
                 "interpreter": "python3", "autorestart": True}]
    p = tmp_path / "services.json"
    p.write_text(json.dumps(services))

    apps = gen.load_from_json(str(p))
    out = gen.generate_all(apps, interpreter_map={}, default_workdir="/app",
                           repo_root=None, log_dir=str(tmp_path))
    assert "ai.avadigital.codec.dashboard" in out
    plist = plistlib.loads(out["ai.avadigital.codec.dashboard"])
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is True
