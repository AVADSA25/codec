"""Tests for PR-7B (Audit B / B-2 part 1) — destructiveness is derived
server-side (OR-only), so the agent can't bypass the consent gate by emitting
is_destructive=false on a dangerous skill or an irreversible task.

Reference: docs/PR7B-DERIVE-DESTRUCTIVE-DESIGN.md, docs/audits/PHASE-1-PROJECTS-PILOT.md (B-2).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:] = [p for p in sys.path if p != str(REPO)]
sys.path.insert(0, str(REPO))

import codec_agent_runner as car  # noqa: E402


def _a(skill, task, is_destructive=False):
    return car.Action(skill=skill, task=task, is_destructive=is_destructive)


# ---- _effective_destructive (the OR-only derivation) ----------------------

def test_http_blocked_skill_forces_destructive():
    # terminal / python_exec / process control are always destructive,
    # even if the LLM says otherwise.
    assert car._effective_destructive(_a("terminal", "echo hi", is_destructive=False)) is True
    assert car._effective_destructive(_a("python_exec", "print(1)", is_destructive=False)) is True


def test_destructive_verb_in_task_forces_destructive():
    assert car._effective_destructive(_a("file_ops", "delete ~/Documents/old.txt")) is True
    assert car._effective_destructive(_a("imessage_send", "send the report to Bob")) is True


def test_benign_action_is_not_destructive():
    assert car._effective_destructive(_a("web_search", "look up the weather in Marbella")) is False
    assert car._effective_destructive(_a("notes", "write a summary of the meeting")) is False


def test_llm_flag_alone_still_destructive():
    assert car._effective_destructive(_a("notes", "tidy up", is_destructive=True)) is True


# ---- gate routing (OR-only: can't be downgraded by the LLM) ---------------

def test_gate_routes_dangerous_skill_to_consent(monkeypatch):
    calls = []
    monkeypatch.setattr(car, "_strict_consent",
                        lambda action, deadline=0: calls.append(action) or car.ConsentResult(approved=True))
    car._enforce_destructive_gate(_a("terminal", "rm stuff", is_destructive=False))
    assert calls, "a dangerous skill must reach strict-consent even with is_destructive=False"


def test_gate_skips_consent_for_benign(monkeypatch):
    calls = []
    monkeypatch.setattr(car, "_strict_consent",
                        lambda action, deadline=0: calls.append(action) or car.ConsentResult(approved=True))
    res = car._enforce_destructive_gate(_a("web_search", "weather today", is_destructive=False))
    assert not calls, "benign action must NOT trigger consent"
    assert res.approved is True
