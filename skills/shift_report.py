"""CODEC Skill: Shift Report — Phase 2 Step 7.

End-of-day summary of everything CODEC observed and accomplished.
Single notification with type="shift_report" for the PWA to render
inline.

Trigger paths (from codec_observer):
  1. Scheduled fire at config.shift_report.daily_at_hour (default 18 local)
  2. Idle fire when CGEventSourceSecondsSinceLastEventType >
     config.shift_report.idle_minutes (default 30 min)
  3. Manual fire via skill name ("shift report" / "what did i do today" /
     "summarize my day") through chat / voice / MCP.

Per-day deduplication:
  ~/.codec/shift_report_state.json tracks last fired date so that on a
  long idle the report fires once per day (not once every poll cycle past
  the idle threshold).

Inputs assembled (from blueprint §"Step 7"):
  1. Last 24h of audit.log filtered to: tool_result(ok), crew_complete,
     schedule_done, hook_fired, ask_user_question_answer, stuck_warning,
     step_budget_exhausted, trigger_fired
  2. ~/.codec/notifications.json — entries created in last 24h
  3. ~/.codec/observation_summaries/ — observer summaries persisted by
     codec_observer.persist_for_shift_report()
  4. ~/.codec/skill_proposals/ — pending unreviewed proposals

Output: 5-section markdown body, ~500-1500 words.
  Section 1: Completed tasks (crew runs, voice sessions, multi-step chats)
  Section 2: Blocked / stuck moments (warnings, timeouts, budget exhaustions)
  Section 3: Observed work patterns (apps, files, time spent)
  Section 4: Pending decisions (open AskUser, queued proposals)
  Section 5: Tomorrow's open threads (incomplete crews, scheduled work)

Optional auto-save to ~/Documents/CODEC Shift Reports/YYYY-MM-DD.md if
config.shift_report.auto_save_path is set.

Kill switch: SHIFT_REPORT_ENABLED env var (default true).
"""
from __future__ import annotations

SKILL_NAME = "shift_report"
SKILL_DESCRIPTION = (
    "Generate end-of-day shift report from CODEC's observations and "
    "post a single notification."
)
SKILL_TRIGGERS = [
    "shift report", "shift-report", "daily shift report",
    "what did i do today", "summarize my day", "today's summary",
    "end of day report", "eod report",
]
SKILL_MCP_EXPOSE = True

import json
import logging
import os
import secrets
import time
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# Lazy imports for codec modules to avoid circular-import on plugin load.
_log = logging.getLogger("skills.shift_report")

# ── Storage paths ─────────────────────────────────────────────────────────────
_CODEC_DIR = Path(os.path.expanduser("~/.codec"))
_AUDIT_LOG = _CODEC_DIR / "audit.log"
_NOTIFS_PATH = _CODEC_DIR / "notifications.json"
_OBS_SUMMARIES_DIR = _CODEC_DIR / "observation_summaries"
_PROPOSALS_DIR = _CODEC_DIR / "skill_proposals"
_STATE_PATH = _CODEC_DIR / "shift_report_state.json"
_CONFIG_PATH = _CODEC_DIR / "config.json"

# Audit events that count as "real work" for section 1.
_COMPLETED_TASK_EVENTS = frozenset({
    "tool_result", "crew_complete", "schedule_done",
    "hook_fired", "ask_user_question_answer", "trigger_fired",
})

# Audit events that count as "blocked / stuck" for section 2.
_BLOCKED_EVENTS = frozenset({
    "stuck_warning", "stuck_escalated", "step_budget_exhausted",
    "ask_user_question_timeout", "trigger_blocked",
})


# ── Feature flag ──────────────────────────────────────────────────────────────
def _enabled() -> bool:
    """Read SHIFT_REPORT_ENABLED env var. Default true. Read each call so
    tests can monkeypatch and PM2 env override takes effect on restart."""
    val = (os.environ.get("SHIFT_REPORT_ENABLED") or "true").strip().lower()
    return val not in ("false", "0", "no", "off")


# ── Config ────────────────────────────────────────────────────────────────────
_DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "daily_at_hour": 18,        # 24-hr clock, local time
    "daily_at_minute": 0,
    "idle_minutes": 30,         # min idle before idle-fire
    "auto_save_path": None,     # e.g. "~/Documents/CODEC Shift Reports"
    "lookback_hours": 24,
}


def _load_config() -> Dict[str, Any]:
    cfg = dict(_DEFAULT_CONFIG)
    try:
        with open(_CONFIG_PATH) as f:
            user = json.load(f).get("shift_report", {})
        if isinstance(user, dict):
            cfg.update(user)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return cfg


# ── Per-day dedup state ───────────────────────────────────────────────────────
def _load_state() -> Dict[str, Any]:
    try:
        with open(_STATE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        os.replace(tmp, _STATE_PATH)
    except Exception as e:
        _log.debug("shift_report state save failed: %s", e)


def _today_local_date() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def already_fired_today() -> bool:
    """Returns True if a shift report has already been written today
    (per-day dedup; idle fires don't repeat)."""
    return _load_state().get("last_fired_date") == _today_local_date()


def mark_fired_today(trigger_kind: str) -> None:
    state = _load_state()
    state["last_fired_date"] = _today_local_date()
    state["last_fired_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state["last_trigger_kind"] = trigger_kind
    _save_state(state)


# ── Input assembly ────────────────────────────────────────────────────────────
def _load_audit_records(lookback_hours: int) -> List[dict]:
    """Read audit.log + recent rotated logs; filter to last N hours."""
    records: List[dict] = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    cutoff_str = cutoff.isoformat(timespec="milliseconds")
    paths = [_AUDIT_LOG]
    # Include up to 2 rotated days back (covers the boundary case)
    today = datetime.now(timezone.utc).date()
    for delta in (1, 2):
        day = today - timedelta(days=delta)
        rotated = _CODEC_DIR / f"audit.log.{day.isoformat()}"
        if rotated.exists():
            paths.append(rotated)
    for p in paths:
        if not p.exists():
            continue
        try:
            for line in p.read_text(errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("ts", "") >= cutoff_str:
                    records.append(r)
        except Exception:
            continue
    records.sort(key=lambda r: r.get("ts", ""))
    return records


def _load_notifications(lookback_hours: int) -> List[dict]:
    try:
        with open(_NOTIFS_PATH) as f:
            notifs = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    if not isinstance(notifs, list):
        return []
    cutoff = datetime.now() - timedelta(hours=lookback_hours)
    out = []
    for n in notifs:
        try:
            ts = datetime.fromisoformat(n.get("created", "").replace("Z", ""))
            if ts >= cutoff:
                out.append(n)
        except (ValueError, TypeError):
            continue
    return out


def _load_observer_summaries() -> List[Path]:
    if not _OBS_SUMMARIES_DIR.is_dir():
        return []
    today_local = _today_local_date()
    out = []
    for f in sorted(_OBS_SUMMARIES_DIR.glob("*.md")):
        # Filename pattern: YYYY-MM-DDTHH-MM-SS.md
        if f.name.startswith(today_local):
            out.append(f)
    return out


def _load_pending_proposals() -> List[Path]:
    if not _PROPOSALS_DIR.is_dir():
        return []
    today_local = _today_local_date()
    today_dir = _PROPOSALS_DIR / today_local
    if not today_dir.is_dir():
        return []
    return sorted(today_dir.glob("*.md"))


# ── Section renderers ─────────────────────────────────────────────────────────
def _render_completed_tasks(records: List[dict]) -> str:
    """Section 1: completed tasks."""
    completed = [r for r in records
                 if r.get("event") in _COMPLETED_TASK_EVENTS
                 and r.get("outcome") == "ok"]
    if not completed:
        return "_(No completed tasks recorded in this window.)_"
    by_kind = Counter(r.get("event", "?") for r in completed)
    by_tool = Counter(r.get("tool", "") for r in completed
                      if r.get("tool"))
    lines = [f"**{len(completed)} successful operation(s):**", ""]
    for event, count in by_kind.most_common():
        lines.append(f"- `{event}`: {count}")
    if by_tool:
        lines.append("")
        lines.append("**Most-fired tools:**")
        for tool, count in by_tool.most_common(5):
            lines.append(f"- `{tool}`: {count}")
    return "\n".join(lines)


def _render_blocked(records: List[dict]) -> str:
    """Section 2: blocked / stuck moments."""
    blocked = [r for r in records if r.get("event") in _BLOCKED_EVENTS]
    if not blocked:
        return "_(No agent blockers recorded in this window — clean run.)_"
    by_kind = Counter(r.get("event", "?") for r in blocked)
    lines = [f"**{len(blocked)} blocker event(s):**", ""]
    for event, count in by_kind.most_common():
        lines.append(f"- `{event}`: {count}")
    # Surface the FIRST 3 distinct blocked tools / agents for diagnostic
    seen_tools = set()
    examples = []
    for r in blocked:
        tool = r.get("tool", "") or r.get("extra", {}).get("tool", "")
        if tool and tool not in seen_tools:
            seen_tools.add(tool)
            examples.append((r.get("ts", "")[:16].replace("T", " "),
                              r.get("event", ""), tool))
            if len(examples) >= 3:
                break
    if examples:
        lines.append("")
        lines.append("**First 3 blockers:**")
        for ts, event, tool in examples:
            lines.append(f"- `{ts}` `{event}` on `{tool}`")
    return "\n".join(lines)


def _render_observed_patterns(records: List[dict],
                               summaries: List[Path]) -> str:
    """Section 3: observed work patterns. Pulls from observation_tick
    metadata (METADATA-only — no titles in audit log). Uses persisted
    observer_summaries if available for richer context."""
    ticks = [r for r in records if r.get("event") == "observation_tick"]
    if not ticks:
        return "_(Observer didn't capture activity in this window.)_"
    apps = Counter()
    for r in ticks:
        app = r.get("extra", {}).get("active_app", "")
        if app:
            apps[app] += 1
    lines = [f"**Observed {len(ticks)} polls** "
             f"({ticks[0].get('ts','')[:16].replace('T', ' ')} → "
             f"{ticks[-1].get('ts','')[:16].replace('T', ' ')}).", ""]
    if apps:
        lines.append("**Time-share by app (poll count):**")
        for app, count in apps.most_common(8):
            pct = (count / len(ticks)) * 100.0
            lines.append(f"- `{app}`: {count} polls ({pct:.0f}%)")
    if summaries:
        lines.append("")
        lines.append(f"**{len(summaries)} observer summary file(s) "
                     f"persisted today** "
                     f"(`~/.codec/observation_summaries/`):")
        for s in summaries[-3:]:
            lines.append(f"- `{s.name}`")
    return "\n".join(lines)


def _render_pending_decisions(notifs: List[dict],
                                proposals: List[Path]) -> str:
    """Section 4: pending decisions."""
    open_questions = [n for n in notifs
                      if n.get("type") == "question"
                      and not n.get("read")]
    parts = []
    if open_questions:
        parts.append(f"**{len(open_questions)} open question(s) "
                     f"awaiting your answer:**")
        for q in open_questions[:5]:
            title = (q.get("title") or "")[:80]
            parts.append(f"- {title}")
    if proposals:
        parts.append("")
        parts.append(f"**{len(proposals)} unreviewed skill proposal(s) "
                     f"today:**")
        for p in proposals[:5]:
            parts.append(f"- `{p.name}`")
        if len(proposals) > 5:
            parts.append(f"- _and {len(proposals) - 5} more in "
                          f"`~/.codec/skill_proposals/{_today_local_date()}/`_")
    if not parts:
        return "_(Nothing waiting on your decision right now.)_"
    return "\n".join(parts)


def _render_tomorrow(records: List[dict]) -> str:
    """Section 5: tomorrow's open threads. Looks for crew_start without a
    matching crew_complete (incomplete operation), plus any scheduled
    fires due."""
    crew_starts = {r.get("extra", {}).get("correlation_id"): r
                   for r in records if r.get("event") == "crew_start"}
    crew_completes = {r.get("extra", {}).get("correlation_id"): r
                      for r in records if r.get("event") == "crew_complete"}
    incomplete = [cid for cid in crew_starts if cid not in crew_completes]
    parts = []
    if incomplete:
        parts.append(f"**{len(incomplete)} crew run(s) started but not "
                     f"completed today:**")
        for cid in incomplete[:5]:
            r = crew_starts[cid]
            agents = r.get("extra", {}).get("agents", [])
            parts.append(f"- cid `{cid[:8]}…` "
                          f"({len(agents)} agent(s))")
    if not parts:
        return "_(No incomplete operations carrying over.)_"
    return "\n".join(parts)


# ── Main assembly ─────────────────────────────────────────────────────────────
def _assemble_shift_report(trigger_kind: str = "manual") -> Dict[str, Any]:
    """Build the report. Returns dict with markdown body + counters.
    Caller posts the notification."""
    cfg = _load_config()
    lookback_hours = int(cfg.get("lookback_hours", 24))

    audit_records = _load_audit_records(lookback_hours)
    notifs = _load_notifications(lookback_hours)
    summaries = _load_observer_summaries()
    proposals = _load_pending_proposals()

    sections = [
        ("Completed tasks", _render_completed_tasks(audit_records)),
        ("Blocked / stuck moments", _render_blocked(audit_records)),
        ("Observed work patterns",
         _render_observed_patterns(audit_records, summaries)),
        ("Pending decisions",
         _render_pending_decisions(notifs, proposals)),
        ("Tomorrow's open threads",
         _render_tomorrow(audit_records)),
    ]

    sections_included = sum(1 for _, body in sections
                            if not body.startswith("_("))

    today = _today_local_date()
    body_parts = [
        f"# CODEC Shift Report — {today}",
        "",
        f"_Generated {datetime.now().strftime('%H:%M %Z')} via "
        f"`{trigger_kind}` trigger. Window: last {lookback_hours}h._",
        "",
    ]
    for title, content in sections:
        body_parts.append(f"## {title}")
        body_parts.append("")
        body_parts.append(content)
        body_parts.append("")

    markdown = "\n".join(body_parts)
    word_count = len(markdown.split())

    return {
        "markdown": markdown,
        "title": f"CODEC Shift Report — {today}",
        "trigger_kind": trigger_kind,
        "sections_included": sections_included,
        "word_count": word_count,
        "audit_records_scanned": len(audit_records),
        "notifications_scanned": len(notifs),
        "observer_summaries_used": len(summaries),
    }


def _post_notification(report: Dict[str, Any]) -> Optional[str]:
    """Append a type='shift_report' entry to ~/.codec/notifications.json
    and return the notification id."""
    try:
        try:
            with open(_NOTIFS_PATH) as f:
                notifs = json.load(f)
            if not isinstance(notifs, list):
                notifs = []
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            notifs = []
        nid = f"notif_{secrets.token_hex(5)}"
        entry = {
            "id": nid,
            "type": "shift_report",
            "title": report["title"],
            "body": report["markdown"],
            "status": "success",
            "created": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "read": False,
            "schedule_id": None,
            "doc_url": None,
            "trigger_kind": report["trigger_kind"],
            "word_count": report["word_count"],
        }
        notifs.insert(0, entry)
        _NOTIFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _NOTIFS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(notifs, indent=2, default=str))
        os.replace(tmp, _NOTIFS_PATH)
        return nid
    except Exception as e:
        _log.warning("shift_report notification post failed: %s", e)
        return None


def _maybe_auto_save(report: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Path]:
    """If config.shift_report.auto_save_path is set, write the markdown
    to that directory as YYYY-MM-DD.md."""
    save_root = cfg.get("auto_save_path")
    if not save_root:
        return None
    try:
        root = Path(os.path.expanduser(str(save_root)))
        root.mkdir(parents=True, exist_ok=True)
        out = root / f"{_today_local_date()}.md"
        out.write_text(report["markdown"])
        return out
    except Exception as e:
        _log.warning("shift_report auto_save failed: %s", e)
        return None


# ── Public entry: run() (skill API) ───────────────────────────────────────────
def run(task: str = "", app: str = "", ctx: str = "") -> str:
    """Skill entry. Invoked manually (chat / voice / MCP / "shift report")
    OR by codec_observer's idle/time fire path. Returns a short summary
    string for the caller; the actual report goes to notifications.json."""
    if not _enabled():
        return "Shift report is disabled (SHIFT_REPORT_ENABLED=false)."

    # Detect trigger_kind from task hints (observer passes a marker; manual
    # invocations don't).
    trigger_kind = "manual"
    if isinstance(task, str):
        low = task.lower()
        if "[trigger=time]" in low:
            trigger_kind = "time"
        elif "[trigger=idle]" in low:
            trigger_kind = "idle"

    return run_with_trigger_kind(trigger_kind)


_MANUAL_COOLDOWN_SECONDS = 300   # 5 min — protects against runaway loops


def _manual_cooldown_active() -> bool:
    """Returns True if a manual shift_report fired within the last
    `_MANUAL_COOLDOWN_SECONDS` seconds. Prevents button-mash and
    polling-loop spam from hammering the audit log."""
    state = _load_state()
    last = state.get("last_fired_at")
    if not last or state.get("last_trigger_kind") != "manual":
        return False
    try:
        last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
    return elapsed < _MANUAL_COOLDOWN_SECONDS


def run_with_trigger_kind(trigger_kind: str) -> str:
    """Internal entry — used by codec_observer when it knows the trigger
    kind. Per-day dedup means time AND idle on the same day fire once.
    Manual fires bypass per-day dedup but are protected by a 5-min
    cooldown so a button-mash or polling loop can't pile up reports."""
    if not _enabled():
        return "Shift report is disabled."
    if trigger_kind != "manual" and already_fired_today():
        return f"Shift report already fired today ({trigger_kind} suppressed)."
    if trigger_kind == "manual" and _manual_cooldown_active():
        return ("Shift report fired in the last 5 minutes — "
                "suppressed to prevent loop spam. "
                "Run again after the cooldown if you need a fresh one.")

    # Lazy-import codec_audit so the skill loads cleanly even in stripped envs
    try:
        from codec_audit import (
            SHIFT_REPORT_STARTED, SHIFT_REPORT_COMPLETED,
            log_event as _log_event,
        )
    except Exception:
        SHIFT_REPORT_STARTED = "shift_report_started"
        SHIFT_REPORT_COMPLETED = "shift_report_completed"

        def _log_event(*a, **kw):
            pass

    cid = secrets.token_hex(6)
    t0 = time.monotonic()
    try:
        _log_event(SHIFT_REPORT_STARTED, "codec-shift-report",
                   f"shift report starting ({trigger_kind})",
                   extra={"trigger_kind": trigger_kind},
                   outcome="ok", level="info",
                   correlation_id=cid)
    except Exception:
        pass

    cfg = _load_config()
    report = _assemble_shift_report(trigger_kind)
    nid = _post_notification(report)
    saved_path = _maybe_auto_save(report, cfg)
    # Always mark — manual fires need the timestamp for the 5-min cooldown
    # check above; idle/time still need it for per-day dedup.
    mark_fired_today(trigger_kind)

    duration_ms = (time.monotonic() - t0) * 1000.0
    try:
        _log_event(
            SHIFT_REPORT_COMPLETED, "codec-shift-report",
            f"shift report completed ({report['word_count']} words, "
            f"{report['sections_included']}/5 sections)",
            extra={
                "trigger_kind": trigger_kind,
                "sections_included": report["sections_included"],
                "word_count": report["word_count"],
                "audit_records_scanned": report["audit_records_scanned"],
                "notifications_scanned": report["notifications_scanned"],
                "observer_summaries_used": report["observer_summaries_used"],
            },
            outcome="ok", level="info",
            duration_ms=duration_ms,
            correlation_id=cid,
        )
    except Exception:
        pass

    summary = (
        f"Shift report posted ({trigger_kind} trigger): "
        f"{report['word_count']} words, "
        f"{report['sections_included']}/5 sections, "
        f"{report['audit_records_scanned']} audit records scanned"
        + (f", saved to {saved_path}" if saved_path else "")
        + "."
    )
    return summary
