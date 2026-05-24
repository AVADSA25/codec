"""Phase 1 Step 3 §7 — AskUserQuestion core tests.

Validates docs/PHASE1-STEP3-DESIGN.md §1 + §6:
    - Round-trip emit → notification → answer → resume
    - Deadline timeout
    - ambiguous_consent timeout (two strict-consent rejections)
    - correlation_id preservation across emit + answer
    - ASKUSER_ENABLED kill switch

Each test redirects codec_audit._AUDIT_LOG, codec_ask_user.PENDING_QUESTIONS_PATH,
and codec_ask_user.NOTIFICATIONS_PATH to tmp_path so the real ~/.codec/* state
is never touched. Threading.Event-based blocking is exercised with a small
helper thread that calls submit_answer asynchronously.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

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
    """Redirect pending_questions.json + notifications.json to tmp_path AND
    clear the in-memory waiter / rejection-count registries so each test
    starts from a clean slate. Tests share the same module instance."""
    pq = tmp_path / "pending_questions.json"
    nf = tmp_path / "notifications.json"
    monkeypatch.setattr(codec_ask_user, "PENDING_QUESTIONS_PATH", pq)
    monkeypatch.setattr(codec_ask_user, "NOTIFICATIONS_PATH", nf)
    monkeypatch.setattr(codec_ask_user, "CONFIG_PATH",
                        tmp_path / "config.json")
    # Reset module-level state.
    codec_ask_user._WAITERS.clear()
    codec_ask_user._REJECTION_COUNT.clear()
    return pq, nf


def _records(audit_log: Path) -> list[dict]:
    if not audit_log.exists():
        return []
    return [json.loads(l) for l in audit_log.read_text(encoding="utf-8").splitlines() if l.strip()]


def _events_of(records: list[dict], event_name: str) -> list[dict]:
    return [r for r in records if r.get("event") == event_name]


# ── §1 round-trip: emit → notification → answer → resume ─────────────────────

def test_round_trip_pwa_answer_unblocks_caller(temp_audit_log, temp_askuser_paths):
    """ask() blocks; submit_answer() unblocks; both events emitted."""
    pq_path, notif_path = temp_askuser_paths

    answer_holder = {}

    def caller():
        answer_holder["v"] = codec_ask_user.ask(
            "Should I publish the post?",
            options=["Yes", "No"],
            timeout=10,
            asked_from="chat",
        )

    t = threading.Thread(target=caller, daemon=True)
    t.start()
    # Wait for the question to be persisted (poll up to 2s).
    deadline = time.monotonic() + 2.0
    qid = None
    while time.monotonic() < deadline:
        if pq_path.exists():
            data = json.loads(pq_path.read_text())
            pending = data.get("pending_questions", [])
            if pending and pending[0]["status"] == "pending":
                qid = pending[0]["id"]
                break
        time.sleep(0.05)
    assert qid is not None, "ask() never wrote a pending record"

    # Verify notifications.json has a type="question" entry.
    assert notif_path.exists(), "notification not written"
    notifs = json.loads(notif_path.read_text())
    assert any(n.get("type") == "question" and n.get("pending_question_id") == qid
               for n in notifs), "no type='question' notification for this qid"

    # Submit the answer.
    res = codec_ask_user.submit_answer(qid, "Yes", answered_via="pwa")
    assert res == {"ok": True, "agent_unblocked": True}

    # Caller should unblock within ~2s (the polling loop checks every 1s).
    t.join(timeout=3)
    assert not t.is_alive(), "caller still blocked after submit_answer"
    assert answer_holder["v"] == "Yes"

    # Audit: emit + answer events.
    recs = _records(temp_audit_log)
    emits = _events_of(recs, codec_ask_user.ASKUSER_EVENT_EMIT)
    answers = _events_of(recs, codec_ask_user.ASKUSER_EVENT_ANSWER)
    assert len(emits) == 1
    assert len(answers) == 1
    assert emits[0]["extra"]["pending_question_id"] == qid
    assert answers[0]["extra"]["pending_question_id"] == qid
    assert answers[0]["extra"]["answered_via"] == "pwa"


def test_pending_record_marked_answered_after_submit(
        temp_audit_log, temp_askuser_paths):
    """The on-disk record transitions pending → answered with a timestamp."""
    pq_path, _ = temp_askuser_paths
    barrier = threading.Event()

    def caller():
        codec_ask_user.ask("Pick one", options=["a", "b"], timeout=5)
        barrier.set()

    t = threading.Thread(target=caller, daemon=True)
    t.start()
    # Wait for emit.
    deadline = time.monotonic() + 2.0
    qid = None
    while time.monotonic() < deadline:
        if pq_path.exists():
            data = json.loads(pq_path.read_text())
            if data.get("pending_questions"):
                qid = data["pending_questions"][0]["id"]
                break
        time.sleep(0.05)
    assert qid is not None

    codec_ask_user.submit_answer(qid, "a")
    barrier.wait(timeout=3)

    final = json.loads(pq_path.read_text())
    rec = final["pending_questions"][0]
    assert rec["status"] == "answered"
    assert rec["answer"] == "a"
    assert rec["answered_at"] is not None


# ── §1.2 deadline timeout ─────────────────────────────────────────────────────

def test_deadline_timeout_returns_sentinel(temp_audit_log, temp_askuser_paths):
    """A short deadline expires; ask() returns TIMEOUT_SENTINEL; emit + timeout
    audit lines written; record marked timed_out with reason='deadline'."""
    pq_path, _ = temp_askuser_paths
    t0 = time.monotonic()
    result = codec_ask_user.ask(
        "Will time out", options=["x"], timeout=2,
        asked_from="chat",
    )
    elapsed = time.monotonic() - t0
    assert result == codec_ask_user.TIMEOUT_SENTINEL
    # Should be roughly 2s (polling loop checks every 1s); allow up to 4s.
    assert 1.5 <= elapsed <= 4.5, f"deadline elapsed={elapsed:.2f}s"

    # Audit: emit + timeout.
    recs = _records(temp_audit_log)
    emits = _events_of(recs, codec_ask_user.ASKUSER_EVENT_EMIT)
    timeouts = _events_of(recs, codec_ask_user.ASKUSER_EVENT_TIMEOUT)
    assert len(emits) == 1
    assert len(timeouts) == 1
    assert timeouts[0]["extra"]["reason"] == "deadline"
    assert timeouts[0]["outcome"] == "warning"
    assert timeouts[0]["level"] == "warning"

    # Record state.
    data = json.loads(pq_path.read_text())
    assert data["pending_questions"][0]["status"] == "timed_out"
    assert data["pending_questions"][0]["timeout_reason"] == "deadline"


# ── §1.7 ambiguous_consent timeout (two strict-consent rejections) ───────────

def test_ambiguous_consent_two_strikes_times_out(temp_audit_log,
                                                  temp_askuser_paths):
    """Two generic-yes rejections on a strict-consent question fire the
    ambiguous_consent timeout WITHOUT waiting the full deadline."""
    pq_path, _ = temp_askuser_paths

    holder = {}
    def caller():
        # destructive=True — strict-consent gate engaged. Verb is "delete".
        holder["v"] = codec_ask_user.ask(
            "Delete the production database?",
            options=["delete", "cancel"],
            timeout=300,                 # long deadline — proves we time out early
            destructive=True,
        )

    t = threading.Thread(target=caller, daemon=True)
    t.start()
    # Wait for emit.
    deadline = time.monotonic() + 2.0
    qid = None
    while time.monotonic() < deadline:
        if pq_path.exists():
            data = json.loads(pq_path.read_text())
            if data.get("pending_questions"):
                qid = data["pending_questions"][0]["id"]
                break
        time.sleep(0.05)
    assert qid is not None

    # First rejection — generic "yes".
    r1 = codec_ask_user.submit_answer(qid, "yes", answered_via="pwa")
    assert r1["ok"] is False
    assert r1["rejected"] is True
    assert r1["reason"] == "ambiguous_consent"
    assert r1["remaining_attempts"] == 1

    # Second rejection — generic "ok".
    r2 = codec_ask_user.submit_answer(qid, "ok", answered_via="pwa")
    assert r2["ok"] is False
    assert r2["rejected"] is True
    assert r2["reason"] == "ambiguous_consent"
    assert r2["remaining_attempts"] == 0

    # Within ~2s (the polling loop notices the rejection-count and finalizes).
    t0 = time.monotonic()
    t.join(timeout=4)
    assert not t.is_alive(), "caller still blocked despite two strikes"
    elapsed = time.monotonic() - t0
    assert elapsed < 4, f"two-strike timeout took {elapsed:.2f}s — should be ≤2s"
    assert holder["v"] == codec_ask_user.TIMEOUT_SENTINEL

    # Audit: emit + timeout(reason=ambiguous_consent) — answer NOT emitted
    # because answer was rejected, not accepted.
    recs = _records(temp_audit_log)
    emits = _events_of(recs, codec_ask_user.ASKUSER_EVENT_EMIT)
    timeouts = _events_of(recs, codec_ask_user.ASKUSER_EVENT_TIMEOUT)
    answers = _events_of(recs, codec_ask_user.ASKUSER_EVENT_ANSWER)
    assert len(emits) == 1
    assert len(timeouts) == 1
    assert len(answers) == 0
    extra = timeouts[0]["extra"]
    assert extra["reason"] == "ambiguous_consent"
    assert extra["consent_rejection_count"] == 2

    # Record state.
    data = json.loads(pq_path.read_text())
    assert data["pending_questions"][0]["status"] == "timed_out"
    assert data["pending_questions"][0]["timeout_reason"] == "ambiguous_consent"


# ── §1.4 correlation_id preservation ─────────────────────────────────────────

def test_correlation_id_preserved_across_emit_and_answer(
        temp_audit_log, temp_askuser_paths):
    """When ask() runs inside a wrapping operation that has set
    _correlation_id_var, both emit and answer events carry the same cid."""
    pq_path, _ = temp_askuser_paths

    # Set the codec_agents contextvar from this thread (the worker thread will
    # inherit via copy_context if we do it that way; here we keep it simple
    # and run in the main thread but set the var explicitly).
    from codec_agents import _correlation_id_var
    fake_cid = "abcdef012345"
    token = _correlation_id_var.set(fake_cid)
    try:
        holder = {}
        def caller():
            holder["v"] = codec_ask_user.ask(
                "What now?", options=["a"], timeout=5,
            )
        # Run caller in a thread that inherits this thread's context.
        import contextvars
        ctx = contextvars.copy_context()
        t = threading.Thread(target=ctx.run, args=(caller,), daemon=True)
        t.start()
        # Wait for emit.
        deadline = time.monotonic() + 2.0
        qid = None
        while time.monotonic() < deadline:
            if pq_path.exists():
                data = json.loads(pq_path.read_text())
                if data.get("pending_questions"):
                    qid = data["pending_questions"][0]["id"]
                    break
            time.sleep(0.05)
        assert qid is not None

        codec_ask_user.submit_answer(qid, "a")
        t.join(timeout=3)
    finally:
        _correlation_id_var.reset(token)

    # Verify both audit events carry the same cid.
    recs = _records(temp_audit_log)
    emits = _events_of(recs, codec_ask_user.ASKUSER_EVENT_EMIT)
    answers = _events_of(recs, codec_ask_user.ASKUSER_EVENT_ANSWER)
    assert len(emits) == 1 and len(answers) == 1
    assert emits[0]["extra"]["correlation_id"] == fake_cid
    assert answers[0]["extra"]["correlation_id"] == fake_cid

    # The on-disk record's correlation_id matches.
    rec = json.loads(pq_path.read_text())["pending_questions"][0]
    assert rec["correlation_id"] == fake_cid


# ── ASKUSER_ENABLED kill switch ──────────────────────────────────────────────

def test_kill_switch_returns_disabled_sentinel(monkeypatch, temp_audit_log,
                                                temp_askuser_paths):
    """When ASKUSER_ENABLED=false, ask() returns sentinel immediately with
    NO state writes (no pending_questions, no notifications, no audit emit)."""
    pq_path, notif_path = temp_askuser_paths
    monkeypatch.setenv("ASKUSER_ENABLED", "false")

    t0 = time.monotonic()
    result = codec_ask_user.ask("won't fire", options=["x"], timeout=99)
    elapsed = time.monotonic() - t0

    assert result == codec_ask_user.DISABLED_SENTINEL
    assert elapsed < 0.5, "kill switch should return immediately"

    # No state files written.
    if pq_path.exists():
        data = json.loads(pq_path.read_text())
        assert data.get("pending_questions") in (None, []), (
            "pending_questions should be empty / unwritten")
    assert not notif_path.exists() or notif_path.read_text().strip() in ("", "[]")

    # No audit emits.
    recs = _records(temp_audit_log)
    assert _events_of(recs, codec_ask_user.ASKUSER_EVENT_EMIT) == []


# ── submit_answer error paths ─────────────────────────────────────────────────

def test_submit_answer_unknown_qid_returns_not_found(temp_audit_log,
                                                      temp_askuser_paths):
    res = codec_ask_user.submit_answer("q_deadbeef", "anything")
    assert res == {"ok": False, "error": "not_found"}


def test_submit_answer_idempotent_on_already_answered(
        temp_audit_log, temp_askuser_paths):
    """Posting twice to the same qid: first wins, second returns
    already_answered with no state change."""
    pq_path, _ = temp_askuser_paths
    holder = {}
    def caller():
        holder["v"] = codec_ask_user.ask("q", options=["a"], timeout=5)
    t = threading.Thread(target=caller, daemon=True)
    t.start()
    deadline = time.monotonic() + 2.0
    qid = None
    while time.monotonic() < deadline:
        if pq_path.exists():
            data = json.loads(pq_path.read_text())
            if data.get("pending_questions"):
                qid = data["pending_questions"][0]["id"]
                break
        time.sleep(0.05)
    assert qid is not None

    r1 = codec_ask_user.submit_answer(qid, "a")
    assert r1["ok"] is True
    t.join(timeout=3)
    assert holder["v"] == "a"

    r2 = codec_ask_user.submit_answer(qid, "different")
    assert r2["ok"] is False
    assert r2["error"] == "already_answered"

    # Record's stored answer is still the first one.
    rec = json.loads(pq_path.read_text())["pending_questions"][0]
    assert rec["answer"] == "a"


# ── parse_skill_input helper (skill shim) ────────────────────────────────────

def test_parse_skill_input_json_with_options():
    parsed = codec_ask_user.parse_skill_input(
        '{"question": "Pick", "options": ["a", "b"]}'
    )
    assert parsed["question"] == "Pick"
    assert parsed["options"] == ["a", "b"]


def test_parse_skill_input_bare_string():
    parsed = codec_ask_user.parse_skill_input("Just a question?")
    assert parsed["question"] == "Just a question?"
    assert parsed["options"] is None


def test_parse_skill_input_destructive_passthrough():
    parsed = codec_ask_user.parse_skill_input(
        '{"question": "Delete it?", "destructive": true, "destructive_verb": "delete"}'
    )
    assert parsed["destructive"] is True
    assert parsed["destructive_verb"] == "delete"
