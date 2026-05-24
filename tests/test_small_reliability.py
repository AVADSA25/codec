"""Tests for PR-4I (M-3 / M-4 / L-1 / L-2) — small Wave-4 reliability fixes.

  * M-3 codec_audit._rotate_if_needed      → log.warning on rename failure
  * M-4 codec_observer.run_daemon          → whole iteration body inside try
  * L-1 codec_ask_user._atomic_write_text  → fsync before os.replace
  * L-2 codec_autopilot._load_state/_tick  → corrupt state refuses to fire

Reference: docs/PR4I-SMALL-RELIABILITY-DESIGN.md, docs/audits/PHASE-1-RELIABILITY.md.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# Force THIS worktree to the front of sys.path and import the modules-under-test
# here (at collection time) so they cache from the worktree. Without this, when
# the worktree is nested under another checkout of the repo, a module no earlier
# test imported (codec_autopilot) can resolve to the parent checkout — silently
# testing stale code. (A no-op in CI's single standalone checkout.)
if str(REPO) in sys.path:
    sys.path.remove(str(REPO))
sys.path.insert(0, str(REPO))
import codec_audit  # noqa: E402,F401
import codec_observer  # noqa: E402,F401
import codec_ask_user  # noqa: E402,F401
import codec_autopilot  # noqa: E402,F401


# ── M-3: audit rotation failure is logged, not silently swallowed ─────────────


def test_rotation_failure_is_logged(monkeypatch, tmp_path, caplog):
    log_path = tmp_path / "audit.log"
    log_path.write_text('{"old": true}\n')
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", log_path)
    monkeypatch.setattr(codec_audit, "_AUDIT_DIR", tmp_path)
    # Backdate so _rotate_if_needed decides to rotate.
    old = time.time() - 2 * 86400
    os.utime(log_path, (old, old))

    def _boom_rename(self, target):
        raise OSError("disk full")
    monkeypatch.setattr(codec_audit.Path, "rename", _boom_rename)

    with caplog.at_level(logging.WARNING):
        codec_audit._rotate_if_needed()  # must not raise

    assert any("rotat" in r.message.lower() or "disk full" in r.message.lower()
               for r in caplog.records), (
        "M-3: a rotation failure must emit a WARNING (was silently swallowed)"
    )


# ── M-4: observer iteration body is inside the try (source-invariant) ─────────


def test_observer_iteration_body_guarded():
    src = (REPO / "codec_observer.py").read_text()
    body = src[src.index("def run_daemon("):]
    body = body[:body.index("\ndef _maybe_fire_shift_report")]
    idle_line = next(ln for ln in body.splitlines()
                     if "_idle_seconds()" in ln and not ln.lstrip().startswith("#"))
    sleep_line = next(ln for ln in body.splitlines() if "time.sleep(cadence)" in ln)
    idle_indent = len(idle_line) - len(idle_line.lstrip())
    sleep_indent = len(sleep_line) - len(sleep_line.lstrip())
    assert idle_indent > sleep_indent, (
        "M-4: _idle_seconds() must sit inside the try (deeper than the "
        "time.sleep(cadence) at the while-body level) so it can't kill the loop"
    )


# ── L-1: codec_ask_user atomic write fsyncs ───────────────────────────────────


def test_atomic_write_text_fsyncs_and_replaces(monkeypatch, tmp_path):
    fsync_fds = []
    real_fsync = os.fsync
    monkeypatch.setattr(codec_ask_user.os, "fsync",
                        lambda fd: (fsync_fds.append(fd), real_fsync(fd)))
    target = tmp_path / "state.json"
    codec_ask_user._atomic_write_text(target, '{"a": 1}')
    assert target.read_text() == '{"a": 1}'
    assert fsync_fds, "L-1: must fsync before os.replace"
    assert not (tmp_path / "state.tmp").exists(), "tmp must be replaced, none left behind"


def test_ask_user_has_no_raw_write_text():
    src = (REPO / "codec_ask_user.py").read_text()
    assert "tmp.write_text(" not in src, (
        "L-1: raw tmp.write_text (no fsync) must be replaced by _atomic_write_text"
    )


# ── L-2: autopilot refuses to fire on a corrupt state file ────────────────────


def test_load_state_corrupt_returns_sentinel(monkeypatch, tmp_path):
    p = tmp_path / "autopilot_state.json"
    p.write_text("{ this is not valid json")
    monkeypatch.setattr(codec_autopilot, "STATE", p)
    assert codec_autopilot._load_state().get("__corrupt__") is True


def test_load_state_missing_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(codec_autopilot, "STATE", tmp_path / "absent.json")
    assert codec_autopilot._load_state() == {}


def test_load_state_valid_roundtrips(monkeypatch, tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"briefing": "2026-05-24"}))
    monkeypatch.setattr(codec_autopilot, "STATE", p)
    assert codec_autopilot._load_state() == {"briefing": "2026-05-24"}


def test_tick_refuses_to_fire_on_corrupt_state(monkeypatch):
    fired = []
    monkeypatch.setattr(codec_autopilot, "_fire", lambda trig, reg: fired.append(trig))
    cfg = {
        "enabled": True, "timezone": "UTC",
        "triggers": [{"name": "morning", "at": "00:00", "days": "daily"}],
    }
    codec_autopilot._tick(cfg, {"__corrupt__": True}, registry=None)
    assert fired == [], "L-2: a corrupt state file must refuse to fire any trigger"
