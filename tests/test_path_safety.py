"""Tests for PR-7L (Audit B / B-15 + B-18) — open-folder realpath-confines project_dir
under the configured project root (and rejects symlinks) before running `open`, and
_path_allowed enforces a specific glob grant instead of collapsing it to the directory
root (while keeping PR-1D's `..`/realpath safety).

Reference: docs/PR7L-PATH-SAFETY-DESIGN.md.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path[:] = [p for p in sys.path if p != str(REPO)]
sys.path.insert(0, str(REPO))

import codec_agent_plan as cap  # noqa: E402
import codec_agent_runner as car  # noqa: E402


# ── B-18: _path_allowed glob precision ────────────────────────────────────────
def test_specific_glob_enforced(tmp_path):
    """A grant of `{p}/*.md` must NOT authorize a `.key` file under the same dir
    (B-18 — was collapsed to the directory root)."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.md").write_text("x")
    (proj / "a.key").write_text("x")
    grant = f"{proj}/*.md"
    ok_md, _ = car._path_allowed(f"{proj}/a.md", [grant])
    ok_key, reason = car._path_allowed(f"{proj}/a.key", [grant])
    assert ok_md is True, "a *.md grant must allow a .md file"
    assert ok_key is False, "a *.md grant must NOT authorize a .key file (B-18)"


def test_recursive_glob_allows_subtree(tmp_path):
    """A `{p}/**` grant (production's default write_paths) still authorizes the whole
    subtree — no regression to the common case."""
    proj = tmp_path / "proj"
    (proj / "sub" / "deep").mkdir(parents=True)
    ok, _ = car._path_allowed(f"{proj}/sub/deep/f.txt", [f"{proj}/**"])
    assert ok is True


def test_plain_dir_grant_allows_subtree(tmp_path):
    """A plain directory grant (no glob) still authorizes its subtree."""
    proj = tmp_path / "proj"
    proj.mkdir()
    ok, _ = car._path_allowed(f"{proj}/x.txt", [str(proj)])
    assert ok is True


def test_dotdot_still_rejected(tmp_path):
    """PR-1D must not be regressed: a `..` segment is rejected outright."""
    ok, reason = car._path_allowed(f"{tmp_path}/proj/../etc/x", [f"{tmp_path}/proj/**"])
    assert ok is False and reason == "path_traversal"


# ── B-15: open_folder confinement ─────────────────────────────────────────────
@pytest.fixture
def _routes(tmp_path, monkeypatch):
    import codec_audit
    monkeypatch.setattr(cap, "_AGENTS_DIR", tmp_path / "agents")
    root = tmp_path / "codec-projects"
    root.mkdir()
    monkeypatch.setattr(cap, "_PROJECT_ROOT", root)
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", tmp_path / "audit.log")
    import routes.agents as ra
    return ra, root


def test_open_folder_allows_in_root(monkeypatch, _routes, tmp_path):
    ra, root = _routes
    pdir = root / "myproj"
    pdir.mkdir()
    cap.save_manifest("a1", {"agent_id": "a1", "title": "x", "project_dir": str(pdir)})
    calls = []
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: calls.append(a))
    ra.open_folder("a1")
    assert calls, "open must be invoked for a project_dir inside the project root"


def test_open_folder_rejects_outside_root(monkeypatch, _routes, tmp_path):
    ra, root = _routes
    evil = tmp_path / "evil"  # a real dir OUTSIDE the project root
    evil.mkdir()
    cap.save_manifest("a2", {"agent_id": "a2", "title": "x", "project_dir": str(evil)})
    calls = []
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: calls.append(a))
    resp = ra.open_folder("a2")
    assert not calls, "open must NOT be invoked for a dir outside the project root (B-15)"
    assert getattr(resp, "status_code", 200) == 400


def test_open_folder_rejects_symlink(monkeypatch, _routes, tmp_path):
    ra, root = _routes
    realdir = root / "realdir"
    realdir.mkdir()
    link = root / "sneaky"  # symlink INSIDE root → only the islink check catches it
    os.symlink(realdir, link)
    cap.save_manifest("a3", {"agent_id": "a3", "title": "x", "project_dir": str(link)})
    calls = []
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: calls.append(a))
    resp = ra.open_folder("a3")
    assert not calls, "open must reject a symlinked project_dir (B-15)"
    assert getattr(resp, "status_code", 200) == 400
