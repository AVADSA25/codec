"""Structured JSON audit log for CODEC — unified envelope (schema:1).

One JSON line per audit entry to ~/.codec/audit.log. Daily rotation, 30-day retention.

Unified envelope (per docs/PHASE1-STEP1-DESIGN.md §1.1):
    Required on every entry: ts, schema, event, source, outcome
    Optional top-level:      tool, duration_ms, task_len, context_len, transport,
                             agent, client_id, level, message, error_type, error
    Extension payload:       extra.{...} — anything not in the above (incl. correlation_id)

Two public emitters:
    audit(...)      — tool/skill/MCP-shaped events. `event=` is REQUIRED (no default per Q4).
    log_event(...)  — lifecycle/event adapter (heartbeat, scheduler, dispatch, etc.)

Both never raise. Both share the same writer + lock + rotation.

Event-type enumeration (Step 1 §1.2 is the canonical table). Phase 1 Step 2
adds three hook-layer events (additive; analyzer tolerates):

    HOOK_EVENT_FIRED   = "hook_fired"
        - emitted by codec_hooks.run_with_hooks per successful hook fire
          (incl. veto — that's a normal outcome, not a failure).
        - extra.plugin_name, extra.hook_name, extra.tool_name (null for
          operation hooks), extra.mutated (bool), extra.vetoed (bool).
        - outcome="ok", level="info". duration_ms = hook wall-clock,
          NOT the wrapping operation.

    HOOK_EVENT_ERROR   = "hook_error"      (Step 2 §11 Q4 tightening)
        - emitted when the plugin's hook function ITSELF raises.
        - top-level error_type + error (truncated to _PREVIEW_MAX),
          extra.plugin_name, extra.hook_name. correlation_id inherits.
        - outcome="error", level="WARNING" — NOT "error". The operation
          still succeeded; only the plugin failed. Keeps audit_report's
          error-rate metric meaningful (a noisy plugin doesn't inflate
          error counts and mask real problems).
        - hook_fired and hook_error are split events; never both for
          the same call.

    HOOK_EVENT_VETOED  = "tool_vetoed"
        - emitted when pre_tool returned HookVeto. Replaces the per-path
          tool_result emit on vetoed calls.
        - extra.veto_reason (≤_PREVIEW_MAX), extra.plugin_name,
          extra.task_preview.
        - outcome="denied", level="warning".

Constants exported below so callers + analyzer can grep for canonical
names without typos.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Storage ────────────────────────────────────────────────────────────────────
_AUDIT_DIR = Path(os.path.expanduser("~/.codec"))
_AUDIT_DIR.mkdir(parents=True, exist_ok=True)
_AUDIT_LOG = _AUDIT_DIR / "audit.log"
_LOCK = threading.Lock()
_RETAIN_DAYS = 30

# ── Schema constants ───────────────────────────────────────────────────────────
_SCHEMA_VERSION = 1
_PREVIEW_MAX = 200      # *_preview field caps (task_preview, cmd_preview, prompt_preview…)
_MESSAGE_MAX = 500      # log_event message cap
_ERROR_MAX = 500        # error string cap

# Transport derived from emitter source — used when caller doesn't override.
_TRANSPORT_BY_SOURCE = {
    "codec-heartbeat": "heartbeat",
    "codec-scheduler": "scheduler",
    "codec-dispatch":  "dispatch",
    "codec-session":   "session",
    "codec-dashboard": "chat",
    "codec-mcp-http":  "http",
    "codec-mcp":       "stdio",
    "codec-voice":     "voice",
}

# Top-level reserved fields that callers may NOT override via extra={...}.
_RESERVED_TOP = ("ts", "schema", "event", "source", "outcome", "tool",
                 "duration_ms", "task_len", "context_len", "transport",
                 "agent", "client_id", "level", "message", "error_type", "error")

# Phase 1 Step 2 hook-layer event names. Exported as module constants so
# codec_hooks.py + tests + audit_report can reference canonical names. The
# strings are the on-wire `event` values written into ~/.codec/audit.log;
# changing them is a schema change (would bump _SCHEMA_VERSION).
HOOK_EVENT_FIRED = "hook_fired"     # successful hook fire (incl. deliberate veto)
HOOK_EVENT_ERROR = "hook_error"     # plugin's hook function raised — Step 2 §7.5
HOOK_EVENT_VETOED = "tool_vetoed"   # pre_tool returned HookVeto — Step 2 §4.3

# Quick lookup for the analyzer / introspection: the set of event names
# emitted by the hook layer (codec-hooks source).
HOOK_LAYER_EVENTS = frozenset({HOOK_EVENT_FIRED, HOOK_EVENT_ERROR, HOOK_EVENT_VETOED})


# ── Phase 1 Step 3 event names (AskUserQuestion + stuck + step budget) ────────
# Per docs/PHASE1-STEP3-DESIGN.md §6. All level="warning" (operationally not
# failures — same Q4 reasoning as Step 2's hook_error). Inherits Step 1 §1.4
# correlation_id from the wrapping operation.
ASKUSER_EVENT_EMIT     = "ask_user_question_emit"      # agent emits a question
ASKUSER_EVENT_ANSWER   = "ask_user_question_answer"    # user replies (PWA or voice)
ASKUSER_EVENT_TIMEOUT  = "ask_user_question_timeout"   # deadline OR ambiguous_consent
STUCK_EVENT_WARNING    = "stuck_warning"               # N=3 repeats detected
STUCK_EVENT_ESCALATED  = "stuck_escalated"             # N+2 repeats → ask_user / abort
STEP_BUDGET_EXHAUSTED  = "step_budget_exhausted"       # per-route cap hit

ASKUSER_EVENTS = frozenset({ASKUSER_EVENT_EMIT, ASKUSER_EVENT_ANSWER, ASKUSER_EVENT_TIMEOUT})
STUCK_EVENTS   = frozenset({STUCK_EVENT_WARNING, STUCK_EVENT_ESCALATED})
STEP3_EVENTS   = ASKUSER_EVENTS | STUCK_EVENTS | frozenset({STEP_BUDGET_EXHAUSTED})

# Step 3 event-specific extra-field reservations. These names are documented
# in §6 and §1.7 of the design doc; reserving them here keeps the analyzer's
# schema understanding (and any future migration script) discoverable.
#
# Use as documentation only — `audit()` and `log_event()` accept arbitrary
# extra={} fields by design (Step 1 §2.3). The `_RESERVED_TOP` tuple above
# stays the boundary for top-level reserved fields; these are extra-namespace
# field names, not top-level, so no `_RESERVED_TOP` change is needed.
ASKUSER_EXTRA_FIELDS = (
    "pending_question_id",     # 12-char hex id, "q_<8hex>"
    "question_preview",        # ≤ _PREVIEW_MAX
    "options",                 # list[str] | None
    "timeout_seconds",         # int
    "agent",                   # str | None (null for solo skill use)
    "crew_id",                 # str | None
    "asked_from",              # "chat" | "voice" | "crew" | "mcp"
    "consent_strict",          # bool — §1.7 strict-consent gate flag
    "destructive_verb",        # str | None — only when consent_strict=True
    "answered_via",            # "pwa" | "voice"
    "answer_len",              # int
    "elapsed_seconds",         # float
    "reason",                  # "deadline" | "ambiguous_consent" — on timeout
    "consent_rejection_count", # int — only when reason="ambiguous_consent"
)

STUCK_EXTRA_FIELDS = (
    "tool",                    # str — the repeating tool name (also top-level on emit)
    "repeat_count",            # int — how many identical calls observed
    "agent",                   # str — which agent
    "action",                  # "ask_user" | "abort" | "warn_only" — on escalated
)

STEP_BUDGET_EXTRA_FIELDS = (
    "budget_type",             # "chat_turn" | "crew_max_steps" | "agent_max_tool_calls"
    "limit",                   # int — the budget value that was hit
    "actual",                  # int — current count when budget hit (== limit)
)


# ── Phase 2 Step 5 event names (Continuous Observation Loop) ──────────────────
# Per docs/PHASE2-STEP5-DESIGN.md §3. `observation_tick` is `level="info"`
# (operational signal, fires once per poll cycle). `observation_summary_injected`
# is `level="info"` and inherits the wrapping chat/voice operation's
# correlation_id (per Step 1 §1.4 — this emit is part of that op, not new).
# `observation_tick_slow` (Q5.5) is `level="warning"` to flag poll-overrun
# without changing behavior. `observer_buffer_inspected` (Q5.6) audits any
# debug-gated read of the live buffer state via the PWA endpoint.
OBSERVATION_TICK              = "observation_tick"
OBSERVATION_TICK_SLOW         = "observation_tick_slow"        # Q5.5
OBSERVATION_SUMMARY_INJECTED  = "observation_summary_injected"
OBSERVER_BUFFER_INSPECTED     = "observer_buffer_inspected"    # Q5.6

PHASE2_STEP5_EVENTS = frozenset({
    OBSERVATION_TICK, OBSERVATION_TICK_SLOW,
    OBSERVATION_SUMMARY_INJECTED, OBSERVER_BUFFER_INSPECTED,
})

# Step 5 event-specific extra-field reservations.
# observation_tick / observation_tick_slow are METADATA-ONLY by design —
# no titles, no OCR text, no clipboard content, no file paths.
# (See design §3 "What we deliberately do NOT emit".)
OBSERVATION_TICK_EXTRA_FIELDS = (
    "active_app",              # str — e.g. "Google Chrome"
    "active_title_len",        # int — length only
    "ocr_chars",               # int — length of OCR result
    "ocr_skipped",             # bool — true if OCR timed out
    "clipboard_changed",       # bool
    "clipboard_kind",          # "url" | "text" | "code" | "json" | "image_blob_redacted"
    "recent_files_count",      # int
    "idle_seconds",            # float — at time of poll
    "cadence_used_s",          # int — 60 or 300, selected per Q1
    "buffer_depth",            # int — current ring buffer length
    "poll_duration_ms",        # float — for OBSERVATION_TICK_SLOW threshold
)

OBSERVATION_INJECTION_EXTRA_FIELDS = (
    "tokens_used",             # int
    "injection_reason",        # "always_local" | "possessive_match" |
                               # "continuation_match" | "skill_flag"
    "buffer_entries_summarized",  # int
)

OBSERVER_BUFFER_INSPECT_EXTRA_FIELDS = (
    "client_ip",               # str — who hit the debug endpoint
    "buffer_entries_returned", # int
)


# ── Phase 2 Step 6 event names (Trigger System) ───────────────────────────────
# Per docs/PHASE2-STEP6-DESIGN.md §3. trigger_evaluated and trigger_fired are
# `level="info"` (operational); trigger_blocked is `level="warning"` because
# block_reason values flag user-action-required or consent-failure states.
# All inherit `correlation_id` from the wrapping observer poll's cid.
TRIGGER_EVALUATED = "trigger_evaluated"
TRIGGER_FIRED     = "trigger_fired"
TRIGGER_BLOCKED   = "trigger_blocked"

PHASE2_STEP6_EVENTS = frozenset({
    TRIGGER_EVALUATED, TRIGGER_FIRED, TRIGGER_BLOCKED,
})

# Step 6 event-specific extra-field reservations.
TRIGGER_EXTRA_FIELDS = (
    "trigger_key",                  # "<skill_name>:<sha8(trigger_dict)>"
    "skill_name",                   # str
    "trigger_type",                 # window_title_match | clipboard_pattern |
                                    # file_change | time | compound
    "match_summary",                # short, on trigger_evaluated
    "dispatch_correlation_id",      # on trigger_fired only
    "block_reason",                 # on trigger_blocked only:
                                    # cooldown | user_skipped |
                                    # confirmation_timeout |
                                    # ambiguous_consent | killed
)


# ── Phase 2 Step 7 event names (Shift Report) ─────────────────────────────────
# Per docs/PHASE2-BLUEPRINT.md §"Step 7". The `shift_report_started` event
# opens the assembly operation and `shift_report_completed` closes it with
# summary stats. Both share a single correlation_id per Step 1 §1.4
# (multi-emit operation envelope).
SHIFT_REPORT_STARTED   = "shift_report_started"
SHIFT_REPORT_COMPLETED = "shift_report_completed"

PHASE2_STEP7_EVENTS = frozenset({SHIFT_REPORT_STARTED, SHIFT_REPORT_COMPLETED})

# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 Step 8 — Plan + Permission Contract
# ─────────────────────────────────────────────────────────────────────────────
AGENT_PLAN_DRAFTED         = "agent_plan_drafted"
AGENT_PLAN_APPROVED        = "agent_plan_approved"
AGENT_PLAN_REJECTED        = "agent_plan_rejected"
AGENT_PLAN_REVISED         = "agent_plan_revised"
AGENT_GLOBAL_GRANT_ADDED   = "agent_global_grant_added"
AGENT_GLOBAL_GRANT_REMOVED = "agent_global_grant_removed"

PHASE3_STEP8_EVENTS = frozenset({
    AGENT_PLAN_DRAFTED, AGENT_PLAN_APPROVED, AGENT_PLAN_REJECTED,
    AGENT_PLAN_REVISED, AGENT_GLOBAL_GRANT_ADDED, AGENT_GLOBAL_GRANT_REMOVED,
})

SHIFT_REPORT_EXTRA_FIELDS = (
    "trigger_kind",            # "time" | "idle" | "manual"
    "sections_included",       # int — how many of the 5 sections rendered
    "word_count",              # int — final markdown word count
    "audit_records_scanned",   # int
    "notifications_scanned",   # int
    "observer_summaries_used", # int
    "duration_ms",             # top-level reserved field — also on completed
)


# ── Helpers ────────────────────────────────────────────────────────────────────
def _truncate(s, max_len: int = _PREVIEW_MAX) -> str:
    """Truncate a string to `max_len` chars. None/non-str → ''. Never raises."""
    if not s:
        return ""
    s = s if isinstance(s, str) else str(s)
    return s if len(s) <= max_len else s[:max_len]


def _cmd_hash(code) -> str:
    """8-char sha1 fingerprint of a command. Used to pair flagged/approved/denied
    triplets without storing the raw command twice. Pairing key, not security."""
    if code is None:
        code = ""
    if not isinstance(code, str):
        code = str(code)
    return hashlib.sha1(code.encode("utf-8", errors="replace")).hexdigest()[:8]


def _transport_for(source: str | None) -> str:
    """Map an emitter source name to its canonical transport. Default 'local'."""
    return _TRANSPORT_BY_SOURCE.get(source or "", "local")


def _rotate_if_needed():
    """Rotate audit.log daily. Keep .log.YYYY-MM-DD files, prune >30d."""
    if not _AUDIT_LOG.exists():
        return
    mtime_day = datetime.fromtimestamp(_AUDIT_LOG.stat().st_mtime, timezone.utc).date()
    today = datetime.now(timezone.utc).date()
    if mtime_day >= today:
        return
    rotated = _AUDIT_DIR / f"audit.log.{mtime_day.isoformat()}"
    try:
        _AUDIT_LOG.rename(rotated)
    except OSError:
        return
    cutoff = time.time() - _RETAIN_DAYS * 86400
    for p in _AUDIT_DIR.glob("audit.log.*"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass


def _write(record: dict) -> None:
    """Serialize one record to the audit log. Never raises."""
    try:
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
    except Exception:
        return
    try:
        with _LOCK:
            _rotate_if_needed()
            with open(_AUDIT_LOG, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass


# ── Public emitters ────────────────────────────────────────────────────────────
def audit(
    tool: str = "",
    *,
    event: str,
    source: str | None = None,
    task_len: int = 0,
    context_len: int = 0,
    duration_ms: float | None = None,
    outcome: str = "ok",
    error_type: str | None = None,
    error: str | None = None,
    client_id: str | None = None,
    transport: str | None = None,
    agent: str | None = None,
    level: str | None = None,
    message: str | None = None,
    correlation_id: str | None = None,
    extra: dict | None = None,
) -> None:
    """Write one structured audit line in the unified envelope (schema:1).

    `event` is REQUIRED (per design doc §7-Q4 — no default). Calling without
    `event=` raises TypeError so schema regressions can't be silent.

    `correlation_id`, when non-None, is written under `extra.correlation_id`
    per §1.4. Callers obtain it via `secrets.token_hex(6)` at the entry point
    of any operation that emits ≥2 audit lines.

    Never raises (apart from the explicit TypeError on missing `event=`).
    """
    src = source or os.environ.get("CODEC_PROCESS", "codec")
    record: dict = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "schema": _SCHEMA_VERSION,
        "event": event,
        "source": src,
        "tool": tool or "",
        "task_len": task_len,
        "context_len": context_len,
        "duration_ms": round(duration_ms, 2) if duration_ms is not None else None,
        "outcome": outcome,
        "error_type": error_type,
        "client_id": client_id,
        "transport": transport or os.environ.get("CODEC_MCP_TRANSPORT") or _transport_for(src),
    }
    if agent is not None:
        record["agent"] = agent
    if level is not None:
        record["level"] = level
    if message:
        record["message"] = _truncate(message, _MESSAGE_MAX)
    if error:
        record["error"] = _truncate(error, _ERROR_MAX)

    # Build extra namespace. Strip any reserved-field keys that callers may
    # have stashed in extra so they can't masquerade as top-level.
    ex: dict = {}
    if extra:
        for k, v in extra.items():
            if k in _RESERVED_TOP:
                continue
            ex[k] = v
    if correlation_id is not None:
        ex["correlation_id"] = correlation_id
    if ex:
        record["extra"] = ex

    _write(record)


def log_event(
    event_type: str,
    source: str,
    message: str = "",
    extra: dict | None = None,
    *,
    level: str = "info",
    outcome: str | None = None,
    tool: str | None = None,
    transport: str | None = None,
    duration_ms: float | None = None,
    error_type: str | None = None,
    error: str | None = None,
    client_id: str | None = None,
    correlation_id: str | None = None,
) -> None:
    """Lifecycle / event audit emitter — adapter over audit(). Never raises.

    Positional-friendly signature so the existing 5+ call sites work:
        log_event("error", "codec-heartbeat", "Service down: ...", level="error")
    """
    if outcome is None:
        outcome = "error" if level == "error" else "ok"
    if transport is None:
        transport = _transport_for(source)

    audit(
        tool=tool or "",
        event=event_type,
        source=source,
        outcome=outcome,
        duration_ms=duration_ms,
        error_type=error_type,
        error=error,
        client_id=client_id,
        transport=transport,
        level=level,
        message=message,
        correlation_id=correlation_id,
        extra=extra,
    )
