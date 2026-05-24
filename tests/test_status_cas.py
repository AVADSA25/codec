"""Tests for PR-7D (Audit B / B-7) — set_status's transition CAS must run under
a cross-process flock so the daemon and the PWA can't clobber each other's
status writes. Reference: docs/PR7D-STATUS-CAS-DESIGN.md, PHASE-1-PROJECTS-PILOT.md (B-7).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path[:] = [p for p in sys.path if p != str(REPO)]
sys.path.insert(0, str(REPO))

import codec_agent_plan as cap  # noqa: E402
import codec_jsonstore  # noqa: E402


@pytest.fixture
def temp_codec_dir(tmp_path, monkeypatch):
    import codec_audit
    monkeypatch.setattr(cap, "_CODEC_DIR", tmp_path)
    monkeypatch.setattr(cap, "_AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(cap, "_GLOBAL_GRANTS_PATH", tmp_path / "agent_global_grants.json")
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", tmp_path / "audit.log")
    return tmp_path


class _LockRecorder:
    def __init__(self):
        self.entered = []

    def __call__(self, path):
        self._path = str(path)
        return self

    def __enter__(self):
        self.entered.append(self._path)
        return self

    def __exit__(self, *a):
        return False


def test_set_status_acquires_manifest_flock(temp_codec_dir, monkeypatch):
    cap.save_manifest("a1", {"agent_id": "a1", "status": "running", "title": "x"})
    rec = _LockRecorder()
    monkeypatch.setattr(codec_jsonstore, "file_lock", rec)
    cap.set_status("a1", "paused")
    assert rec.entered, "set_status must acquire a cross-process lock (B-7)"
    assert any("manifest.json" in p and "a1" in p for p in rec.entered), \
        "the lock must be on the agent's manifest.json"


def test_set_status_legal_transition_persists(temp_codec_dir):
    cap.save_manifest("a2", {"agent_id": "a2", "status": "running", "title": "x"})
    cap.set_status("a2", "paused")
    assert cap.load_manifest("a2")["status"] == "paused"


def test_set_status_illegal_transition_still_raises(temp_codec_dir):
    cap.save_manifest("a3", {"agent_id": "a3", "status": "running", "title": "x"})
    with pytest.raises(cap.InvalidStatusTransition):
        cap.set_status("a3", "draft_pending")
    assert cap.load_manifest("a3")["status"] == "running", "illegal move must not write"
