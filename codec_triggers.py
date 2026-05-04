"""CODEC Trigger System (Phase 2 Step 6).

Skills declare an `SKILL_OBSERVATION_TRIGGER` dict alongside their
existing `SKILL_TRIGGERS` list. After every codec_observer poll, this
module evaluates the snapshot against all registered triggers and
optionally dispatches matches through the existing
`codec_dispatch.run_skill` chokepoint (which Step 2's `run_with_hooks`
already wraps).

────────────────────────────────────────────────────────────────────────
Architecture
────────────────────────────────────────────────────────────────────────

  codec_observer.poll()
        │
        ▼
   evaluate(snapshot)
        │
        ├─ for each Trigger discovered from SkillRegistry:
        │     ├─ matches snapshot? → no, continue
        │     ├─ killed via PWA?    → yes, skip silently
        │     ├─ cooldown elapsed?  → no, emit trigger_blocked + skip
        │     │
        │     └─ emit trigger_evaluated, then:
        │           destructive=True  → codec_ask_user.ask(destructive=True)
        │           require_confirmation=True → PWA notification + wait
        │           else → fire silently
        │
        ▼
   _dispatch(trigger, snapshot)
        │
        ▼
   codec_dispatch.run_skill(skill, task=<rendered context>)
        │
        ▼ (existing chokepoint, hooks fire as usual)
   skill returns → emit trigger_fired

────────────────────────────────────────────────────────────────────────
Trust model
────────────────────────────────────────────────────────────────────────

Same as plugins (Phase 1 Step 2): user-curated local Python.
SKILL_OBSERVATION_TRIGGER is data the skill author writes; CODEC just
honors it. No marketplace, no auto-install. Step 6 ships ZERO triggers
— only the plumbing.

Every fire goes through `codec_dispatch.run_skill`, which means:
  - Step 2 plugins observe via post_tool / pre_tool / on_error
  - Step 4 self_improve plugin captures signals
  - Step 5 observer continues to capture state
  - Step 3 step budget applies
  - Step 3 destructive-consent gate applies (when destructive=True)

────────────────────────────────────────────────────────────────────────
Safety
────────────────────────────────────────────────────────────────────────

1. Default cooldown is 600s (10 min) — opinion: skill authors should
   not declare cooldowns under 60s without a strong reason.
2. Default require_confirmation is True — opt-in to silent fires.
3. destructive=True routes through Step 3 §1.7 strict-consent gate
   (literal verb-match; two-strike timeout = ambiguous_consent).
4. Per-trigger kill switch persists at ~/.codec/triggers_killed.json.
5. Global kill switch: TRIGGERS_ENABLED env var (default true).
6. Tests MUST mock codec_dispatch.run_skill — never fire real skills.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from codec_audit import (
    TRIGGER_EVALUATED,
    TRIGGER_FIRED,
    TRIGGER_BLOCKED,
    TRIGGER_MUTED,
    log_event as _log_event,
)

log = logging.getLogger("codec_triggers")

# ── Storage ───────────────────────────────────────────────────────────────────
_KILLED_PATH = Path(os.path.expanduser("~/.codec/triggers_killed.json"))
_KILLED_SCHEMA = 1
_KILLED_LOCK = threading.Lock()

# Mute config — user-facing soft-disable for whole skills (any pattern they
# declare). When the file is absent, defaults apply. The hand-edit + restart
# is the documented path; tests use _refresh_mute_cache() to reload.
_MUTE_CONFIG_PATH = Path(os.path.expanduser("~/.codec/triggers.json"))
_DEFAULT_MUTE_CONFIG: Dict[str, Any] = {
    "muted_skills": ["clipboard_url_fetch"],
    "muted_until": {},
}

# ── Module-level state ────────────────────────────────────────────────────────
# Per-trigger last-fired timestamp (RAM only — process restart resets).
_LAST_FIRED: Dict[str, float] = {}
_LAST_FIRED_LOCK = threading.Lock()

# Cached killed-keys set; reloaded from disk lazily.
_KILLED_CACHE: Optional[set] = None
_KILLED_CACHE_LOCK = threading.Lock()

# Cached mute config; reloaded from disk lazily. Hand-edits to the JSON file
# require either a service restart or a call to _refresh_mute_cache().
_MUTE_CACHE: Optional[dict] = None
_MUTE_CACHE_LOCK = threading.Lock()


# ── Kill switch ───────────────────────────────────────────────────────────────
def _enabled() -> bool:
    """Read TRIGGERS_ENABLED env var (default true). Read each call so
    PM2 restart with a different env value takes effect without code
    change."""
    val = (os.environ.get("TRIGGERS_ENABLED") or "true").strip().lower()
    return val not in ("false", "0", "no", "off")


# ── Validation ────────────────────────────────────────────────────────────────
_VALID_TYPES = frozenset({
    "window_title_match", "clipboard_pattern", "file_change", "time",
    "compound",
})


def _validate_trigger_dict(d: Any) -> Tuple[bool, str]:
    """Returns (ok, reason). Reason is empty string when ok."""
    if not isinstance(d, dict):
        return (False, f"trigger must be dict, got {type(d).__name__}")
    required = {"type", "pattern", "cooldown_seconds",
                "require_confirmation", "destructive"}
    missing = required - set(d.keys())
    if missing:
        return (False, f"missing required keys: {sorted(missing)}")
    if d["type"] not in _VALID_TYPES:
        return (False, f"unknown type {d['type']!r}; expected {sorted(_VALID_TYPES)}")
    if not isinstance(d["cooldown_seconds"], int) or d["cooldown_seconds"] < 0:
        return (False, "cooldown_seconds must be non-negative int")
    if not isinstance(d["require_confirmation"], bool):
        return (False, "require_confirmation must be bool")
    if not isinstance(d["destructive"], bool):
        return (False, "destructive must be bool")
    if d["type"] == "compound":
        pat = d["pattern"]
        if not isinstance(pat, dict) or pat.get("op") not in ("and", "or"):
            return (False, "compound pattern must be {op: 'and'|'or', children: [...]}")
        children = pat.get("children")
        if not isinstance(children, list) or not children:
            return (False, "compound pattern must have non-empty children list")
        for child in children:
            ok, why = _validate_trigger_dict({**child,
                                              **{"cooldown_seconds": 0,
                                                 "require_confirmation": False,
                                                 "destructive": False}})
            if not ok:
                return (False, f"compound child invalid: {why}")
    return (True, "")


# ── Trigger dataclass ─────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Trigger:
    """Validated trigger dict + stable hash key. Constructed from a
    skill module's SKILL_OBSERVATION_TRIGGER constant."""
    skill_name: str
    type: str
    pattern: Any
    cooldown_seconds: int
    require_confirmation: bool
    destructive: bool
    key: str   # "<skill_name>:<sha8(trigger_dict)>"
    raw: dict = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, skill_name: str, d: dict) -> Optional["Trigger"]:
        ok, why = _validate_trigger_dict(d)
        if not ok:
            log.warning("Invalid trigger for skill %s: %s", skill_name, why)
            return None
        # Stable hash over the canonical dict serialization
        canonical = json.dumps(d, sort_keys=True, default=str)
        h = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:8]
        return cls(
            skill_name=skill_name,
            type=d["type"],
            pattern=d["pattern"],
            cooldown_seconds=int(d["cooldown_seconds"]),
            require_confirmation=bool(d["require_confirmation"]),
            destructive=bool(d["destructive"]),
            key=f"{skill_name}:{h}",
            raw=dict(d),
        )

    def short_summary(self) -> str:
        if self.type == "window_title_match":
            return f"window~{str(self.pattern)[:40]}"
        if self.type == "clipboard_pattern":
            return f"clipboard~{str(self.pattern)[:40]}"
        if self.type == "file_change":
            return f"file~{str(self.pattern)[:60]}"
        if self.type == "time":
            return f"time~{self.pattern}"
        if self.type == "compound":
            op = self.pattern.get("op", "?")
            n = len(self.pattern.get("children", []))
            return f"compound({op}, {n} children)"
        return self.type


# ── Discovery ─────────────────────────────────────────────────────────────────
def discover_triggers(registry) -> List[Trigger]:
    """Walk the skill registry, extract validated SKILL_OBSERVATION_TRIGGER
    dicts. Returns the list of Trigger instances. Skills whose trigger
    fails validation are logged and skipped."""
    triggers: List[Trigger] = []
    try:
        names = registry.names()
    except Exception:
        return triggers
    for name in names:
        try:
            trig_dict = registry.get_observation_trigger(name)
        except Exception as e:
            log.debug("get_observation_trigger(%s) failed: %s", name, e)
            continue
        if trig_dict is None:
            continue
        t = Trigger.from_dict(name, trig_dict)
        if t is not None:
            triggers.append(t)
    return triggers


# ── Killed-keys persistence ───────────────────────────────────────────────────
def _load_killed() -> set:
    """Read killed_keys set from disk. Cached after first call; use
    _refresh_killed_cache() to invalidate."""
    global _KILLED_CACHE
    with _KILLED_CACHE_LOCK:
        if _KILLED_CACHE is not None:
            return set(_KILLED_CACHE)
        try:
            with open(_KILLED_PATH) as f:
                data = json.load(f)
            keys = set(data.get("killed_keys", []))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            keys = set()
        _KILLED_CACHE = keys
        return set(keys)


def _refresh_killed_cache() -> None:
    """Invalidate the killed-keys cache. Called after writes."""
    global _KILLED_CACHE
    with _KILLED_CACHE_LOCK:
        _KILLED_CACHE = None


def is_killed(trigger_key: str) -> bool:
    return trigger_key in _load_killed()


def set_killed(trigger_key: str, killed: bool) -> None:
    """Toggle a trigger's killed state. Atomic write via tmp+rename."""
    with _KILLED_LOCK:
        try:
            with open(_KILLED_PATH) as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = {"killed_keys": [], "schema": _KILLED_SCHEMA}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            data = {"killed_keys": [], "schema": _KILLED_SCHEMA}
        keys = set(data.get("killed_keys", []))
        if killed:
            keys.add(trigger_key)
        else:
            keys.discard(trigger_key)
        data["killed_keys"] = sorted(keys)
        data["schema"] = _KILLED_SCHEMA
        _KILLED_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _KILLED_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, _KILLED_PATH)
    _refresh_killed_cache()


# ── Runtime mute config ───────────────────────────────────────────────────────
def _load_mute_config() -> dict:
    """Read mute config from disk, cached. Returns _DEFAULT_MUTE_CONFIG when
    the file is missing or malformed (fail-open: no muting on bad config)."""
    global _MUTE_CACHE
    with _MUTE_CACHE_LOCK:
        if _MUTE_CACHE is not None:
            return dict(_MUTE_CACHE)
        try:
            with open(_MUTE_CONFIG_PATH) as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("triggers.json root must be a JSON object")
            ms = data.get("muted_skills") or []
            mu = data.get("muted_until") or {}
            if not isinstance(ms, list):
                raise ValueError("muted_skills must be a list")
            if not isinstance(mu, dict):
                raise ValueError("muted_until must be a dict")
            cfg = {
                "muted_skills": [str(s) for s in ms if isinstance(s, str)],
                "muted_until": {str(k): str(v) for k, v in mu.items()
                                if isinstance(k, str) and isinstance(v, str)},
            }
        except FileNotFoundError:
            cfg = dict(_DEFAULT_MUTE_CONFIG)
        except (json.JSONDecodeError, OSError, ValueError) as e:
            log.warning("triggers.json unreadable (%s); applying defaults", e)
            cfg = dict(_DEFAULT_MUTE_CONFIG)
        _MUTE_CACHE = cfg
        return dict(cfg)


def _refresh_mute_cache() -> None:
    """Invalidate the mute-config cache. Tests + future setter API call this."""
    global _MUTE_CACHE
    with _MUTE_CACHE_LOCK:
        _MUTE_CACHE = None


def _parse_iso8601(ts: str) -> Optional[datetime]:
    """Best-effort ISO-8601 parser. Accepts trailing 'Z' as UTC."""
    if not isinstance(ts, str) or not ts.strip():
        return None
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _resolve_mute(skill_name: str) -> Tuple[bool, str, Optional[str]]:
    """Internal: returns (muted, source, until_iso). source ∈ {"",
    "muted_skills", "muted_until"}; until_iso is the raw timestamp when
    source is "muted_until", else None.

    A skill is muted when either:
      - its name is in `muted_skills` (permanent until removed), OR
      - `muted_until[skill]` parses to a future-utc datetime.
    """
    cfg = _load_mute_config()
    muted_skills = cfg.get("muted_skills") or []
    if skill_name in muted_skills:
        return (True, "muted_skills", None)
    until_map = cfg.get("muted_until") or {}
    until_raw = until_map.get(skill_name)
    if not until_raw:
        return (False, "", None)
    parsed = _parse_iso8601(until_raw)
    if parsed is None:
        return (False, "", None)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if parsed > datetime.now(timezone.utc):
        return (True, "muted_until", until_raw)
    return (False, "", None)


def _is_muted(skill_name: str) -> bool:
    """Public bool helper per the spec contract — True if `skill_name` is
    currently suppressed by ~/.codec/triggers.json."""
    muted, _, _ = _resolve_mute(skill_name)
    return muted


# ── Cooldown ──────────────────────────────────────────────────────────────────
def cooldown_remaining(trigger_key: str, cooldown_seconds: int) -> float:
    """Returns seconds until trigger can fire again. 0.0 means ready."""
    with _LAST_FIRED_LOCK:
        last = _LAST_FIRED.get(trigger_key, 0)
    elapsed = time.time() - last
    remaining = float(cooldown_seconds) - elapsed
    return max(0.0, remaining)


def mark_fired(trigger_key: str) -> None:
    with _LAST_FIRED_LOCK:
        _LAST_FIRED[trigger_key] = time.time()


# ── Match logic per type ──────────────────────────────────────────────────────
def _match_window_title(pattern: str, snapshot: dict) -> Tuple[bool, str]:
    """Returns (matched, summary). Pattern is regex, matched against
    active_window.title."""
    win = snapshot.get("active_window") or {}
    title = win.get("title", "") or ""
    if not title:
        return (False, "")
    try:
        m = re.search(pattern, title)
    except re.error as e:
        log.warning("Invalid window_title_match regex %r: %s", pattern, e)
        return (False, "")
    if m:
        return (True, f"window:{title[:40]}")
    return (False, "")


def _match_clipboard(pattern: str, snapshot: dict) -> Tuple[bool, str]:
    cb = snapshot.get("clipboard")
    if not cb:
        return (False, "")
    preview = cb.get("preview", "") or ""
    if not preview:
        return (False, "")
    try:
        m = re.search(pattern, preview)
    except re.error as e:
        log.warning("Invalid clipboard_pattern regex %r: %s", pattern, e)
        return (False, "")
    if m:
        kind = cb.get("content_type", "text")
        return (True, f"clipboard:{kind}")
    return (False, "")


def _match_file_change(pattern: str, snapshot: dict) -> Tuple[bool, str]:
    """Pattern is a glob (fnmatch). Match any path in recent_files."""
    expanded = os.path.expanduser(pattern)
    recents = snapshot.get("recent_files") or []
    for rf in recents:
        path = rf.get("path", "")
        if not path:
            continue
        if fnmatch.fnmatch(path, expanded):
            return (True, f"file:{os.path.basename(path)}")
    return (False, "")


def _match_time(pattern: str, snapshot: dict) -> Tuple[bool, str]:
    """Cron-like: 'M H D Mo W'. Each field is `*` (any) or an int.
    Compares to wall-clock at evaluation. ≥1 min granularity per design."""
    parts = pattern.strip().split()
    if len(parts) != 5:
        log.warning("Invalid time pattern %r — expected 'M H D Mo W'", pattern)
        return (False, "")
    now = datetime.now()
    fields = (now.minute, now.hour, now.day, now.month, now.weekday())
    for p, val in zip(parts, fields):
        if p == "*":
            continue
        try:
            if int(p) != int(val):
                return (False, "")
        except ValueError:
            return (False, "")
    return (True, f"time:{pattern}")


def _match_compound(pattern: dict, snapshot: dict) -> Tuple[bool, str]:
    """{op: and|or, children: [{type, pattern}, ...]}."""
    op = pattern.get("op", "and")
    children = pattern.get("children", []) or []
    matches: List[bool] = []
    summaries: List[str] = []
    for child in children:
        c_type = child.get("type")
        c_pattern = child.get("pattern")
        ok, summary = _match_one(c_type, c_pattern, snapshot)
        matches.append(ok)
        if summary:
            summaries.append(summary)
    if op == "or":
        result = any(matches)
    else:
        result = all(matches)
    return (result, " & ".join(summaries) if result else "")


_MATCHERS: Dict[str, Callable[[Any, dict], Tuple[bool, str]]] = {
    "window_title_match": _match_window_title,
    "clipboard_pattern":  _match_clipboard,
    "file_change":        _match_file_change,
    "time":               _match_time,
    "compound":           _match_compound,
}


def _match_one(trigger_type: str, pattern: Any,
               snapshot: dict) -> Tuple[bool, str]:
    matcher = _MATCHERS.get(trigger_type)
    if matcher is None:
        return (False, "")
    try:
        return matcher(pattern, snapshot)
    except Exception as e:
        log.debug("matcher %s failed: %s", trigger_type, e)
        return (False, "")


def matches(trigger: Trigger, snapshot: dict) -> Tuple[bool, str]:
    """Public: does this trigger match this observer snapshot?"""
    return _match_one(trigger.type, trigger.pattern, snapshot)


# ── Audit emit helpers ────────────────────────────────────────────────────────
def _emit_evaluated(trigger: Trigger, match_summary: str,
                    correlation_id: str) -> None:
    try:
        _log_event(
            TRIGGER_EVALUATED, "codec-triggers",
            f"trigger matched: {trigger.skill_name}",
            extra={
                "trigger_key": trigger.key,
                "skill_name": trigger.skill_name,
                "trigger_type": trigger.type,
                "match_summary": match_summary[:200],
            },
            outcome="ok", level="info",
            correlation_id=correlation_id,
        )
    except Exception as e:
        log.debug("trigger_evaluated emit failed: %s", e)


def _emit_fired(trigger: Trigger, dispatch_cid: str,
                correlation_id: str) -> None:
    try:
        _log_event(
            TRIGGER_FIRED, "codec-triggers",
            f"trigger fired: {trigger.skill_name}",
            extra={
                "trigger_key": trigger.key,
                "skill_name": trigger.skill_name,
                "trigger_type": trigger.type,
                "dispatch_correlation_id": dispatch_cid,
            },
            outcome="ok", level="info",
            correlation_id=correlation_id,
        )
    except Exception as e:
        log.debug("trigger_fired emit failed: %s", e)


def _emit_muted(trigger: Trigger, mute_source: str,
                 until_iso: Optional[str], correlation_id: str) -> None:
    extra = {
        "trigger_key": trigger.key,
        "skill_name": trigger.skill_name,
        "trigger_type": trigger.type,
        "mute_source": mute_source,
    }
    if until_iso is not None:
        extra["muted_until"] = until_iso
    try:
        _log_event(
            TRIGGER_MUTED, "codec-triggers",
            f"trigger muted: {trigger.skill_name} ({mute_source})",
            extra=extra,
            outcome="warning", level="warning",
            correlation_id=correlation_id,
        )
    except Exception as e:
        log.debug("trigger_muted emit failed: %s", e)


def _emit_blocked(trigger: Trigger, block_reason: str,
                   correlation_id: str) -> None:
    try:
        _log_event(
            TRIGGER_BLOCKED, "codec-triggers",
            f"trigger blocked: {trigger.skill_name} ({block_reason})",
            extra={
                "trigger_key": trigger.key,
                "skill_name": trigger.skill_name,
                "trigger_type": trigger.type,
                "block_reason": block_reason,
            },
            outcome="warning", level="warning",
            correlation_id=correlation_id,
        )
    except Exception as e:
        log.debug("trigger_blocked emit failed: %s", e)


# ── Dispatch ──────────────────────────────────────────────────────────────────
def _render_task(trigger: Trigger, snapshot: dict, match_summary: str) -> str:
    """Compose the `task` string passed to the skill. Provides minimal
    context so the skill knows WHY it fired."""
    return (
        f"[CODEC trigger fire — {trigger.type}]\n"
        f"Match: {match_summary}\n"
        f"Active window: {(snapshot.get('active_window') or {}).get('title', '(unknown)')}\n"
        f"Trigger key: {trigger.key}"
    )


def _dispatch(trigger: Trigger, snapshot: dict,
              match_summary: str, correlation_id: str) -> bool:
    """Run the skill via codec_dispatch.run_skill. Returns True on dispatch
    (caller decides if that means "fired")."""
    try:
        from codec_dispatch import run_skill, registry
    except Exception as e:
        log.warning("codec_dispatch unavailable: %s", e)
        return False
    try:
        meta = registry.get_meta(trigger.skill_name) or {}
    except Exception:
        meta = {}
    skill = {
        "name": trigger.skill_name,
        "_all_matches": [trigger.skill_name],
        **meta,
    }
    task = _render_task(trigger, snapshot, match_summary)
    dispatch_cid = secrets.token_hex(6)
    try:
        run_skill(skill, task)
        _emit_fired(trigger, dispatch_cid, correlation_id)
        mark_fired(trigger.key)
        return True
    except Exception as e:
        log.warning("trigger dispatch failed for %s: %s", trigger.skill_name, e)
        return False


# ── Confirmation gate ─────────────────────────────────────────────────────────
def _await_confirmation(trigger: Trigger, snapshot: dict,
                         correlation_id: str) -> Tuple[bool, str]:
    """For require_confirmation=True (and destructive=False).
    Posts to PWA via codec_ask_user.ask() with a short option list.
    Returns (approved, block_reason_if_not).

    Note: codec_ask_user.ask is blocking with a deadline. Because the
    observer poll loop calls evaluate() inline, this WILL block the next
    poll. That's the intended behavior — auto-fires shouldn't outpace the
    user's ability to approve. Default deadline is 60s (much shorter than
    the ask_user default 600s) to keep observer cadence responsive."""
    try:
        from codec_ask_user import ask, TIMEOUT_SENTINEL
    except Exception as e:
        log.debug("codec_ask_user unavailable: %s", e)
        return (False, "confirmation_timeout")
    answer = ask(
        question=f"CODEC trigger: run skill `{trigger.skill_name}` "
                 f"({trigger.short_summary()})?",
        options=["Approve", "Skip"],
        timeout=60,
        agent="codec-triggers",
        asked_from="crew",
        tool_name=trigger.skill_name,
    )
    if answer == TIMEOUT_SENTINEL:
        return (False, "confirmation_timeout")
    answer_lc = (answer or "").strip().lower()
    if answer_lc.startswith("approv"):
        return (True, "")
    return (False, "user_skipped")


def _await_destructive_consent(trigger: Trigger, snapshot: dict,
                                correlation_id: str) -> Tuple[bool, str]:
    """destructive=True path: route through Step 3 §1.7 strict-consent."""
    try:
        from codec_ask_user import ask, TIMEOUT_SENTINEL, _is_consenting_answer
    except Exception:
        return (False, "ambiguous_consent")
    answer = ask(
        question=f"CODEC trigger wants to run DESTRUCTIVE skill "
                 f"`{trigger.skill_name}` ({trigger.short_summary()}). "
                 f"Confirm?",
        timeout=60,
        destructive=True,
        agent="codec-triggers",
        asked_from="crew",
        tool_name=trigger.skill_name,
    )
    if answer == TIMEOUT_SENTINEL:
        return (False, "ambiguous_consent")
    return (True, "")


# ── Main entry: evaluate ──────────────────────────────────────────────────────
def evaluate(snapshot: dict, *, registry: Optional[Any] = None,
              fire: bool = True) -> List[dict]:
    """Match snapshot against all registered triggers. Optionally dispatch.

    Args:
        snapshot: a single observer ring-buffer entry (from
                  codec_observer.poll()).
        registry: optional injected SkillRegistry; defaults to the
                  one shared with codec_dispatch.run_skill.
        fire: if True, dispatch matching triggers that pass cooldown +
              consent. If False, only return match list (test mode).

    Returns: list of dicts, one per trigger evaluated, with keys
        {trigger_key, skill_name, status, block_reason?, dispatch_cid?}
        status ∈ {"matched_fired", "matched_pending_confirmation",
                  "blocked_cooldown", "blocked_killed", "blocked_user_skipped",
                  "blocked_confirmation_timeout", "blocked_ambiguous_consent",
                  "no_match"}
    """
    if not _enabled():
        return []
    if registry is None:
        try:
            from codec_dispatch import registry as _dispatch_registry
            registry = _dispatch_registry
        except Exception:
            return []

    cid = secrets.token_hex(6)
    triggers = discover_triggers(registry)
    out: List[dict] = []

    for trig in triggers:
        # Killed check first (cheapest)
        if is_killed(trig.key):
            out.append({"trigger_key": trig.key,
                        "skill_name": trig.skill_name,
                        "status": "blocked_killed"})
            continue

        ok, summary = matches(trig, snapshot)
        if not ok:
            out.append({"trigger_key": trig.key,
                        "skill_name": trig.skill_name,
                        "status": "no_match"})
            continue

        # Match found
        _emit_evaluated(trig, summary, cid)

        # Mute check — soft-disable via ~/.codec/triggers.json. Audited
        # (trigger_muted) so the user sees what they're suppressing.
        muted, mute_source, until_iso = _resolve_mute(trig.skill_name)
        if muted:
            _emit_muted(trig, mute_source, until_iso, cid)
            entry = {"trigger_key": trig.key,
                     "skill_name": trig.skill_name,
                     "status": "blocked_muted",
                     "mute_source": mute_source}
            if until_iso is not None:
                entry["muted_until"] = until_iso
            out.append(entry)
            continue

        # Cooldown check
        remaining = cooldown_remaining(trig.key, trig.cooldown_seconds)
        if remaining > 0:
            _emit_blocked(trig, "cooldown", cid)
            out.append({"trigger_key": trig.key,
                        "skill_name": trig.skill_name,
                        "status": "blocked_cooldown",
                        "block_reason": "cooldown",
                        "cooldown_remaining": remaining})
            continue

        if not fire:
            # Test/inspection mode
            out.append({"trigger_key": trig.key,
                        "skill_name": trig.skill_name,
                        "status": "matched_pending_confirmation",
                        "match_summary": summary})
            continue

        # Consent gate
        if trig.destructive:
            approved, block_reason = _await_destructive_consent(
                trig, snapshot, cid)
        elif trig.require_confirmation:
            approved, block_reason = _await_confirmation(
                trig, snapshot, cid)
        else:
            approved, block_reason = (True, "")

        if not approved:
            _emit_blocked(trig, block_reason, cid)
            out.append({"trigger_key": trig.key,
                        "skill_name": trig.skill_name,
                        "status": f"blocked_{block_reason}",
                        "block_reason": block_reason})
            continue

        # Fire!
        if _dispatch(trig, snapshot, summary, cid):
            out.append({"trigger_key": trig.key,
                        "skill_name": trig.skill_name,
                        "status": "matched_fired",
                        "match_summary": summary})
        else:
            out.append({"trigger_key": trig.key,
                        "skill_name": trig.skill_name,
                        "status": "blocked_dispatch_failed"})

    return out


# ── Test helpers ──────────────────────────────────────────────────────────────
def _reset_state_for_test() -> None:
    """Clear cooldowns + killed cache + mute cache. Used only by tests."""
    with _LAST_FIRED_LOCK:
        _LAST_FIRED.clear()
    _refresh_killed_cache()
    _refresh_mute_cache()


__all__ = [
    "Trigger",
    "discover_triggers",
    "matches",
    "evaluate",
    "is_killed",
    "set_killed",
    "cooldown_remaining",
    "mark_fired",
    "_validate_trigger_dict",
    "_KILLED_PATH",
    "_MUTE_CONFIG_PATH",
    "_is_muted",
    "_load_mute_config",
    "_refresh_mute_cache",
]
