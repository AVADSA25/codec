"""CODEC AskUserQuestion — pause an agent and ask the user.

Per docs/PHASE1-STEP3-DESIGN.md (v2, §9 RESOLVED).

Public API: ``ask(question, options=None, timeout=None, ...) → str``

Storage:    ``~/.codec/pending_questions.json`` (canonical state — atomic write)
Notif:      ``~/.codec/notifications.json``     (display surface — type="question")
Reply:      ``POST /api/agents/answer/{id}``    (PWA + voice — see codec_dashboard.py / codec_voice.py)

Blocking: ``threading.Event`` keyed by ``pending_question_id``. The caller's
worker thread sleeps on ``Event.wait(timeout)`` until either the answer
endpoint fires the event or the deadline elapses. **No async** — matches
the Step 2 sync-only-hooks contract.

Correlation: per Step 1 §1.4, the wrapping operation generates
``correlation_id`` once and threads it via ``contextvars``. ``ask()`` does
NOT re-emit the envelope on resume — the blocked-then-unblocked tool call
is one logical operation in the audit log.

Strict-consent gate (§1.7): irreversible actions opt in via
``destructive=True`` kwarg, caller-supplied ``destructive_verb``, OR
auto-trigger when the calling tool name is in ``codec_config._HTTP_BLOCKED``.
On strict-consent, the answer must contain the destructive verb literally
(case-insensitive) — generic "yes"/"ok"/"sure" rejected with re-prompt.
After two rejections, the question times out as
``ask_user_question_timeout`` with ``extra.reason="ambiguous_consent"``.

Kill switch: ``ASKUSER_ENABLED`` env var (default ``true``). When ``false``,
``ask()`` returns ``"(skill disabled)"`` immediately with no state change.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import codec_jsonstore  # PR-4C (C-4/M-2): cross-process file_lock + eviction
from codec_audit import (
    ASKUSER_EVENT_ANSWER,
    ASKUSER_EVENT_EMIT,
    ASKUSER_EVENT_TIMEOUT,
    _PREVIEW_MAX,
    _truncate,
    log_event as _log_event,
)

log = logging.getLogger("codec_ask_user")

# ── Storage paths ──────────────────────────────────────────────────────────────
_CODEC_DIR = Path(os.path.expanduser("~/.codec"))
_CODEC_DIR.mkdir(parents=True, exist_ok=True)
PENDING_QUESTIONS_PATH = _CODEC_DIR / "pending_questions.json"
NOTIFICATIONS_PATH = _CODEC_DIR / "notifications.json"
CONFIG_PATH = _CODEC_DIR / "config.json"

# ── Schema constants ───────────────────────────────────────────────────────────
DEFAULT_TIMEOUT_SECONDS = 600           # §1.2 Q1 — 10 minutes
DEFAULT_CONSENT_MAX_ATTEMPTS = 2        # §1.7 two-strike rule
PENDING_QUESTIONS_SCHEMA = 1

# Sentinel returned to the agent on timeout (deadline OR ambiguous_consent).
TIMEOUT_SENTINEL = "(no answer — timed out)"
DISABLED_SENTINEL = "(skill disabled)"

# Generic affirmatives that DO NOT count as consent on a strict-consent gate.
# §1.7 PWA acceptance rules: any of these (lower-cased, stripped) → reject.
_GENERIC_YES = frozenset({
    "y", "yes", "yeah", "yep", "yup", "sure", "ok", "okay",
    "fine", "alright", "go", "go ahead", "proceed", "do it",
})

# ── Lock + waiter registry ─────────────────────────────────────────────────────
# Lock guards atomic file writes. Waiters dict maps pending_question_id →
# threading.Event (caller signals via set() to unblock the agent thread).
_FILE_LOCK = threading.Lock()
_WAITERS: Dict[str, threading.Event] = {}
_WAITERS_LOCK = threading.Lock()

# Per-question rejection counter for the strict-consent two-strike gate.
# Cleared on accepted answer or terminal timeout.
_REJECTION_COUNT: Dict[str, int] = {}


# ── Feature flag ───────────────────────────────────────────────────────────────
def _enabled() -> bool:
    """Read ASKUSER_ENABLED env var. Default true. Read each call so tests can
    monkeypatch os.environ without restarting the module."""
    val = (os.environ.get("ASKUSER_ENABLED") or "true").strip().lower()
    return val not in ("false", "0", "no", "off")


# ── Config helpers ─────────────────────────────────────────────────────────────
def _load_config() -> dict:
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _config_timeout_default() -> int:
    cfg = _load_config().get("ask_user", {})
    v = cfg.get("timeout_seconds")
    if isinstance(v, (int, float)) and v > 0:
        return int(v)
    return DEFAULT_TIMEOUT_SECONDS


def _config_max_attempts() -> int:
    cfg = _load_config().get("ask_user", {})
    v = cfg.get("consent_strict_max_attempts")
    if isinstance(v, int) and v > 0:
        return v
    return DEFAULT_CONSENT_MAX_ATTEMPTS


# ── pending_questions.json read/write ─────────────────────────────────────────
def _load_pending_questions() -> dict:
    """Read the canonical state file. Returns the full envelope dict.

    On missing file or parse error, returns a fresh-shaped envelope. Caller
    must hold _FILE_LOCK if mutating.
    """
    try:
        with open(PENDING_QUESTIONS_PATH) as f:
            data = json.load(f)
        if not isinstance(data, dict) or "pending_questions" not in data:
            return {"pending_questions": [], "schema": PENDING_QUESTIONS_SCHEMA}
        return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"pending_questions": [], "schema": PENDING_QUESTIONS_SCHEMA}


_RESOLVED_TTL_HOURS = 24   # M-2: evict answered/timed_out records older than this


def _prune_resolved(data: dict) -> None:
    """M-2: drop answered/timed_out records older than _RESOLVED_TTL_HOURS
    (in-place). Pending records are kept regardless of age; records with an
    unparseable timestamp are kept (never lose data on a bad field)."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=_RESOLVED_TTL_HOURS)
    kept = []
    for rec in data.get("pending_questions", []):
        if rec.get("status") in ("answered", "timed_out"):
            ts = rec.get("answered_at") or rec.get("asked_at") or ""
            try:
                when = datetime.fromisoformat(ts)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                if when < cutoff:
                    continue   # prune this resolved-and-old record
            except (ValueError, TypeError):
                pass           # keep records with unparseable timestamps
        kept.append(rec)
    data["pending_questions"] = kept


def _atomic_write_text(path, text: str) -> None:
    """L-1 (PR-4I): atomic write WITH fsync. `Path.write_text` skips fsync, so a
    hard crash between the write and os.replace can land a replaced-but-stale
    file. Caller passes pre-serialized text so each site keeps its
    `json.dumps(..., default=str)` (codec_jsonstore.atomic_write_json omits
    default=str, which would raise on a stray non-JSON value)."""
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _save_pending_questions(data: dict) -> None:
    """Atomic write via tmp+rename. Caller must hold _FILE_LOCK (+ codec_jsonstore
    .file_lock for cross-process safety). M-2: prunes resolved records >24h first."""
    _prune_resolved(data)
    PENDING_QUESTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(PENDING_QUESTIONS_PATH, json.dumps(data, indent=2, default=str))


def _find_pending_record(qid: str) -> Optional[dict]:
    """Locate a pending-question record by id. Returns the dict ref OR None."""
    data = _load_pending_questions()
    for rec in data.get("pending_questions", []):
        if rec.get("id") == qid:
            return rec
    return None


# ── Notification helper ───────────────────────────────────────────────────────
def _write_question_notification(record: dict) -> None:
    """Append a type="question" entry to notifications.json. Best-effort —
    audit emit is the source of truth, notifications.json is the display
    surface; failures here log + continue.
    """
    try:
        # C5 (Fix #5): hold the cross-process file_lock across the whole
        # read-modify-write so concurrent daemons (dashboard / voice /
        # agent-runner) can't clobber each other's append. _FILE_LOCK is the
        # in-process guard; codec_jsonstore.file_lock is the cross-process one —
        # same pairing the PENDING_QUESTIONS read-modify-write already uses.
        with _FILE_LOCK, codec_jsonstore.file_lock(NOTIFICATIONS_PATH):
            try:
                with open(NOTIFICATIONS_PATH) as f:
                    notifs = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                notifs = []
            entry = {
                "id": f"notif_{secrets.token_hex(5)}",
                "type": "question",
                "title": (
                    f"{record['agent']} is asking a question"
                    if record.get("agent") else "CODEC is asking a question"
                ),
                "body": record["question"],
                "status": "warning",
                "created": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "read": False,
                "schedule_id": None,
                "doc_url": None,
                "pending_question_id": record["id"],
                "options": record.get("options"),
                "agent": record.get("agent"),
                "deadline": record.get("deadline"),
                "consent_strict": bool(record.get("consent_strict")),
            }
            notifs.insert(0, entry)
            _atomic_write_text(NOTIFICATIONS_PATH, json.dumps(notifs, indent=2, default=str))
    except Exception as e:
        log.warning("[ask_user] notification write failed: %s", e)


# ── §1.7 strict-consent gate ───────────────────────────────────────────────────
def _is_destructive_tool(tool_name: Optional[str]) -> bool:
    """Auto-trigger: caller's tool is in codec_config._HTTP_BLOCKED. Read each
    call so config edits take effect on PM2 restart per the don't-touch-zone
    convention."""
    if not tool_name:
        return False
    try:
        from codec_config import _HTTP_BLOCKED
        return tool_name in _HTTP_BLOCKED
    except Exception:
        return False


_VERB_RE = re.compile(r"\b([a-z]{4,})\b", re.IGNORECASE)
_DESTRUCTIVE_VERB_HINTS = (
    "delete", "remove", "destroy", "wipe", "trash", "erase",
    "send", "transfer", "transmit", "deliver",
    "purge", "kill", "shutdown", "drop", "format",
)


def _default_destructive_verb(question: str) -> str:
    """Heuristic: pick the first hint verb that appears in the question. Falls
    back to ``"confirm"`` if none found. Caller can always override via the
    explicit ``destructive_verb`` kwarg."""
    if not question:
        return "confirm"
    low = question.lower()
    for v in _DESTRUCTIVE_VERB_HINTS:
        if re.search(rf"\b{v}\b", low):
            return v
    # Fallback: extract first 4+ letter verb-ish word
    m = _VERB_RE.search(low)
    if m:
        return m.group(1)
    return "confirm"


def _is_consenting_answer(answer: str, *, destructive_verb: str,
                          options: Optional[List[str]]) -> Tuple[bool, str]:
    """§1.7 acceptance rules. Returns (accepted, normalized_answer).

    Strict-consent mode (destructive_verb non-empty):
    - Empty / whitespace → reject
    - Generic affirmative ("yes"/"ok"/"yeah" alone) → reject
    - Answer matches an option label exactly (case-insensitive) → accept
    - Answer text contains destructive_verb (case-insensitive) → accept
    - Anything else (incl. free-text refusals like "no" / "cancel") → reject

    Non-strict mode (destructive_verb empty — general question):
    - Empty / whitespace → reject
    - Anything else → accept the answer as-is
    """
    if not answer:
        return (False, "")
    stripped = answer.strip()
    if not stripped:
        return (False, "")
    low = stripped.lower()
    # Exact match against an option (button click sends label) → accept.
    if options:
        for opt in options:
            if opt.lower() == low:
                return (True, stripped)
    if destructive_verb:
        # Strict-consent gate (LS-1 / SR-1): require literal verb-match.
        # Free-text without verb is treated as a refusal — paired with
        # submit_answer's rejection counter, two refusals → ambiguous_consent
        # timeout → ask() returns TIMEOUT_SENTINEL → caller blocks the action.
        if low in _GENERIC_YES:
            return (False, "")
        if re.search(rf"\b{re.escape(destructive_verb.lower())}\b", low):
            return (True, stripped)
        return (False, "")
    # Non-strict (general question): accept any non-empty answer.
    return (True, stripped)


# ── ID generation ─────────────────────────────────────────────────────────────
def _new_question_id() -> str:
    return "q_" + secrets.token_hex(4)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _deadline_iso(timeout_seconds: int) -> str:
    deadline = datetime.now(timezone.utc).timestamp() + timeout_seconds
    return datetime.fromtimestamp(deadline, timezone.utc).isoformat(timespec="milliseconds")


# ── Correlation_id discovery from contextvars (Step 1 §1.4) ───────────────────
def _current_correlation_id() -> Optional[str]:
    """Read the wrapping operation's correlation_id from whichever module's
    contextvar happens to be set. ``codec_agents._correlation_id_var`` is
    set by Crew.run / Agent.run; ``codec_voice._voice_correlation_id_var``
    is set by VoicePipeline.run. Try both — first non-None wins.
    """
    try:
        from codec_agents import _correlation_id_var as _cv1
        v = _cv1.get()
        if v:
            return v
    except Exception:
        pass
    try:
        from codec_voice import _voice_correlation_id_var as _cv2
        v = _cv2.get()
        if v:
            return v
    except Exception:
        pass
    return None


# ── Public API ────────────────────────────────────────────────────────────────
def ask(
    question: str,
    *,
    options: Optional[List[str]] = None,
    timeout: Optional[int] = None,
    destructive: bool = False,
    destructive_verb: Optional[str] = None,
    agent: Optional[str] = None,
    crew_id: Optional[str] = None,
    asked_from: str = "chat",
    tool_name: Optional[str] = None,
) -> str:
    """Pause and ask the user. Blocks until answer or timeout.

    Args:
        question: The question text shown to the user.
        options: Optional list of structured option labels (PWA renders as
            quick-action buttons; voice ASR fuzzy-matches against them in
            codec_voice).
        timeout: Seconds before the question times out. Defaults to
            ~/.codec/config.json: ask_user.timeout_seconds (default 600).
        destructive: §1.7 — when True, requires literal verb-match for
            acceptance. Generic "yes"/"ok" rejected with re-prompt; two
            rejections → ambiguous_consent timeout.
        destructive_verb: §1.7 — the keyword that must appear. If
            destructive=True and this is None, auto-extracted from the
            question via _default_destructive_verb. Falls back to
            "confirm". Caller-supplied verb wins.
        agent: Display name (e.g. "Writer"). Optional.
        crew_id: Originating crew name. Optional.
        asked_from: "chat" | "voice" | "crew" | "mcp". Default "chat".
        tool_name: Caller's tool name. If in codec_config._HTTP_BLOCKED,
            forces destructive=True even if caller didn't set the flag.

    Returns:
        The user's answer string, OR ``"(no answer — timed out)"`` on
        timeout, OR ``"(skill disabled)"`` if ASKUSER_ENABLED=false.
    """
    if not _enabled():
        return DISABLED_SENTINEL

    if timeout is None or timeout <= 0:
        timeout = _config_timeout_default()

    # §1.7: auto-trigger destructive on _HTTP_BLOCKED tools.
    if not destructive and _is_destructive_tool(tool_name):
        destructive = True
    if destructive and not destructive_verb:
        destructive_verb = _default_destructive_verb(question)
    consent_strict = bool(destructive)
    verb_for_audit = destructive_verb if consent_strict else None

    qid = _new_question_id()
    correlation_id = _current_correlation_id() or secrets.token_hex(6)

    record = {
        "id": qid,
        "operation_id": correlation_id,         # operation == cid for Step 3 callers
        "correlation_id": correlation_id,
        "agent": agent,
        "crew_id": crew_id,
        "question": question,
        "options": list(options) if options else None,
        "asked_at": _now_iso(),
        "deadline": _deadline_iso(timeout),
        "timeout_seconds": timeout,
        "status": "pending",
        "answered_at": None,
        "answered_via": None,
        "answer": None,
        "asked_from": asked_from,
        "consent_strict": consent_strict,
        "destructive_verb": verb_for_audit,
    }

    # Write canonical state. C-4: file_lock serializes the read-modify-write
    # across processes (codec-dashboard + codec-agent-runner) so two near-
    # simultaneous ask()s can't clobber each other and lose a question.
    with _FILE_LOCK, codec_jsonstore.file_lock(PENDING_QUESTIONS_PATH):
        data = _load_pending_questions()
        data.setdefault("pending_questions", []).append(record)
        data["schema"] = PENDING_QUESTIONS_SCHEMA
        _save_pending_questions(data)

    # Register the waiter event BEFORE writing the notification — we don't
    # want the user to click "Answer" and have us miss the set() because
    # we're still installing the waiter.
    waiter = threading.Event()
    with _WAITERS_LOCK:
        _WAITERS[qid] = waiter
        _REJECTION_COUNT[qid] = 0

    # Display surface.
    _write_question_notification(record)

    # Audit emit — ask_user_question_emit.
    try:
        _log_event(
            ASKUSER_EVENT_EMIT,
            "codec-ask-user",
            f"{agent or 'CODEC'} asked: {_truncate(question, 80)}",
            extra={
                "pending_question_id": qid,
                "question_preview": _truncate(question, _PREVIEW_MAX),
                "options": record["options"],
                "timeout_seconds": timeout,
                "agent": agent,
                "crew_id": crew_id,
                "asked_from": asked_from,
                "consent_strict": consent_strict,
                "destructive_verb": verb_for_audit,
            },
            outcome="ok",
            level="info",
            correlation_id=correlation_id,
        )
    except Exception as e:
        log.warning("[ask_user] emit audit failed: %s", e)

    # Block. Polls every 1s for terminal status (waiter.set() OR rejected
    # twice) so the strict-consent two-strike timeout can fire WITHOUT
    # waiting the full deadline.
    deadline_t = time.monotonic() + timeout
    while True:
        remaining = deadline_t - time.monotonic()
        if remaining <= 0:
            return _finalize_timeout(qid, reason="deadline")
        # Wait up to 1 second per loop iteration so we can re-check rejection
        # state (Q5 strict-consent path).
        signalled = waiter.wait(timeout=min(1.0, remaining))
        # Reload the record to see what happened during the wait.
        rec = _find_pending_record(qid)
        if rec is None:
            # File got nuked? Treat as terminal timeout.
            return _finalize_timeout(qid, reason="deadline")
        if rec.get("status") == "answered":
            return _finalize_answered(qid, rec)
        if rec.get("status") == "timed_out":
            # Some other path (admin clear, etc.) flipped it.
            return TIMEOUT_SENTINEL
        # Two-strike consent rejection check: if rejected_count >= max,
        # finalize as ambiguous_consent without waiting for the deadline.
        with _WAITERS_LOCK:
            cnt = _REJECTION_COUNT.get(qid, 0)
        if consent_strict and cnt >= _config_max_attempts():
            return _finalize_timeout(qid, reason="ambiguous_consent",
                                     rejection_count=cnt)
        if signalled:
            # Event was set but record still pending — race; continue loop
            # to re-check.
            waiter.clear()


def _finalize_answered(qid: str, rec: dict) -> str:
    """Emit ask_user_question_answer and clean up. Returns the answer."""
    answer = rec.get("answer") or ""
    correlation_id = rec.get("correlation_id")
    answered_via = rec.get("answered_via", "pwa")
    asked_at = rec.get("asked_at", "")
    answered_at = rec.get("answered_at", "")
    elapsed = _elapsed_seconds(asked_at, answered_at)
    try:
        _log_event(
            ASKUSER_EVENT_ANSWER,
            "codec-ask-user",
            f"User answered q={qid} via {answered_via}",
            extra={
                "pending_question_id": qid,
                "answered_via": answered_via,
                "answer_len": len(answer),
                "elapsed_seconds": elapsed,
            },
            outcome="ok",
            level="info",
            correlation_id=correlation_id,
        )
    except Exception as e:
        log.warning("[ask_user] answer audit failed: %s", e)
    with _WAITERS_LOCK:
        _WAITERS.pop(qid, None)
        _REJECTION_COUNT.pop(qid, None)
    return answer


def _finalize_timeout(qid: str, *, reason: str,
                       rejection_count: int = 0) -> str:
    """Emit ask_user_question_timeout and clean up. Returns sentinel."""
    rec = _find_pending_record(qid) or {}
    correlation_id = rec.get("correlation_id")
    asked_at = rec.get("asked_at", "")
    timeout_seconds = rec.get("timeout_seconds", 0)
    elapsed = _elapsed_seconds(asked_at, _now_iso())
    # Mark record terminal.
    try:
        with _FILE_LOCK, codec_jsonstore.file_lock(PENDING_QUESTIONS_PATH):
            data = _load_pending_questions()
            for r in data.get("pending_questions", []):
                if r.get("id") == qid:
                    r["status"] = "timed_out"
                    r["timeout_reason"] = reason
                    break
            _save_pending_questions(data)
    except Exception as e:
        log.warning("[ask_user] timeout state-write failed: %s", e)
    extra = {
        "pending_question_id": qid,
        "elapsed_seconds": elapsed,
        "timeout_seconds": timeout_seconds,
        "reason": reason,
    }
    if reason == "ambiguous_consent":
        extra["consent_rejection_count"] = rejection_count
    try:
        _log_event(
            ASKUSER_EVENT_TIMEOUT,
            "codec-ask-user",
            f"q={qid} timed out ({reason})",
            extra=extra,
            outcome="warning",
            level="warning",
            correlation_id=correlation_id,
        )
    except Exception as e:
        log.warning("[ask_user] timeout audit failed: %s", e)
    with _WAITERS_LOCK:
        _WAITERS.pop(qid, None)
        _REJECTION_COUNT.pop(qid, None)
    return TIMEOUT_SENTINEL


def _elapsed_seconds(asked_iso: str, answered_iso: str) -> float:
    try:
        a = datetime.fromisoformat(asked_iso.replace("Z", "+00:00"))
        b = datetime.fromisoformat(answered_iso.replace("Z", "+00:00"))
        return round((b - a).total_seconds(), 2)
    except Exception:
        return 0.0


# ── Reply path — called by /api/agents/answer/{id} (codec_dashboard) ─────────
def submit_answer(qid: str, answer: str, *, answered_via: str = "pwa") -> dict:
    """Apply an answer to a pending question. Called by the dashboard
    endpoint AND the voice handler.

    Returns a small dict the caller turns into HTTP JSON:
        { "ok": True,  "agent_unblocked": True }                     — accepted
        { "ok": False, "rejected": True, "reason": "ambiguous_consent",
          "remaining_attempts": <N> }                                 — strict-consent reject
        { "ok": False, "error": "not_found" | "already_answered"
          | "already_timed_out" }                                     — bad state

    Idempotency: a duplicate POST after status=answered returns
    already_answered with no state mutation.
    """
    rec = _find_pending_record(qid)
    if rec is None:
        return {"ok": False, "error": "not_found"}
    status = rec.get("status")
    if status == "answered":
        return {"ok": False, "error": "already_answered",
                "answered_at": rec.get("answered_at")}
    if status == "timed_out":
        return {"ok": False, "error": "already_timed_out"}

    # §1.7 strict-consent acceptance check.
    if rec.get("consent_strict"):
        accepted, normalized = _is_consenting_answer(
            answer,
            destructive_verb=rec.get("destructive_verb") or "confirm",
            options=rec.get("options"),
        )
        if not accepted:
            with _WAITERS_LOCK:
                _REJECTION_COUNT[qid] = _REJECTION_COUNT.get(qid, 0) + 1
                rejections = _REJECTION_COUNT[qid]
            max_attempts = _config_max_attempts()
            remaining = max(0, max_attempts - rejections)
            # Wake the waiter so the ask() loop can check rejection count.
            with _WAITERS_LOCK:
                ev = _WAITERS.get(qid)
            if ev is not None:
                ev.set()
            return {
                "ok": False,
                "rejected": True,
                "reason": "ambiguous_consent",
                "remaining_attempts": remaining,
            }
        answer = normalized

    # Apply the answer atomically (C-4: cross-process file_lock on the RMW).
    with _FILE_LOCK, codec_jsonstore.file_lock(PENDING_QUESTIONS_PATH):
        data = _load_pending_questions()
        for r in data.get("pending_questions", []):
            if r.get("id") == qid:
                r["status"] = "answered"
                r["answer"] = answer
                r["answered_at"] = _now_iso()
                r["answered_via"] = answered_via
                break
        _save_pending_questions(data)

    # Mark the matching notification as read.
    try:
        # C5 (Fix #5): same cross-process file_lock as the question-write path
        # above, so a concurrent _write_question_notification and this
        # mark-read can't clobber each other on notifications.json.
        with _FILE_LOCK, codec_jsonstore.file_lock(NOTIFICATIONS_PATH):
            try:
                with open(NOTIFICATIONS_PATH) as f:
                    notifs = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                notifs = []
            for n in notifs:
                if n.get("pending_question_id") == qid:
                    n["read"] = True
            _atomic_write_text(NOTIFICATIONS_PATH, json.dumps(notifs, indent=2, default=str))
    except Exception as e:
        log.warning("[ask_user] notification-mark-read failed: %s", e)

    # Wake the blocked agent thread.
    with _WAITERS_LOCK:
        ev = _WAITERS.get(qid)
    if ev is not None:
        ev.set()
    return {"ok": True, "agent_unblocked": True}


# ── Skill-shim parser helper ─────────────────────────────────────────────────
def parse_skill_input(task: str) -> Dict[str, Any]:
    """Parse the LLM-emitted ask_user input. Accepts either a JSON object
    ``{"question": "...", "options": [...]}`` or a bare string (treated as
    the question with no options). Used by ``skills/ask_user.py``."""
    if not task:
        return {"question": "", "options": None}
    try:
        parsed = json.loads(task)
        if isinstance(parsed, dict) and "question" in parsed:
            return {
                "question": str(parsed.get("question", "")),
                "options": parsed.get("options"),
                "destructive": bool(parsed.get("destructive", False)),
                "destructive_verb": parsed.get("destructive_verb"),
                "timeout": parsed.get("timeout"),
            }
    except (json.JSONDecodeError, TypeError):
        pass
    return {"question": str(task), "options": None}


__all__ = [
    "ask",
    "submit_answer",
    "parse_skill_input",
    "PENDING_QUESTIONS_PATH",
    "NOTIFICATIONS_PATH",
    "TIMEOUT_SENTINEL",
    "DISABLED_SENTINEL",
]
