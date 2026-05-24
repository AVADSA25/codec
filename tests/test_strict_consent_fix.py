"""Tests for PR-7A (Audit B / B-1) — the destructive-consent gate must call the
REAL codec_ask_user.ask, not a non-existent `strict_consent_gate`.

These exercise the real `_strict_consent` body (the gap B-1 left: every existing
test mocks the wrapper, so the phantom import was never hit).

Reference: docs/PR7A-STRICT-CONSENT-DESIGN.md, docs/audits/PHASE-1-PROJECTS-PILOT.md (B-1).
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:] = [p for p in sys.path if p != str(REPO)]
sys.path.insert(0, str(REPO))

import codec_agent_runner as car  # noqa: E402
import codec_ask_user  # noqa: E402


def _destructive_action():
    return car.Action(skill="file_ops", task="delete ~/Documents/old.txt", is_destructive=True)


def _recording_ask(return_value):
    calls = []

    def fake_ask(question, **kwargs):
        calls.append({"question": question, **kwargs})
        return return_value

    fake_ask.calls = calls
    return fake_ask


def test_strict_consent_approved_on_verb_match(monkeypatch):
    ask = _recording_ask("confirm")  # verb-matched answer ⇒ ask() returns the answer
    monkeypatch.setattr(codec_ask_user, "ask", ask)
    res = car._strict_consent(_destructive_action())
    assert res.approved is True, "verb-matched consent must approve"
    assert res.timed_out is False
    assert ask.calls, "must actually call codec_ask_user.ask"
    assert ask.calls[0].get("destructive") is True, "must call ask in destructive mode"


def test_strict_consent_timeout_maps_to_blocked(monkeypatch):
    ask = _recording_ask(codec_ask_user.TIMEOUT_SENTINEL)
    monkeypatch.setattr(codec_ask_user, "ask", ask)
    res = car._strict_consent(_destructive_action())
    assert ask.calls, "must call ask"
    assert res.approved is False and res.timed_out is True


def test_strict_consent_disabled_is_blocked_never_approved(monkeypatch):
    ask = _recording_ask(codec_ask_user.DISABLED_SENTINEL)
    monkeypatch.setattr(codec_ask_user, "ask", ask)
    res = car._strict_consent(_destructive_action())
    assert ask.calls, "must call ask"
    assert res.approved is False, "ask_user disabled must NEVER auto-approve a destructive op"
    assert res.timed_out is True


def test_no_phantom_strict_consent_gate():
    # B-1 regression guard: the wrapper must not reference the non-existent symbol,
    # and the symbol genuinely does not exist in codec_ask_user.
    src = inspect.getsource(car._strict_consent)
    assert "strict_consent_gate" not in src, "must not import the phantom strict_consent_gate"
    assert not hasattr(codec_ask_user, "strict_consent_gate"), "phantom symbol must stay absent"
