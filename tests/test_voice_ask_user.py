"""Phase 1 Step 3 §7 — voice AskUserQuestion fuzzy-match tests.

Validates docs/PHASE1-STEP3-DESIGN.md §5.3 + §5.3.1:
    - 3-tier fuzzy match: exact substring → synonym dict → Levenshtein
    - Strict-consent (§1.7) BYPASSES fuzzy match (returns raw transcript)
    - Active voice session marker (~/.codec/voice_session.json) write/clear
    - _poll_pending_question_for_voice picks correct records:
        * matches by correlation_id, OR
        * picks asked_from=='crew' / 'voice' background ops
    - Levenshtein helper: correct edit distance + cap at 100 chars

The voice handler methods (_handle_voice_ask_user_answer, _announce_pending_question)
require a real VoicePipeline + WebSocket, so we test the resolver + marker +
pollers in isolation. The full session integration is exercised by
tests/test_voice_pipeline.py (existing).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import codec_voice
import codec_ask_user


# ── Levenshtein helper ────────────────────────────────────────────────────────

def test_levenshtein_identical_strings():
    assert codec_voice._levenshtein("hello", "hello") == 0


def test_levenshtein_simple_distances():
    assert codec_voice._levenshtein("kitten", "sitting") == 3
    assert codec_voice._levenshtein("flaw", "lawn") == 2
    assert codec_voice._levenshtein("a", "b") == 1


def test_levenshtein_empty_strings():
    assert codec_voice._levenshtein("", "") == 0
    assert codec_voice._levenshtein("abc", "") == 3
    assert codec_voice._levenshtein("", "xyz") == 3


def test_levenshtein_caps_at_100_chars():
    """Per the helper's docstring, both inputs cap at 100 chars."""
    long_a = "a" * 200
    long_b = "b" * 200
    # Both truncate to 100. Edit distance becomes 100 (replace each).
    assert codec_voice._levenshtein(long_a, long_b) == 100


# ── Fuzzy resolver: tier 1 — exact substring of option label ─────────────────

def test_resolver_exact_substring_match():
    """The transcript contains the lowercased option label → matches."""
    out = codec_voice._resolve_voice_option_choice(
        "Yes please approve it", ["approve", "reject"], strict=False)
    assert out == "approve"


def test_resolver_exact_match_case_insensitive():
    out = codec_voice._resolve_voice_option_choice(
        "DELETE THE FILE", ["delete", "cancel"], strict=False)
    assert out == "delete"


# ── Fuzzy resolver: tier 2 — synonym dict ────────────────────────────────────

def test_resolver_synonym_yes_maps_to_approve():
    """'go ahead' is a synonym for 'approve'. The option must contain
    'approve' for the dict to fire."""
    out = codec_voice._resolve_voice_option_choice(
        "go ahead", ["approve", "reject"], strict=False)
    assert out == "approve"


def test_resolver_synonym_skip_maps_to_reject():
    out = codec_voice._resolve_voice_option_choice(
        "skip it", ["approve", "reject"], strict=False)
    assert out == "reject"


def test_resolver_synonym_change_maps_to_modify():
    """'change' / 'edit' are synonyms for 'modify'."""
    out = codec_voice._resolve_voice_option_choice(
        "change it", ["approve", "reject", "modify"], strict=False)
    assert out == "modify"


def test_resolver_synonym_with_punctuation():
    """Resolver strips simple punctuation before matching."""
    out = codec_voice._resolve_voice_option_choice(
        "Go ahead!", ["approve", "reject"], strict=False)
    assert out == "approve"


# ── Fuzzy resolver: tier 3 — Levenshtein fallback ────────────────────────────

def test_resolver_levenshtein_close_match():
    """One-letter typo: 'aprove' → 'approve' (dist=1, label_len=7, 30%≈2)."""
    out = codec_voice._resolve_voice_option_choice(
        "aprove", ["approve", "reject"], strict=False)
    assert out == "approve"


def test_resolver_levenshtein_too_far_returns_raw():
    """Distance > 30% of label length → no match; returns raw transcript."""
    out = codec_voice._resolve_voice_option_choice(
        "completelydifferent", ["approve", "reject"], strict=False)
    # Falls through tier 3, returns raw transcript stripped.
    assert out == "completelydifferent"


def test_resolver_no_options_returns_raw():
    out = codec_voice._resolve_voice_option_choice(
        "anything goes here", [], strict=False)
    assert out == "anything goes here"


def test_resolver_none_options_returns_raw():
    """Passing None for options → returns raw."""
    out = codec_voice._resolve_voice_option_choice(
        "hello world", None, strict=False)
    assert out == "hello world"


def test_resolver_empty_transcript_returns_empty():
    out = codec_voice._resolve_voice_option_choice(
        "", ["approve"], strict=False)
    assert out == ""
    out = codec_voice._resolve_voice_option_choice(
        "   ", ["approve"], strict=False)
    assert out == ""


# ── §1.7 strict-consent BYPASS ────────────────────────────────────────────────

def test_resolver_strict_returns_raw_unmodified():
    """strict=True → the resolver passes through the raw transcript so the
    codec_ask_user.submit_answer literal-verb gate evaluates it."""
    out = codec_voice._resolve_voice_option_choice(
        "yeah ok do it", ["delete", "cancel"], strict=True,
        destructive_verb="delete")
    assert out == "yeah ok do it"


def test_resolver_strict_does_not_synonym_match():
    """In strict mode, "go ahead" does NOT map to "approve" — strict
    requires the literal verb."""
    out = codec_voice._resolve_voice_option_choice(
        "go ahead", ["approve", "delete"], strict=True,
        destructive_verb="delete")
    # Returned raw — would then be rejected by submit_answer because no "delete".
    assert out == "go ahead"


def test_resolver_strict_passes_verb_through():
    """Strict + transcript contains verb → returned raw, then codec_ask_user
    submit_answer's verb-match accepts it."""
    out = codec_voice._resolve_voice_option_choice(
        "delete it now", ["delete", "cancel"], strict=True,
        destructive_verb="delete")
    assert out == "delete it now"
    # Round-trip through the consent gate to confirm it's accepted.
    accepted, _ = codec_ask_user._is_consenting_answer(
        out, destructive_verb="delete", options=["delete", "cancel"])
    assert accepted is True


# ── Voice session marker ─────────────────────────────────────────────────────

def test_voice_session_marker_touch_and_clear(tmp_path, monkeypatch):
    """_touch writes a JSON file with session_id+started_at; _clear removes it."""
    marker = tmp_path / "voice_session.json"
    monkeypatch.setattr(codec_voice, "_VOICE_SESSION_MARKER", str(marker))
    assert not marker.exists()
    codec_voice._touch_voice_session_marker("sess_test_123")
    assert marker.exists()
    data = json.loads(marker.read_text())
    assert data["session_id"] == "sess_test_123"
    assert "started_at" in data
    codec_voice._clear_voice_session_marker()
    assert not marker.exists()


def test_voice_session_marker_clear_idempotent(tmp_path, monkeypatch):
    """Calling _clear when no marker exists is safe (no-op)."""
    marker = tmp_path / "voice_session.json"
    monkeypatch.setattr(codec_voice, "_VOICE_SESSION_MARKER", str(marker))
    # Marker doesn't exist; calling clear should not raise.
    codec_voice._clear_voice_session_marker()  # should not raise
    assert not marker.exists()


def test_voice_session_marker_touch_overwrites_existing(tmp_path, monkeypatch):
    """A second _touch overwrites the previous marker."""
    marker = tmp_path / "voice_session.json"
    monkeypatch.setattr(codec_voice, "_VOICE_SESSION_MARKER", str(marker))
    codec_voice._touch_voice_session_marker("first_sess")
    codec_voice._touch_voice_session_marker("second_sess")
    data = json.loads(marker.read_text())
    assert data["session_id"] == "second_sess"


# ── _poll_pending_question_for_voice ─────────────────────────────────────────

class _FakeVoicePipeline:
    """Minimal stand-in that has the attributes _poll_pending_question_for_voice
    needs: ``self._cid`` and ``self._announced_question_ids``. The real method
    is bound in __init__ so we don't reach into codec_voice at class-definition
    time (which races with pytest's collection order if codec_voice happens to
    be cached from a wrong path)."""
    def __init__(self, cid: str):
        self._cid = cid
        self._announced_question_ids = set()
        # Bind at instance time so we read the CURRENT codec_voice.VoicePipeline.
        self._poll_pending_question_for_voice = (
            codec_voice.VoicePipeline._poll_pending_question_for_voice.__get__(self)
        )


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


def _write_pending(pq_path: Path, records: list[dict]):
    pq_path.parent.mkdir(parents=True, exist_ok=True)
    pq_path.write_text(json.dumps({"pending_questions": records, "schema": 1}))


def _await(coro):
    """Run a coroutine in a fresh event loop (since the resolver is sync but
    the poller is async). Tests aren't full asyncio."""
    import asyncio
    return asyncio.run(coro)


def test_poll_picks_up_matching_correlation_id(temp_askuser_paths):
    """A pending question whose correlation_id matches self._cid → picked."""
    pq_path, _, _ = temp_askuser_paths
    _write_pending(pq_path, [{
        "id": "q_aaa", "status": "pending", "correlation_id": "session-cid",
        "asked_from": "chat", "agent": "Writer", "question": "go?",
        "options": ["yes", "no"],
    }])
    p = _FakeVoicePipeline("session-cid")
    rec = _await(p._poll_pending_question_for_voice())
    assert rec is not None
    assert rec["id"] == "q_aaa"


def test_poll_picks_up_crew_asked_from(temp_askuser_paths):
    """asked_from='crew' → picked even when correlation_id doesn't match
    (background crew ops the user might want to answer out-loud)."""
    pq_path, _, _ = temp_askuser_paths
    _write_pending(pq_path, [{
        "id": "q_bbb", "status": "pending",
        "correlation_id": "completely-different",
        "asked_from": "crew", "agent": "Coder", "question": "stuck?",
    }])
    p = _FakeVoicePipeline("session-cid")
    rec = _await(p._poll_pending_question_for_voice())
    assert rec is not None
    assert rec["id"] == "q_bbb"


def test_poll_skips_chat_asked_from_with_different_cid(temp_askuser_paths):
    """asked_from='chat' AND different cid → NOT picked. (Chat-originated
    questions answer via the PWA, not voice, when the cid is different.)"""
    pq_path, _, _ = temp_askuser_paths
    _write_pending(pq_path, [{
        "id": "q_chat", "status": "pending",
        "correlation_id": "different-cid",
        "asked_from": "chat", "agent": "Bot", "question": "hmm?",
    }])
    p = _FakeVoicePipeline("session-cid")
    rec = _await(p._poll_pending_question_for_voice())
    assert rec is None


def test_poll_skips_already_announced_question(temp_askuser_paths):
    """Once announced this session, the same qid is skipped on the next poll
    (avoids re-announcing the same question forever).

    L2 / SR-62: the poll no longer self-marks announced — the caller marks it
    only AFTER a successful announce (so a failed announce retries). The dedup
    contract is unchanged; here we mark it ourselves to simulate the caller's
    post-announce step, then confirm the next poll skips it."""
    pq_path, _, _ = temp_askuser_paths
    _write_pending(pq_path, [{
        "id": "q_xxx", "status": "pending", "correlation_id": "session-cid",
        "asked_from": "chat",
    }])
    p = _FakeVoicePipeline("session-cid")
    rec1 = _await(p._poll_pending_question_for_voice())
    assert rec1 is not None
    # Poll again BEFORE marking → still returned (the fix: a failed announce
    # must be able to retry).
    assert _await(p._poll_pending_question_for_voice()) is not None
    # Caller marks it announced after speaking it → now skipped.
    p._announced_question_ids.add("q_xxx")
    assert _await(p._poll_pending_question_for_voice()) is None


def test_poll_skips_answered_questions(temp_askuser_paths):
    """status != 'pending' → not picked."""
    pq_path, _, _ = temp_askuser_paths
    _write_pending(pq_path, [{
        "id": "q_done", "status": "answered", "correlation_id": "session-cid",
        "asked_from": "voice",
    }])
    p = _FakeVoicePipeline("session-cid")
    rec = _await(p._poll_pending_question_for_voice())
    assert rec is None


def test_poll_returns_none_when_no_questions(temp_askuser_paths):
    """No pending_questions.json → safe None return."""
    p = _FakeVoicePipeline("session-cid")
    rec = _await(p._poll_pending_question_for_voice())
    assert rec is None


def test_poll_picks_oldest_first(temp_askuser_paths):
    """Multiple matching pendings → returns the FIRST one in file order."""
    pq_path, _, _ = temp_askuser_paths
    _write_pending(pq_path, [
        {"id": "q_old", "status": "pending", "correlation_id": "session-cid",
         "asked_from": "chat"},
        {"id": "q_new", "status": "pending", "correlation_id": "session-cid",
         "asked_from": "chat"},
    ])
    p = _FakeVoicePipeline("session-cid")
    rec = _await(p._poll_pending_question_for_voice())
    assert rec["id"] == "q_old"


# ── Defer-to-PWA path: no active voice session marker ────────────────────────

def test_defer_to_pwa_when_no_voice_session_marker(tmp_path, monkeypatch):
    """When no _VOICE_SESSION_MARKER exists, codec_ask_user.ask emits the
    question and the user answers via PWA only — voice doesn't intercept.
    We don't have voice running here, so this is a structural assertion:
    no marker file → no voice handler runs → answer must come via PWA."""
    marker = tmp_path / "voice_session.json"
    monkeypatch.setattr(codec_voice, "_VOICE_SESSION_MARKER", str(marker))
    assert not marker.exists()
    # The voice resolver is never invoked when there's no live pipeline.
    # This test documents the design assumption: PWA alone is the answer
    # surface when no voice session is active.
