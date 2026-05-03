"""CODEC Phase 3.5 — Proactive Intelligence Overlay.

Observer-driven contextual nudges. Reads codec_observer's snapshot, checks
declarative patterns, posts at most one suggestion per pattern per cooldown
window. Strict not-invasive defaults: OFF by default, 1 suggestion / hour
cap, easy dismiss, per-pattern kill switch.

Examples (when fully enabled):
  - "You've been on this Notion doc 30 min — want me to summarize?"
  - "3 tabs open on github.com — consolidate into research notes?"
  - "Heavy editing in <file> — want me to auto-commit checkpoints?"

Architecture:
  codec_observer.run_daemon()
        ↓ each tick
   _eval_triggers(snapshot)         (Phase 2 Step 6)
        ↓ then
   check_for_proactive(snapshot)    (Phase 3.5 — this module)
        ↓ if pattern matches AND cooldown elapsed AND not killed
   post_message(type=proactive_suggestion)
        ↓
   audit emit: proactive_suggestion_emitted

PWA:
  - Settings panel: per-pattern enable/disable
  - Notification UI: [Acknowledge] [Dismiss today] [Disable forever]

Kill switches:
  - PROACTIVE_OVERLAY_ENABLED env var (default "false" — opt-in)
  - Per-pattern in `~/.codec/proactive_state.json:killed_patterns`
  - Per-day dismissal in `~/.codec/proactive_state.json:dismissed_today`

Audit events: proactive_suggestion_emitted/_acknowledged/_dismissed.
PHASE35_PROACTIVE_EVENTS frozenset exposed in codec_audit.

Reuses:
  - codec_observer's snapshot (active_window, recent_files, clipboard, tabs)
  - codec_audit (Step 1 envelope)
  - codec_agent_messaging.post_message (Step 10 — silenced=False unconditionally
    so proactive nudges always go through, but the post_message's per-agent
    silence applies if the user has silenced the special "proactive" agent)

See docs/PHASE3-BLUEPRINT.md §9 (Phase 3.5 deferrals — proactive overlay)
for design rationale.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("codec_proactive")

# ── Storage paths (overridable for tests) ─────────────────────────────────────
_CODEC_DIR = Path(os.path.expanduser("~/.codec"))
_STATE_PATH = _CODEC_DIR / "proactive_state.json"

# ── Constants ─────────────────────────────────────────────────────────────────
DEFAULT_COOLDOWN_S = 3600                  # 1 hour per pattern
GLOBAL_RATE_LIMIT_S = 1800                 # min 30 min between ANY two suggestions
LONG_FORM_DWELL_THRESHOLD_S = 30 * 60      # 30 min on the same window
SCHEMA_VERSION = 1

# ── Audit constants (mirror codec_audit) ──────────────────────────────────────
try:
    from codec_audit import (
        PROACTIVE_SUGGESTION_EMITTED,
        PROACTIVE_SUGGESTION_ACKNOWLEDGED,
        PROACTIVE_SUGGESTION_DISMISSED,
    )
except ImportError:
    PROACTIVE_SUGGESTION_EMITTED = "proactive_suggestion_emitted"
    PROACTIVE_SUGGESTION_ACKNOWLEDGED = "proactive_suggestion_acknowledged"
    PROACTIVE_SUGGESTION_DISMISSED = "proactive_suggestion_dismissed"


# ── Pattern registry ──────────────────────────────────────────────────────────
@dataclass
class Suggestion:
    """One proactive nudge ready to post to the user."""
    pattern_id: str
    title: str
    body: str
    actions: List[Dict[str, Any]]   # [{label, endpoint, body_hint}]


@dataclass
class Pattern:
    """A declarative proactive-suggestion trigger.

    `match`: receives (snapshot_dict, history_list, state_dict) → bool
    `make_suggestion`: receives (snapshot_dict) → Suggestion
    `cooldown_seconds`: minimum gap between fires of THIS pattern
    """
    id: str
    description: str
    match: Callable[[Dict[str, Any], List[Dict[str, Any]], Dict[str, Any]], bool]
    make_suggestion: Callable[[Dict[str, Any]], Suggestion]
    cooldown_seconds: int = DEFAULT_COOLDOWN_S


# ── Long-form dwell pattern (the v1 ship) ─────────────────────────────────────
_LONG_FORM_DOMAINS = (
    "notion.so", "docs.google.com", "substack.com", "medium.com",
    "nytimes.com", "ft.com", "economist.com", "newyorker.com",
)


def _matches_long_form_dwell(snapshot: Dict[str, Any],
                             history: List[Dict[str, Any]],
                             state: Dict[str, Any]) -> bool:
    """Active window has been on a long-form domain for ≥ 30 min."""
    win = snapshot.get("active_window") or {}
    title = (win.get("title", "") or "").lower()
    bundle = (win.get("bundle", "") or "").lower()
    # Check if any long-form domain is in the title/bundle
    if not any(domain in title or domain in bundle for domain in _LONG_FORM_DOMAINS):
        return False
    # Check dwell: scan history for how long this window has been active
    dwell_s = _compute_dwell_seconds(snapshot, history)
    return dwell_s >= LONG_FORM_DWELL_THRESHOLD_S


def _compute_dwell_seconds(snapshot: Dict[str, Any],
                           history: List[Dict[str, Any]]) -> float:
    """How many consecutive seconds has the current active window been active?
    Walks history backwards counting matching window-title entries."""
    current_title = (snapshot.get("active_window") or {}).get("title", "")
    if not current_title or not history:
        return 0.0
    now_ts = float(snapshot.get("ts", time.time()))
    # Walk backwards through history
    earliest_match = now_ts
    for entry in reversed(history):
        e_title = (entry.get("active_window") or {}).get("title", "")
        if e_title == current_title:
            earliest_match = float(entry.get("ts", earliest_match))
        else:
            break  # title changed earlier than this; stop
    return now_ts - earliest_match


def _suggestion_long_form_dwell(snapshot: Dict[str, Any]) -> Suggestion:
    win = snapshot.get("active_window") or {}
    title = win.get("title", "this page")
    return Suggestion(
        pattern_id="long_form_dwell",
        title="Want me to summarize?",
        body=(
            f"You've been on **{title[:80]}** for over 30 minutes. "
            f"I can pull a summary into your notes. Type 'yes summarize' "
            f"or click below."
        ),
        actions=[
            {"label": "Summarize", "endpoint": "/api/proactive/acknowledge",
             "body_hint": {"pattern_id": "long_form_dwell"}},
            {"label": "Dismiss today", "endpoint": "/api/proactive/dismiss",
             "body_hint": {"pattern_id": "long_form_dwell", "scope": "today"}},
            {"label": "Disable forever", "endpoint": "/api/proactive/dismiss",
             "body_hint": {"pattern_id": "long_form_dwell", "scope": "forever"}},
        ],
    )


# ── Default pattern set ───────────────────────────────────────────────────────
PATTERNS: List[Pattern] = [
    Pattern(
        id="long_form_dwell",
        description="Long-form reading dwell ≥ 30 min on Notion/Docs/Substack/Medium/major news",
        match=_matches_long_form_dwell,
        make_suggestion=_suggestion_long_form_dwell,
        cooldown_seconds=DEFAULT_COOLDOWN_S,
    ),
]


# ── State management ──────────────────────────────────────────────────────────
def _empty_state() -> Dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "last_fired_at": {},      # pattern_id → epoch seconds
        "dismissed_today": {},    # pattern_id → YYYY-MM-DD (UTC)
        "killed_patterns": [],    # list of pattern_ids permanently disabled
        "last_global_fire_at": 0,  # any-pattern global rate limit
        "updated_at": "",
    }


def _read_state() -> Dict[str, Any]:
    if not _STATE_PATH.exists():
        return _empty_state()
    try:
        return json.loads(_STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log.warning("proactive state read failed: %s — resetting", e)
        return _empty_state()


def _atomic_write_json(path: Path, data: Any) -> None:
    """Mirror Step 8 + Step 10 atomic-write pattern."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _save_state(state: Dict[str, Any]) -> None:
    state["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _atomic_write_json(_STATE_PATH, state)


def _today_local_date() -> str:
    """YYYY-MM-DD in UTC for dismissed_today comparisons."""
    return datetime.now(timezone.utc).date().isoformat()


# ── Audit emit ────────────────────────────────────────────────────────────────
def _audit(event: str, source: str = "codec-proactive",
           message: str = "", correlation_id: str = "",
           extra: Optional[Dict[str, Any]] = None) -> None:
    try:
        from codec_audit import audit
    except Exception:
        return
    audit(event=event, source=source, message=message,
          correlation_id=correlation_id, level="info",
          extra=dict(extra or {}))


# ── Public API ────────────────────────────────────────────────────────────────
def is_enabled() -> bool:
    """Global kill switch. OFF by default — user opts in via env var or
    config to avoid surprise notifications."""
    return os.environ.get("PROACTIVE_OVERLAY_ENABLED", "false").lower() == "true"


def is_pattern_killed(pattern_id: str, state: Optional[Dict[str, Any]] = None) -> bool:
    if state is None:
        state = _read_state()
    return pattern_id in (state.get("killed_patterns") or [])


def is_pattern_dismissed_today(pattern_id: str,
                                state: Optional[Dict[str, Any]] = None) -> bool:
    if state is None:
        state = _read_state()
    return state.get("dismissed_today", {}).get(pattern_id) == _today_local_date()


def check_for_proactive(snapshot: Dict[str, Any],
                         history: Optional[List[Dict[str, Any]]] = None
                         ) -> Optional[Suggestion]:
    """Main entry point. Called by codec_observer's daemon loop.
    Returns a Suggestion if a pattern matched and is allowed to fire,
    None otherwise. Caller is responsible for posting the suggestion
    via codec_agent_messaging.post_message.

    Order of gates:
      1. Global kill switch (PROACTIVE_OVERLAY_ENABLED)
      2. Global rate limit (no more than one suggestion per
         GLOBAL_RATE_LIMIT_S = 30 min)
      3. Per-pattern kill (forever)
      4. Per-pattern dismissed today
      5. Per-pattern cooldown (1 hour)
      6. Pattern.match() returns True
    """
    if not is_enabled():
        return None
    if history is None:
        history = []

    state = _read_state()
    now_ts = time.time()

    # Global rate limit
    last_global = float(state.get("last_global_fire_at", 0))
    if now_ts - last_global < GLOBAL_RATE_LIMIT_S:
        return None

    for pattern in PATTERNS:
        # Per-pattern kill (forever)
        if is_pattern_killed(pattern.id, state):
            continue
        # Per-pattern dismissed today
        if is_pattern_dismissed_today(pattern.id, state):
            continue
        # Per-pattern cooldown
        last_fired = float(state.get("last_fired_at", {}).get(pattern.id, 0))
        if now_ts - last_fired < pattern.cooldown_seconds:
            continue
        # Match check
        try:
            if not pattern.match(snapshot, history, state):
                continue
        except Exception as e:
            log.warning("pattern %s match failed: %s", pattern.id, e)
            continue

        # Match! Build suggestion + record fire
        try:
            suggestion = pattern.make_suggestion(snapshot)
        except Exception as e:
            log.warning("pattern %s make_suggestion failed: %s", pattern.id, e)
            continue

        state.setdefault("last_fired_at", {})[pattern.id] = now_ts
        state["last_global_fire_at"] = now_ts
        _save_state(state)

        _audit(PROACTIVE_SUGGESTION_EMITTED,
               message=f"proactive: {suggestion.title}",
               extra={"pattern_id": pattern.id,
                      "title_excerpt": suggestion.title[:80]})

        return suggestion

    return None


def acknowledge(pattern_id: str) -> None:
    """User clicked Acknowledge. No state change beyond audit emit
    (last_fired_at already set when emitted)."""
    _audit(PROACTIVE_SUGGESTION_ACKNOWLEDGED,
           message=f"acknowledged: {pattern_id}",
           extra={"pattern_id": pattern_id})


def dismiss(pattern_id: str, scope: str = "today") -> None:
    """User clicked Dismiss. Scope ∈ {'today', 'forever'}."""
    state = _read_state()
    if scope == "forever":
        killed = list(state.get("killed_patterns", []))
        if pattern_id not in killed:
            killed.append(pattern_id)
        state["killed_patterns"] = killed
    elif scope == "today":
        state.setdefault("dismissed_today", {})[pattern_id] = _today_local_date()
    else:
        raise ValueError(f"invalid scope {scope!r}; expected 'today' or 'forever'")
    _save_state(state)
    _audit(PROACTIVE_SUGGESTION_DISMISSED,
           message=f"dismissed: {pattern_id} ({scope})",
           extra={"pattern_id": pattern_id, "scope": scope})


def list_patterns() -> List[Dict[str, Any]]:
    """For PWA settings panel — lists registered patterns + their state."""
    state = _read_state()
    out = []
    for p in PATTERNS:
        out.append({
            "id": p.id,
            "description": p.description,
            "cooldown_seconds": p.cooldown_seconds,
            "killed": is_pattern_killed(p.id, state),
            "dismissed_today": is_pattern_dismissed_today(p.id, state),
            "last_fired_at": state.get("last_fired_at", {}).get(p.id),
        })
    return out
