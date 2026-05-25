"""Tests for PR-5I (Audit E / E-9, W5-6) — the headless first-run orchestrator.

The native wizard window is W5-11; here we test the sequencing, the sentinel
idempotency, the permission deep-link map, and a no-side-effect --dry-run.

Reference: docs/W5-6-FIRST-RUN-DESIGN.md, docs/audits/PHASE-1-APPLE-APP.md (E-9).
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIRST_RUN = REPO / "packaging" / "macos" / "first_run.py"


def _load():
    spec = importlib.util.spec_from_file_location("first_run", FIRST_RUN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_script_present_executable():
    assert FIRST_RUN.exists(), "E-9: first_run.py must exist"
    assert os.access(FIRST_RUN, os.X_OK), "must be executable"


def test_launchagents_installer_path_is_correct():
    """Regression: first_run invokes install_launchagents.sh, which lives in the launchd/
    subdir — a dry-run masks a wrong path, but a real --yes run would fail. Pin both the
    file's location and that first_run references the launchd/ subdir."""
    script = REPO / "packaging" / "macos" / "launchd" / "install_launchagents.sh"
    assert script.exists(), f"installer script missing: {script}"
    src = FIRST_RUN.read_text(encoding="utf-8")
    assert ('"launchd", "install_launchagents.sh"' in src
            or "launchd/install_launchagents.sh" in src), \
        "first_run.py must reference install_launchagents.sh in the launchd/ subdir"


def test_sentinel_idempotency(tmp_path):
    f = _load()
    home = tmp_path / "home"
    home.mkdir()
    assert f.is_first_run(str(home)) is True
    f.mark_complete(str(home))
    assert f.is_first_run(str(home)) is False


def test_permission_map_has_panes_and_deep_links():
    f = _load()
    keys = {p["key"] for p in f.PERMISSIONS}
    for needed in ("accessibility", "microphone", "screen_recording", "full_disk_access", "automation"):
        assert needed in keys, f"missing permission {needed}"
    for p in f.PERMISSIONS:
        assert p["deep_link"].startswith("x-apple.systempreferences:"), f"{p['key']} needs a settings deep link"
        assert p.get("reason"), f"{p['key']} needs a why-we-need-it reason"


def test_permission_report_returns_known_states():
    f = _load()
    rep = f.permission_report()
    for p in f.PERMISSIONS:
        assert p["key"] in rep
    assert all(v in ("granted", "denied", "unknown") for v in rep.values())


def test_plan_is_ordered():
    f = _load()
    plan = " | ".join(f.plan()).lower()
    assert "launchagent" in plan or "launchd" in plan
    assert "model" in plan
    assert "permission" in plan
    # install before models before permissions
    assert plan.index("launch") < plan.index("model") < plan.index("permission")


def test_dry_run_no_side_effects(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    r = subprocess.run(
        [sys.executable, str(FIRST_RUN), "--home", str(home), "--dry-run"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, f"dry-run failed: {r.stderr}\n{r.stdout}"
    out = r.stdout.lower()
    assert "fetch_models" in out and ("launchagent" in out or "install_launchagents" in out)
    assert not (home / ".first_run_complete").exists(), "dry-run must NOT mark first-run complete"
