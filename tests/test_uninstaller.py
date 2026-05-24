"""Tests for PR-5E (Audit E / E-14, W5-12) — the macOS uninstaller.

Safety-critical: the script deletes things, so every test runs against a
throwaway ``--home`` temp dir (never the real $HOME) and Keychain ops are gated
to real-$HOME runs. We verify dry-run preserves everything, --yes removes the
app/agents/logs but keeps user data, and --yes --purge-data removes data too.

Reference: docs/W5-12-UNINSTALLER-DESIGN.md, docs/audits/PHASE-1-APPLE-APP.md (E-14).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
UNINSTALL = REPO / "packaging" / "macos" / "uninstall_codec.sh"


def _fixtures(tmp_path: Path) -> tuple[Path, Path]:
    """Build a fake $HOME (with .codec, logs, a LaunchAgent) + a fake .app."""
    home = tmp_path / "home"
    (home / ".codec").mkdir(parents=True)
    (home / ".codec" / "memory.db").write_text("data")
    (home / "Library" / "Logs" / "CODEC").mkdir(parents=True)
    (home / "Library" / "Logs" / "CODEC" / "launch.log").write_text("log")
    la = home / "Library" / "LaunchAgents"
    la.mkdir(parents=True)
    (la / "ai.avadigital.codec.codec-dashboard.plist").write_text("<plist/>")
    app = tmp_path / "Applications" / "Sovereign AI Workstation.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_text("<plist/>")
    return home, app


def _run(home: Path, app: Path, *flags: str):
    return subprocess.run(
        ["bash", str(UNINSTALL), "--home", str(home), "--app", str(app), *flags],
        capture_output=True, text=True, timeout=60,
    )


def test_script_present_executable_and_guarded():
    assert UNINSTALL.exists(), "E-14: packaging/macos/uninstall_codec.sh must exist"
    assert os.access(UNINSTALL, os.X_OK), "must be executable"
    t = UNINSTALL.read_text()
    assert t.splitlines()[0].startswith("#!"), "needs a shebang"
    # Safety: a guarded remove helper, and no bare `rm -rf "$HOME"`.
    assert "safe_rm" in t, "must route deletes through a guarded safe_rm helper"
    assert 'rm -rf "$HOME"' not in t and "rm -rf $HOME" not in t, "must never rm -rf bare $HOME"
    # Must teach the user about the un-revokable TCC residue.
    low = t.lower()
    assert "privacy" in low or "tcc" in low or "accessibility" in low, "must document TCC residue"


def test_dry_run_is_default_and_deletes_nothing(tmp_path):
    home, app = _fixtures(tmp_path)
    r = _run(home, app, "--dry-run")
    assert r.returncode == 0, f"dry-run failed: {r.stderr}\n{r.stdout}"
    # Everything still present.
    assert (home / ".codec" / "memory.db").exists()
    assert (home / "Library" / "Logs" / "CODEC" / "launch.log").exists()
    assert (home / "Library" / "LaunchAgents" / "ai.avadigital.codec.codec-dashboard.plist").exists()
    assert (app / "Contents" / "Info.plist").exists()
    # And it tells the user what it would remove.
    assert ".codec" in r.stdout and "LaunchAgents" in r.stdout


def test_no_yes_also_means_dry_run(tmp_path):
    # Running with neither --dry-run nor --yes must NOT delete (safe default).
    home, app = _fixtures(tmp_path)
    r = _run(home, app)
    assert r.returncode == 0
    assert (home / ".codec" / "memory.db").exists(), "default run must not delete"
    assert (app / "Contents" / "Info.plist").exists()


def test_yes_removes_app_agents_logs_but_keeps_user_data(tmp_path):
    home, app = _fixtures(tmp_path)
    r = _run(home, app, "--yes")
    assert r.returncode == 0, f"{r.stderr}\n{r.stdout}"
    assert not app.exists(), "--yes must remove the .app"
    assert not (home / "Library" / "LaunchAgents" / "ai.avadigital.codec.codec-dashboard.plist").exists()
    assert not (home / "Library" / "Logs" / "CODEC").exists()
    # User data preserved without --purge-data.
    assert (home / ".codec" / "memory.db").exists(), "--yes alone must KEEP ~/.codec"


def test_purge_data_removes_user_data(tmp_path):
    home, app = _fixtures(tmp_path)
    r = _run(home, app, "--yes", "--purge-data")
    assert r.returncode == 0, f"{r.stderr}\n{r.stdout}"
    assert not (home / ".codec").exists(), "--yes --purge-data must remove ~/.codec"
