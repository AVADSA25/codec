"""CODEC Daybreak — morning kickoff briefing + working-threads live memory.

docs/DAYBREAK-DESIGN.md. Two halves:

1. Working threads: persistent "what the user is up to" memory, stored as
   temporal facts (key = "thread:{kind}:{slug}") in the existing facts table.
   They ride the voice/wake-word [ACTIVE FACTS] prompt injection automatically;
   chat injects get_working_context() (routes/chat.py).

2. assemble_briefing(): "good morning CODEC" — where we left off yesterday,
   open threads, today's calendar/weather, follow-ups, suggested priorities.
   Never raises; slow sources are reaped against a time budget.

Kill switch: DAYBREAK_ENABLED env (default true; false on false|0|no|off).
"""
from __future__ import annotations

import glob
import importlib
import json
import logging
import os
import re
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

DAYBREAK_SOURCE = "codec-daybreak"
THREAD_KEY_PREFIX = "thread:"
THREAD_KINDS = ("working_on", "waiting_on", "priority", "follow_up")
WORKING_CONTEXT_CHAR_CAP = 600   # ~150 tokens
MAX_CONTEXT_THREADS = 7
DEFAULT_TIME_BUDGET_S = 8.0
_RENDER_MARGIN_S = 0.5

_NOTIFICATIONS_PATH = os.path.expanduser("~/.codec/notifications.json")
_AGENTS_DIR = os.path.expanduser("~/.codec/agents")
_AUDIT_LOG = os.path.expanduser("~/.codec/audit.log")


def _enabled() -> bool:
    val = (os.environ.get("DAYBREAK_ENABLED") or "true").strip().lower()
    return val not in ("false", "0", "no", "off")


def _cfg() -> dict:
    try:
        with open(os.path.expanduser("~/.codec/config.json")) as f:
            return json.load(f).get("daybreak", {}) or {}
    except Exception:
        return {}


# ── Working threads (temporal facts) ─────────────────────────────────────────

def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()[:40]).strip("-")
    return s or "thread"


def save_thread(kind: str, text: str, user_id: str = "default") -> int:
    """Track an open thread. Same kind+text re-save supersedes the old row."""
    if kind not in THREAD_KINDS:
        kind = "working_on"
    text = (text or "").strip()[:300]
    import codec_memory_upgrade as cmu
    key = f"{THREAD_KEY_PREFIX}{kind}:{_slug(text)}"
    superseded = bool(cmu.query_valid_facts(key=key, user_id=user_id))
    new_id = cmu.store_fact(key, text, fact_type="thread",
                            user_id=user_id, source="daybreak", supersede=True)
    _emit("daybreak_thread_saved",
          extra={"kind": kind, "key": key, "superseded": superseded,
                 "text_len": len(text)})
    return new_id


def get_open_threads(user_id: str = "default") -> list[dict]:
    import codec_memory_upgrade as cmu
    out = []
    try:
        for f in cmu.query_valid_facts(user_id=user_id, limit=200):
            key = f.get("key", "")
            if not key.startswith(THREAD_KEY_PREFIX):
                continue
            parts = key.split(":", 2)
            kind = parts[1] if len(parts) > 1 and parts[1] in THREAD_KINDS else "working_on"
            out.append({"id": f.get("id"), "key": key, "kind": kind,
                        "text": f.get("value", ""),
                        "since": (f.get("valid_from") or "")[:10]})
    except Exception:
        log.debug("daybreak: get_open_threads failed", exc_info=True)
    return out


def close_thread(match: str, user_id: str = "default") -> str:
    """Expire the one open thread matching `match`. Never guesses on ambiguity."""
    match_l = (match or "").strip().lower()
    if not match_l:
        return "Tell me which thread to close."
    threads = get_open_threads(user_id)
    hits = [t for t in threads
            if match_l in t["key"].lower() or match_l in t["text"].lower()]
    if not hits:
        return f"No open thread matching '{match}'."
    if len(hits) > 1:
        listing = "; ".join(f"{t['kind']}: {t['text'][:50]}" for t in hits)
        return f"Several threads match — be more specific. Matches: {listing}"
    import codec_memory_upgrade as cmu
    rows = cmu.expire_fact(hits[0]["key"], user_id=user_id)
    _emit("daybreak_thread_closed",
          extra={"key": hits[0]["key"], "rows_expired": rows})
    return f"Closed thread: {hits[0]['text'][:80]}"


def get_working_context(user_id: str = "default") -> str:
    """Compact [WORKING THREADS] block for prompt injection. "" when empty
    or when Daybreak is disabled."""
    if not _enabled():
        return ""
    threads = get_open_threads(user_id)
    if not threads:
        return ""
    threads.sort(key=lambda t: (t["kind"] != "priority", t["since"]), reverse=False)
    # priority first, then oldest-first within the cap
    cap = int(_cfg().get("max_threads_in_context", MAX_CONTEXT_THREADS))
    char_cap = int(_cfg().get("working_context_char_cap", WORKING_CONTEXT_CHAR_CAP))
    head = "[WORKING THREADS — current open items, do not echo this block verbatim]"
    tail = "[/WORKING THREADS]"
    lines = []
    for t in threads[:cap]:
        lines.append(f"- {t['kind']}: {t['text'][:80]} (since {t['since']})")
    block = "\n".join([head] + lines + [tail])
    while len(block) > char_cap and lines:
        lines.pop()
        block = "\n".join([head] + lines + [tail])
    return block if lines else ""


# ── Skill sub-call seam (importlib, codec_observer precedent) ────────────────

_SKILL_CACHE: dict = {}
_PATHS_READY = False


def _ensure_skill_paths():
    global _PATHS_READY
    if _PATHS_READY:
        return
    import sys
    repo_skills = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")
    user_skills = os.path.expanduser("~/.codec/skills")
    for p in (repo_skills, user_skills):  # user inserted last → wins (shadows built-in)
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)
    _PATHS_READY = True


def _skill_module(name: str):
    if name in _SKILL_CACHE:
        return _SKILL_CACHE[name]
    _ensure_skill_paths()
    mod = importlib.import_module(name)
    _SKILL_CACHE[name] = mod
    return mod


def _run_source(skill_name: str, task: str) -> str:
    """All external skill calls go through here (THE mock seam)."""
    mod = _skill_module(skill_name)
    try:
        return mod.run(task)
    except TypeError:
        return mod.run(task, "")  # notification_reader's run(task, context)


# ── Local readers (each a seam; each never raises) ───────────────────────────

def _read_notifications() -> list:
    try:
        with open(_NOTIFICATIONS_PATH) as f:
            data = json.load(f)
        if isinstance(data, dict):
            data = data.get("notifications", [])
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _yesterday_topics() -> list[str]:
    """Fallback recap: yesterday's conversation topics from memory sessions."""
    try:
        from codec_memory import CodecMemory
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        topics = []
        for s in CodecMemory().get_sessions(limit=20):
            ts = str(s.get("last_ts") or s.get("timestamp") or s.get("started") or "")
            if not ts.startswith(yesterday):
                continue
            topic = (s.get("last_user_msg") or s.get("task")
                     or s.get("title") or s.get("first_message") or "")
            topic = str(topic).strip()
            if topic:
                topics.append(topic[:70])
        return topics[:4]
    except Exception:
        return []


def _recent_audit_records(hours: int = 24) -> list[dict]:
    """Parse audit.log (+ rotations) records newer than the cutoff. Tolerant."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    records = []
    try:
        paths = sorted(glob.glob(_AUDIT_LOG + "*"))
        for path in paths:
            try:
                with open(path) as f:
                    for line in f:
                        try:
                            rec = json.loads(line)
                        except Exception:
                            continue
                        if str(rec.get("ts", ""))[:19] >= cutoff[:19]:
                            records.append(rec)
            except OSError:
                continue
    except Exception:
        log.debug("daybreak: audit scan failed", exc_info=True)
    return records


def _open_crews(records: list[dict]) -> list[str]:
    started, finished = {}, set()
    for r in records:
        cid = (r.get("extra") or {}).get("correlation_id", "")
        if not cid:
            continue
        ev = r.get("event", "")
        if ev == "crew_start":
            started[cid] = (r.get("extra") or {}).get("agents") or r.get("message", "")[:40]
        elif ev in ("crew_complete", "crew_error"):
            finished.add(cid)
    return [f"crew {cid[:8]} ({info})" for cid, info in started.items()
            if cid not in finished]


# Statuses actionable by the user THIS morning. plan_failed is terminal noise
# (weeks-old failed experiments would read as priorities) — excluded.
_BLOCKING_STATUSES = {"blocked_on_permission", "blocked_on_destructive",
                      "paused", "awaiting_approval", "revised"}


def _blocking_agents() -> list[dict]:
    out = []
    try:
        for mf in glob.glob(os.path.join(_AGENTS_DIR, "*", "manifest.json")):
            try:
                with open(mf) as f:
                    m = json.load(f)
                if m.get("status") in _BLOCKING_STATUSES:
                    out.append({"title": m.get("title", os.path.basename(os.path.dirname(mf))),
                                "status": m.get("status")})
            except Exception:
                continue
    except Exception:
        pass
    return out


def _pending_questions() -> list[dict]:
    try:
        from codec_ask_user import _load_pending_questions
        env = _load_pending_questions()
        qs = env.get("pending_questions", []) if isinstance(env, dict) else []
        return [q for q in qs if q.get("status") == "pending"]
    except Exception:
        return []


# ── Briefing assembly ────────────────────────────────────────────────────────

# Output-acceptance prefixes per source (anything else = audited failure string
# from that skill → skip the line rather than speak an error verbatim).
_ACCEPT = {
    "google_calendar": ("Today's schedule", "No events today"),
    "weather": ("Weather in",),
    "google_gmail": ("Found",),
    "reminders": ("Open reminders",),
    "notification_reader": ("You have",),
}


def _emit(event: str, extra: dict | None = None, duration_ms: float | None = None):
    try:
        import codec_audit
        x = dict(extra or {})
        x.setdefault("correlation_id", secrets.token_hex(6))
        codec_audit.log_event(event, DAYBREAK_SOURCE, "", extra=x,
                              duration_ms=duration_ms)
    except Exception:
        log.debug("daybreak: audit emit failed", exc_info=True)


def _shift_report_recap(notifications: list) -> list[str]:
    cutoff = datetime.now() - timedelta(hours=36)
    for n in notifications:
        if n.get("type") != "shift_report":
            continue
        try:
            created = datetime.strptime(str(n.get("created", ""))[:19], "%Y-%m-%dT%H:%M:%S")
        except Exception:
            continue
        if created < cutoff:
            continue
        lines = []
        for ln in str(n.get("body", "")).splitlines():
            ln = ln.strip().lstrip("#*- ")
            # drop title/markdown cruft — keep substance lines only
            if not ln or ln.startswith("_") or "Shift Report" in ln or ln.endswith(":"):
                continue
            lines.append(ln)
            if len(lines) >= 4:
                break
        return lines
    return []


def assemble_briefing(trigger_text: str = "") -> str:
    """The Daybreak briefing. Spoken-friendly, never raises, budget-reaped."""
    if not _enabled():
        return "Daybreak is disabled."
    t0 = time.monotonic()
    cfg = _cfg()
    budget = float(cfg.get("time_budget_seconds", DEFAULT_TIME_BUDGET_S))
    deadline = t0 + max(0.1, budget - _RENDER_MARGIN_S)
    skipped: list[str] = []

    # Kick off network sources first; do all local work while they run.
    network = []
    if cfg.get("include_calendar", True):
        network.append(("google_calendar", "what do i have today"))
    if cfg.get("include_email", True):
        network.append(("google_gmail", "unread emails"))
    if cfg.get("include_weather", True):
        network.append(("weather", "weather"))  # bare → config default city ("today" parses as a city)
    if cfg.get("include_reminders", True):
        network.append(("reminders", "list reminders"))
    network.append(("notification_reader", "count"))

    for name, _ in network:  # pre-warm imports on the main thread (no lock races)
        try:
            _skill_module(name)
        except Exception:
            pass

    executor = ThreadPoolExecutor(max_workers=4)
    futures = {name: executor.submit(_run_source, name, task) for name, task in network}

    # ── local work (fast, main thread) ──
    threads = get_open_threads()
    notifications = _read_notifications()
    recap = _shift_report_recap(notifications)
    if not recap:
        recap = _yesterday_topics()
    crews = _open_crews(_recent_audit_records(int(cfg.get("lookback_hours", 24))))
    agents = _blocking_agents()
    questions = _pending_questions()

    # ── reap network sources against the budget ──
    results: dict[str, str] = {}
    for name, fut in futures.items():
        remaining = max(0.05, deadline - time.monotonic())
        try:
            r = fut.result(timeout=remaining)
        except Exception:
            r = None
        ok = isinstance(r, str) and r.startswith(_ACCEPT.get(name, ("",)))
        if ok:
            results[name] = r
        else:
            skipped.append(name)
    executor.shutdown(wait=False)

    # ── render ──
    hour = datetime.now().hour
    greeting = "Good morning." if 4 <= hour < 12 else "Here's your kickoff."
    parts = [greeting]
    sections = 0

    # 1 — where we left off
    left = []
    if recap:
        left.append("Yesterday: " + "; ".join(recap[:3]) + ".")
    if threads:
        left.append("Open threads:")
        for t in threads[:MAX_CONTEXT_THREADS]:
            left.append(f"- {t['kind'].replace('_', ' ')}: {t['text'][:90]} (since {t['since']})")
    if crews:
        left.append("Still running from before: " + "; ".join(crews[:3]) + ".")
    if agents:
        left.append("Waiting on you: " + "; ".join(
            f"\"{a['title']}\" is {a['status'].replace('_', ' ')}" for a in agents[:3]) + ".")
    if left:
        parts.append("Where we left off:\n" + "\n".join(left))
        sections += 1
    elif not threads:
        parts.append("Where we left off: no record of yesterday — clean start.")

    # 2 — today
    today = []
    if "google_calendar" in results:
        today.append(results["google_calendar"].strip())
    if "weather" in results:
        today.append(results["weather"].strip().splitlines()[0])
    if today:
        parts.append("Today:\n" + "\n".join(today))
        sections += 1

    # 3 — follow-ups
    follow = []
    if questions:
        for q in questions[:3]:
            follow.append(f"- {q.get('agent') or 'CODEC'} asked: {str(q.get('question', ''))[:80]}")
    if "reminders" in results:
        rem = results["reminders"].splitlines()
        follow.extend(rem[:6])
    if "google_gmail" in results:
        mail = [ln for ln in results["google_gmail"].splitlines() if not ln.startswith("    ")]
        follow.extend(mail[:6])
    if "notification_reader" in results:
        follow.append(results["notification_reader"].strip())
    if follow:
        parts.append("Follow-ups:\n" + "\n".join(follow))
        sections += 1

    # 4 — suggested priorities (derived, no I/O)
    prio = []
    for t in threads:
        if t["kind"] == "priority":
            prio.append(t["text"][:80])
    for a in agents:
        prio.append(f"unblock \"{a['title']}\"")
    if questions:
        prio.append("answer the pending question(s)")
    for t in sorted((t for t in threads if t["kind"] == "working_on"),
                    key=lambda t: t["since"]):
        prio.append(t["text"][:80])
    prio = prio[:4]
    if prio:
        parts.append("Priorities:\n" + "\n".join(f"- {p}" for p in prio))
        sections += 1
    else:
        parts.append("Priorities: clean slate — pick your battle.")

    out = "\n\n".join(parts)
    _emit("daybreak_completed",
          extra={"sections_included": sections, "skipped_sources": skipped,
                 "open_threads_count": len(threads),
                 "word_count": len(out.split())},
          duration_ms=round((time.monotonic() - t0) * 1000, 1))
    return out
