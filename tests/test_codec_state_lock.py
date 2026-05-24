"""Tests for PR-4F (H-2) — codec.py atomic check-then-set helpers + _state_lock.

The `state` dict is mutated by the keyboard listener, wake-word, and worker
threads. Compound check-then-set (e.g. `if not state["recording"]: state
["recording"]=True`) used to be non-atomic → two threads could both pass the
guard and start two sox recordings. The fix extracts the decision into
lock-guarded helpers; these tests pin their exactly-one-winner atomicity.

codec.py does `from pynput import keyboard` (absent locally + in CI). The fixture
installs a CONTAINED pynput stub (monkeypatch.setitem auto-revert + pop `codec`
in teardown) so the real helpers are tested without leaking the stub to
test_graceful_shutdown / test_dispatch_inner_helpers / test_smoke (their baseline
failures must stay — verified zero-new AND zero-fixed).

Reference: docs/PR4F-STATE-LOCK-DESIGN.md, docs/audits/PHASE-1-RELIABILITY.md H-2.
"""
from __future__ import annotations

import importlib
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Pin THIS worktree to the front so the fixture's fresh import of `codec`
# resolves here, not a parent checkout that lacks the fix. No-op in CI.
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) in sys.path:
    sys.path.remove(str(_REPO))
sys.path.insert(0, str(_REPO))


@pytest.fixture
def codec_mod(monkeypatch):
    monkeypatch.setitem(sys.modules, "pynput", MagicMock())
    monkeypatch.setitem(sys.modules, "pynput.keyboard", MagicMock())
    sys.modules.pop("codec", None)  # fresh import under the stub
    mod = importlib.import_module("codec")
    try:
        yield mod
    finally:
        sys.modules.pop("codec", None)  # don't leak the stub-based module


def _exactly_one_winner(fn, *, n=8, rounds=20, reset=None):
    """Fire `fn` from n threads released simultaneously; assert exactly one True,
    over several rounds to exercise the lock's critical section."""
    for _ in range(rounds):
        if reset:
            reset()
        results: list = []
        rlock = threading.Lock()
        barrier = threading.Barrier(n)

        def worker():
            barrier.wait()  # release all threads at once → max contention
            r = fn()
            with rlock:
                results.append(r)

        threads = [threading.Thread(target=worker) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert results.count(True) == 1, (
            f"expected exactly one winner, got {results.count(True)} of {n}"
        )


# ── _try_begin_recording ──────────────────────────────────────────────────────


def test_try_begin_recording_single(codec_mod):
    codec_mod.state["recording"] = False
    assert codec_mod._try_begin_recording() is True
    assert codec_mod.state["recording"] is True
    assert "rec_start" in codec_mod.state
    assert codec_mod._try_begin_recording() is False  # already recording


def test_try_begin_recording_exactly_one_winner(codec_mod):
    _exactly_one_winner(
        codec_mod._try_begin_recording,
        reset=lambda: codec_mod.state.__setitem__("recording", False),
    )


# ── _activate_if_off ──────────────────────────────────────────────────────────


def test_activate_if_off_single(codec_mod):
    codec_mod.state["active"] = False
    assert codec_mod._activate_if_off() is True
    assert codec_mod.state["active"] is True
    assert codec_mod._activate_if_off() is False  # already active


def test_activate_if_off_exactly_one_winner(codec_mod):
    _exactly_one_winner(
        codec_mod._activate_if_off,
        reset=lambda: codec_mod.state.__setitem__("active", False),
    )


# ── _toggle_active ────────────────────────────────────────────────────────────


def test_toggle_active_round_trips(codec_mod):
    codec_mod.state["active"] = False
    assert codec_mod._toggle_active() is True
    assert codec_mod.state["active"] is True
    assert codec_mod._toggle_active() is False
    assert codec_mod.state["active"] is False


# ── source invariants: lock present + helpers wired into the handlers ──────────


def test_state_lock_and_helpers_present():
    src = (_REPO / "codec.py").read_text()
    assert "_state_lock = threading.Lock()" in src, "H-2: _state_lock must exist"
    assert "_try_begin_recording()" in src, "F18 handler must use _try_begin_recording"
    assert "_activate_if_off()" in src, "wake-word path must use _activate_if_off"
    assert "_toggle_active()" in src, "F13 toggle must use _toggle_active"
