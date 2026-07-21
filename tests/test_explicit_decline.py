"""An explicit "no" must decline a consent prompt — immediately and safely.

Before this, strict-consent treated "no" as merely "not the consent verb": it
burned a rejection attempt instead of declining. The rejection counter lives in
memory, so from any fresh process the count reset to zero and the question could
never reach the two-strike timeout — it stayed `pending` forever. One prompt had
been stuck since 2026-07-08, and two more were created while reproducing the
2026-07-21 chat-hang incident.

The safety property that must never regress: a decline can only ever WITHHOLD
consent. It must never be mistaken for approval.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


@pytest.fixture
def au(tmp_path, monkeypatch):
    """codec_ask_user pointed at a throwaway state file."""
    import codec_ask_user as mod
    pq = tmp_path / "pending_questions.json"
    monkeypatch.setattr(mod, "PENDING_QUESTIONS_PATH", pq)
    monkeypatch.setattr(mod, "NOTIFICATIONS_PATH", tmp_path / "notifications.json")
    return mod


def _seed(au, *, strict=True, verb="delete", status="pending"):
    rec = {
        "id": "q_test1234", "question": "CODEC wants to run 'file_ops'",
        "options": None, "asked_at": "2026-07-21T10:00:00.000+00:00",
        "deadline": "2026-07-21T10:10:00.000+00:00", "timeout_seconds": 600,
        "status": status, "consent_strict": strict, "destructive_verb": verb,
        "asked_from": "chat",
    }
    au.PENDING_QUESTIONS_PATH.write_text(
        json.dumps({"schema": 1, "pending_questions": [rec]})
    )
    return rec


def _status(au, qid="q_test1234"):
    data = json.loads(au.PENDING_QUESTIONS_PATH.read_text())
    return next(r["status"] for r in data["pending_questions"] if r["id"] == qid)


@pytest.mark.parametrize("word", ["no", "No", "  NOPE  ", "cancel", "stop",
                                  "abort", "decline", "don't", "skip"])
def test_explicit_no_declines_immediately(au, word):
    _seed(au)
    out = au.submit_answer("q_test1234", word, answered_via="pwa")
    assert out["ok"] is True and out["declined"] is True
    assert _status(au) == "declined", "one refusal must close the question"


def test_decline_is_not_consent(au):
    """The safety property: declining must never read as approval."""
    _seed(au)
    au.submit_answer("q_test1234", "no", answered_via="pwa")
    accepted, _ = au._is_consenting_answer(
        "no", destructive_verb="delete", options=None)
    assert accepted is False, "'no' must never be accepted as consent"


def test_decline_is_idempotent(au):
    _seed(au)
    au.submit_answer("q_test1234", "no", answered_via="pwa")
    second = au.submit_answer("q_test1234", "no", answered_via="pwa")
    assert second["ok"] is False and second["error"] == "already_declined"


def test_real_consent_verb_still_works(au):
    """The gate must still open for the literal verb — this fix only adds a
    way to say NO, it must not weaken the way to say YES."""
    _seed(au, verb="delete")
    out = au.submit_answer("q_test1234", "delete", answered_via="pwa")
    assert out["ok"] is True and _status(au) == "answered"


def test_generic_yes_still_rejected(au):
    """A bare "yes" must still NOT satisfy a strict-consent gate."""
    _seed(au, verb="delete")
    out = au.submit_answer("q_test1234", "yes", answered_via="pwa")
    assert out["ok"] is False and out["reason"] == "ambiguous_consent"
    assert _status(au) == "pending"


def test_declined_records_are_pruned(au):
    """Declined records must age out, not accumulate forever."""
    _seed(au, status="declined")
    data = json.loads(au.PENDING_QUESTIONS_PATH.read_text())
    data["pending_questions"][0]["answered_at"] = "2020-01-01T00:00:00+00:00"
    au._prune_resolved(data)
    assert data["pending_questions"] == [], "old declined records must be pruned"
