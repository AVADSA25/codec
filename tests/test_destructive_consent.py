"""Phase 1 Step 3 §7 — destructive-action strict-consent tests.

Validates docs/PHASE1-STEP3-DESIGN.md §1.7:
    - Literal verb-match passes (case-insensitive)
    - Generic affirmative ("yes"/"ok"/"yeah") rejected with re-prompt response
    - Two strikes → ambiguous_consent timeout
    - _HTTP_BLOCKED tool name auto-triggers destructive=True
    - Caller-supplied destructive=True triggers it directly
    - destructive_verb auto-extracted from question when not specified
    - Voice path bypasses fuzzy-match (codec_voice._resolve_voice_option_choice
      returns the raw transcript when strict=True)
    - Option-label exact match also accepts (so PWA button click works)

Each rejection returns a dict with rejected=True, reason='ambiguous_consent',
remaining_attempts; the on-disk record stays status='pending' until either
acceptance or two-strike timeout. The waiter Event is set on rejection so
the caller's polling loop can re-check the rejection count.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_REPO_STR = str(_REPO)
while _REPO_STR in sys.path:
    sys.path.remove(_REPO_STR)
sys.path.insert(0, _REPO_STR)
for _stale in ("codec_audit", "codec_ask_user", "codec_agents", "codec_voice"):
    sys.modules.pop(_stale, None)

import codec_audit
import codec_ask_user


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_audit_log(tmp_path, monkeypatch):
    log = tmp_path / "audit.log"
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", log)
    return log


@pytest.fixture
def temp_askuser_paths(tmp_path, monkeypatch):
    pq = tmp_path / "pending_questions.json"
    nf = tmp_path / "notifications.json"
    cfg = tmp_path / "config.json"
    monkeypatch.setattr(codec_ask_user, "PENDING_QUESTIONS_PATH", pq)
    monkeypatch.setattr(codec_ask_user, "NOTIFICATIONS_PATH", nf)
    monkeypatch.setattr(codec_ask_user, "CONFIG_PATH", cfg)
    codec_ask_user._WAITERS.clear()
    codec_ask_user._REJECTION_COUNT.clear()
    return pq, nf, cfg


def _await_qid(pq_path: Path, timeout=2.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pq_path.exists():
            data = json.loads(pq_path.read_text())
            pending = data.get("pending_questions", [])
            if pending and pending[-1].get("status") == "pending":
                return pending[-1]["id"]
        time.sleep(0.05)
    raise AssertionError("no pending question appeared in time")


def _records(audit_log: Path) -> list[dict]:
    if not audit_log.exists():
        return []
    return [json.loads(l) for l in audit_log.read_text(encoding="utf-8").splitlines() if l.strip()]


# ── §1.7 _is_consenting_answer unit ──────────────────────────────────────────

def test_consent_literal_verb_match_accepted():
    """Answer text contains the destructive verb literally → accepted."""
    accepted, normalized = codec_ask_user._is_consenting_answer(
        "delete the database", destructive_verb="delete", options=None)
    assert accepted is True
    assert normalized == "delete the database"


def test_consent_verb_match_case_insensitive():
    accepted, _ = codec_ask_user._is_consenting_answer(
        "DELETE", destructive_verb="delete", options=None)
    assert accepted is True


def test_consent_generic_yes_rejected():
    """Plain "yes" / "ok" / "sure" alone — rejected on strict-consent."""
    for token in ("yes", "yeah", "yep", "ok", "okay", "sure", "fine"):
        accepted, _ = codec_ask_user._is_consenting_answer(
            token, destructive_verb="delete", options=None)
        assert accepted is False, f"{token!r} should reject"


def test_consent_empty_answer_rejected():
    accepted, _ = codec_ask_user._is_consenting_answer(
        "", destructive_verb="delete", options=None)
    assert accepted is False
    accepted, _ = codec_ask_user._is_consenting_answer(
        "   ", destructive_verb="delete", options=None)
    assert accepted is False


def test_consent_option_label_exact_match_accepted():
    """A button-click sends the option label literally — that bypasses the
    verb rule (the user explicitly chose the destructive option)."""
    accepted, normalized = codec_ask_user._is_consenting_answer(
        "Delete it", destructive_verb="delete",
        options=["Delete it", "Cancel"])
    assert accepted is True
    assert normalized == "Delete it"


def test_consent_freetext_accepted_as_non_confirming():
    """Free-text that's NOT generic-yes and DOESN'T contain the verb is
    accepted as the user's actual answer (e.g. "no don't delete it")."""
    accepted, normalized = codec_ask_user._is_consenting_answer(
        "no don't do that", destructive_verb="delete", options=None)
    assert accepted is True
    assert normalized == "no don't do that"


# ── _default_destructive_verb auto-extraction ────────────────────────────────

def test_default_verb_extracts_destructive_word():
    """Question contains 'delete' → verb='delete'."""
    assert codec_ask_user._default_destructive_verb(
        "Should I delete the file?") == "delete"
    assert codec_ask_user._default_destructive_verb(
        "Send the email to the customer?") == "send"
    assert codec_ask_user._default_destructive_verb(
        "Transfer $500 to the savings account?") == "transfer"


def test_default_verb_falls_back_to_confirm_for_no_match():
    """No destructive hint in the question → 'confirm' fallback OR a generic
    4+ letter verb. Either way, the caller can override via destructive_verb=."""
    v = codec_ask_user._default_destructive_verb("?")
    assert v == "confirm"


# ── _is_destructive_tool: _HTTP_BLOCKED auto-trigger ──────────────────────────

def test_is_destructive_tool_triggers_on_http_blocked():
    """A tool name in codec_config._HTTP_BLOCKED → destructive auto-True."""
    # Sample from the actual list (don't edit it — _HTTP_BLOCKED is the
    # authoritative source).
    assert codec_ask_user._is_destructive_tool("python_exec") is True
    assert codec_ask_user._is_destructive_tool("terminal") is True
    assert codec_ask_user._is_destructive_tool("ax_control") is True


def test_is_destructive_tool_false_for_safe_tools():
    """Tools NOT in the list — destructive flag stays False (caller can still
    set it explicitly)."""
    assert codec_ask_user._is_destructive_tool("weather") is False
    assert codec_ask_user._is_destructive_tool("calculator") is False
    assert codec_ask_user._is_destructive_tool(None) is False
    assert codec_ask_user._is_destructive_tool("") is False


# ── End-to-end: caller destructive=True triggers strict-consent ──────────────

def test_caller_destructive_true_engages_strict_gate(temp_audit_log,
                                                       temp_askuser_paths):
    """destructive=True at call site → record.consent_strict=True → first
    generic-yes rejected with rejected=True/reason=ambiguous_consent."""
    pq_path, _, _ = temp_askuser_paths
    holder = {}
    def caller():
        holder["v"] = codec_ask_user.ask(
            "Delete the production user?",
            options=["delete", "cancel"], timeout=300,
            destructive=True,
        )
    t = threading.Thread(target=caller, daemon=True); t.start()
    qid = _await_qid(pq_path)

    # Verify the record marks strict-consent + auto-extracted verb.
    rec = json.loads(pq_path.read_text())["pending_questions"][0]
    assert rec["consent_strict"] is True
    assert rec["destructive_verb"] == "delete"

    # First strike — generic "yes" rejected.
    r1 = codec_ask_user.submit_answer(qid, "yes")
    assert r1["ok"] is False
    assert r1["rejected"] is True
    assert r1["reason"] == "ambiguous_consent"
    assert r1["remaining_attempts"] == 1
    # Second strike — ambiguous_consent timeout fires.
    r2 = codec_ask_user.submit_answer(qid, "ok")
    assert r2["rejected"] is True
    assert r2["remaining_attempts"] == 0

    t.join(timeout=4)
    assert not t.is_alive(), "two-strike should release the caller"
    assert holder["v"] == codec_ask_user.TIMEOUT_SENTINEL


def test_caller_destructive_with_explicit_verb(temp_audit_log,
                                                  temp_askuser_paths):
    """Explicit destructive_verb wins over auto-extraction."""
    pq_path, _, _ = temp_askuser_paths
    holder = {}
    def caller():
        holder["v"] = codec_ask_user.ask(
            "Should I do the thing?",   # no destructive verb in question text
            timeout=300,
            destructive=True,
            destructive_verb="purge",
        )
    t = threading.Thread(target=caller, daemon=True); t.start()
    qid = _await_qid(pq_path)
    rec = json.loads(pq_path.read_text())["pending_questions"][0]
    assert rec["destructive_verb"] == "purge"
    # "purge" literal → accepted.
    r = codec_ask_user.submit_answer(qid, "purge it")
    assert r["ok"] is True
    t.join(timeout=3)
    assert holder["v"] == "purge it"


def test_destructive_accepted_answer_unblocks(temp_audit_log,
                                                temp_askuser_paths):
    """Saying the verb literally accepts immediately — no rejection cycle."""
    pq_path, _, _ = temp_askuser_paths
    holder = {}
    def caller():
        holder["v"] = codec_ask_user.ask(
            "Delete the staging row?",
            timeout=300, destructive=True,
        )
    t = threading.Thread(target=caller, daemon=True); t.start()
    qid = _await_qid(pq_path)
    r = codec_ask_user.submit_answer(qid, "yes go ahead and delete it")
    assert r["ok"] is True
    t.join(timeout=3)
    assert "delete" in holder["v"].lower()


def test_destructive_first_strike_rejection_keeps_record_pending(
        temp_audit_log, temp_askuser_paths):
    """After the first generic-yes rejection, the on-disk record stays
    status='pending' so the user can answer again."""
    pq_path, _, _ = temp_askuser_paths
    holder = {}
    def caller():
        holder["v"] = codec_ask_user.ask(
            "Delete the database?", timeout=300, destructive=True)
    t = threading.Thread(target=caller, daemon=True); t.start()
    qid = _await_qid(pq_path)

    codec_ask_user.submit_answer(qid, "yes")
    rec = json.loads(pq_path.read_text())["pending_questions"][0]
    assert rec["status"] == "pending"
    # Now answer correctly — accepts and unblocks.
    r = codec_ask_user.submit_answer(qid, "delete")
    assert r["ok"] is True
    t.join(timeout=3)
    assert holder["v"] == "delete"


# ── Voice path bypasses fuzzy match on strict-consent ────────────────────────

def test_voice_resolver_bypasses_fuzzy_when_strict():
    """codec_voice._resolve_voice_option_choice returns the RAW transcript when
    strict=True, so the codec_ask_user.submit_answer literal-verb gate sees
    the user's actual spoken text, not a fuzzy-matched option label."""
    import codec_voice
    transcript = "yeah ok go for it"
    options = ["delete", "cancel"]
    # Non-strict path: this would synonym-match to "delete" or fall through.
    # Strict path: returns the raw transcript untouched.
    out = codec_voice._resolve_voice_option_choice(
        transcript, options, strict=True, destructive_verb="delete")
    assert out == transcript


def test_voice_resolver_non_strict_does_synonym_match():
    """Sanity check for the non-strict path — synonym match works."""
    import codec_voice
    out = codec_voice._resolve_voice_option_choice(
        "yes go ahead", ["approve", "reject"], strict=False)
    assert out == "approve"


# ── Audit emit: timeout reason='ambiguous_consent' ───────────────────────────

def test_two_strike_emits_ambiguous_consent_timeout(temp_audit_log,
                                                      temp_askuser_paths):
    """The terminal audit line on two strikes carries reason='ambiguous_consent'
    AND consent_rejection_count=2 in extra."""
    pq_path, _, _ = temp_askuser_paths
    holder = {}
    def caller():
        holder["v"] = codec_ask_user.ask(
            "Delete the row?", timeout=300, destructive=True)
    t = threading.Thread(target=caller, daemon=True); t.start()
    qid = _await_qid(pq_path)
    codec_ask_user.submit_answer(qid, "yes")
    codec_ask_user.submit_answer(qid, "ok")
    t.join(timeout=4)
    recs = _records(temp_audit_log)
    timeouts = [r for r in recs
                if r.get("event") == codec_audit.ASKUSER_EVENT_TIMEOUT]
    assert len(timeouts) == 1
    extra = timeouts[0]["extra"]
    assert extra["reason"] == "ambiguous_consent"
    assert extra["consent_rejection_count"] == 2


# ── Idempotency on already-timed-out question ────────────────────────────────

def test_submit_after_timeout_returns_already_timed_out(temp_audit_log,
                                                          temp_askuser_paths):
    """A third strike (or any post-timeout submit) returns already_timed_out
    with no state change."""
    pq_path, _, _ = temp_askuser_paths
    holder = {}
    def caller():
        holder["v"] = codec_ask_user.ask(
            "Delete it?", timeout=300, destructive=True)
    t = threading.Thread(target=caller, daemon=True); t.start()
    qid = _await_qid(pq_path)
    codec_ask_user.submit_answer(qid, "yes")
    codec_ask_user.submit_answer(qid, "ok")
    t.join(timeout=4)
    # Now post a 3rd answer — already terminal.
    r3 = codec_ask_user.submit_answer(qid, "delete")
    assert r3["ok"] is False
    assert r3["error"] == "already_timed_out"
