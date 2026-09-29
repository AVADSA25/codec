"""CODEC Scheduler — Cron-like scheduling for agent crews and commands.

P3.3 (docs/P3.3-DESIGN.md): a job is a crew, a non-destructive skill or a free
prompt; its timing is written in plain language (parse_when / describe_when);
one runner (run_scheduled) serves timed fires and "Run now", records every run
with its full output in ~/.codec/schedule_runs.log, then delivers it
(notification, Today card, Google Doc, spoken on the Mac). A daily run missed
across midnight is caught up for CATCHUP_HOURS.
"""
import hashlib
import json
import os
import re
import threading
import time
import logging
import sys
from datetime import datetime, timedelta

import requests

# Audit emits route through codec_audit.log_event (real adapter, not no-op)
# per docs/PHASE1-STEP1-DESIGN.md.
from codec_audit import log_event

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [SCHEDULER] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scheduler")

SCHEDULE_PATH = os.path.expanduser("~/.codec/schedules.json")
RUNS_LOG = os.path.expanduser("~/.codec/schedule_runs.log")
CONFIG_PATH = os.path.expanduser("~/.codec/config.json")
VOICE_SESSION_PATH = os.path.expanduser("~/.codec/voice_session.json")
DASHBOARD_URL = "http://127.0.0.1:8090"

os.makedirs(os.path.expanduser("~/.codec"), exist_ok=True)


# ── Storage ─────────────────────────────────────────────────────────────────

def load_schedules() -> list:
    try:
        with open(SCHEDULE_PATH) as f:
            return json.load(f)
    except Exception:
        return []


def save_schedules(schedules: list):
    # Fix #9 Phase 1: atomic write (was truncate-then-write, racing readers).
    import codec_jsonstore
    codec_jsonstore.atomic_write_json(SCHEDULE_PATH, schedules)


def _update_schedules(mutate):
    """Lock + load + mutate + save schedules.json as one step (the dashboard's
    routes and the timed runner both write it)."""
    import codec_jsonstore
    with codec_jsonstore.file_lock(SCHEDULE_PATH):
        schedules = load_schedules()
        result = mutate(schedules)
        save_schedules(schedules)
        return result


# ── Management ──────────────────────────────────────────────────────────────

def add_schedule(
    crew_name: str,
    topic: str = "",
    cron_hour: int = 8,
    cron_minute: int = 0,
    days: list | None = None,
) -> dict:
    """Add a scheduled agent crew run. days: 0=Mon … 6=Sun, default every day."""
    schedule = {
        "id": f"sched_{int(time.time())}_{os.urandom(2).hex()}",
        "kind": "crew",
        "crew": crew_name,
        "topic": topic,
        "hour": cron_hour,
        "minute": cron_minute,
        "days": days if days is not None else [0, 1, 2, 3, 4, 5, 6],
        "enabled": False,
        "last_run": None,
        "created": datetime.now().isoformat(),
    }
    _update_schedules(lambda schedules: schedules.append(schedule))
    log.info(f"Schedule added: {crew_name} at {cron_hour:02d}:{cron_minute:02d}")
    return schedule


def remove_schedule(sched_id: str) -> bool:
    def mutate(schedules):
        before = len(schedules)
        schedules[:] = [s for s in schedules if s["id"] != sched_id]
        return len(schedules) < before
    return _update_schedules(mutate)


def toggle_schedule(sched_id: str, enabled: bool) -> bool:
    def mutate(schedules):
        for s in schedules:
            if s["id"] == sched_id:
                if enabled and not s.get("enabled"):
                    s["enabled_at"] = datetime.now().isoformat()
                s["enabled"] = enabled
                return True
        return False
    return _update_schedules(mutate)


# ── Execution ────────────────────────────────────────────────────────────────

def _notify(title, body, status="success", schedule_id=None):
    """Save notification to dashboard and send macOS notification."""
    import uuid as _uuid
    import subprocess as _sp
    import re as _re
    # Extract Google Doc URL if present (crew returns it as first line)
    doc_url = None
    doc_match = _re.search(r'(https://docs\.google\.com/document/d/[^\s]+)', body)
    if doc_match:
        doc_url = doc_match.group(1)
        # Clean body: remove raw URL line, add markdown link
        body = _re.sub(r'https://docs\.google\.com/document/d/[^\s]+\n*', '', body).strip()
        body = f"[View Full Report]({doc_url})\n\n{body}"
    # 1. Save to notifications.json (same format as dashboard)
    notif_path = os.path.expanduser("~/.codec/notifications.json")
    try:
        # Fix #9 Phase 2: hold the cross-process file_lock across the whole
        # load→insert→write so this daemon can't clobber a concurrent
        # dashboard / ask_user / heartbeat write of notifications.json.
        import codec_jsonstore
        with codec_jsonstore.file_lock(notif_path):
            try:
                with open(notif_path) as f:
                    notifications = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                notifications = []
            notif = {
                "id": f"notif_{_uuid.uuid4().hex[:10]}",
                "type": "task_report",
                "title": title,
                "body": body[:2000],
                "status": status,
                "created": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "read": False,
                "schedule_id": schedule_id,
            }
            if doc_url:
                notif["doc_url"] = doc_url
            notifications.insert(0, notif)
            codec_jsonstore.atomic_write_json(notif_path, notifications)
    except Exception as e:
        log.warning(f"  Failed to save notification: {e}")
    # 2. macOS notification
    mac_body = "Report ready — tap to view" if doc_url else body[:120]
    try:
        _sp.run(["osascript", "-e",
            f'display notification "{mac_body}" with title "CODEC Task" subtitle "{title}"'],
            capture_output=True, timeout=5)
    except Exception:
        pass


def _run_crew_output(sched: dict, context: str = ""):
    """Run a crew through the dashboard's background job endpoint and wait for
    it (up to 10 minutes). Returns (ok, result text)."""
    payload: dict = {"crew": sched["crew"]}
    topic = sched.get("topic") or ""
    if context:
        topic = (topic + "\n\n" + context).strip()
    if topic:
        payload["topic"] = topic
    # PR-2D (D-11 closure): replace `x-internal: codec` literal with HMAC token.
    try:
        from codec_keychain import get_internal_token
        _ipc_token = get_internal_token() or ""
    except Exception:
        _ipc_token = ""
    _headers = {"Content-Type": "application/json", "x-internal-token": _ipc_token}
    try:
        r = requests.post(f"{DASHBOARD_URL}/api/agents/run", json=payload, headers=_headers, timeout=30)
        if r.status_code != 200:
            log.warning(f"  /api/agents/run returned {r.status_code}")
            return False, f"The crew could not start (server returned {r.status_code})."
        data = r.json()
        job_id = data.get("job_id")
        if not job_id:
            log.info(f"  {sched['crew']} completed synchronously")
            return True, str(data.get("result") or "Task completed.")
        log.info(f"  Job started: {job_id} — polling for result…")
        for _ in range(120):
            time.sleep(5)
            sr = requests.get(f"{DASHBOARD_URL}/api/agents/status/{job_id}",
                              headers={"x-internal-token": _ipc_token}, timeout=10)
            if sr.status_code != 200:
                continue
            job_data = sr.json()
            st = job_data.get("status")
            if st in ("running", "pending"):
                continue
            result_text = job_data.get("result", "")
            if isinstance(result_text, dict):
                result_text = result_text.get("result", str(result_text))
            log.info(f"  {sched['crew']} finished: {st}")
            return st == "complete", str(result_text or "Task completed.")
        return False, "The crew did not finish within 10 minutes."
    except Exception as e:
        log.error(f"  Crew run failed: {e}")
        return False, f"Error: {e}"


# ── Plain-language timing (P3.3) ─────────────────────────────────────────────

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_DAY_WORDS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}
_ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]
_FILLER = {"at", "on", "every", "each", "and", "the", ",", "in", "of", "day", "days", "o'clock", "oclock"}
MIN_EVERY_MINUTES = 15
MAX_EVERY_MINUTES = 7 * 24 * 60
CATCHUP_HOURS = 12
RETRY_MINUTES = 10


def parse_when(text: str) -> dict:
    """Read a plain-language timing. Returns {"type": "daily", "hour", "minute",
    "days"} or {"type": "every", "minutes"}; raises ValueError with a message
    the page can show."""
    t = " ".join(str(text or "").lower().replace(",", " , ").split())
    if not t:
        raise ValueError("Say when, for example 'weekdays at 7:30' or 'every 2 hours'.")
    if t in ("hourly", "every hour", "each hour"):
        return {"type": "every", "minutes": 60}
    m = re.fullmatch(r"(?:every|each)\s+(\d+)\s*(minutes?|mins?|m|hours?|hrs?|h|days?|d)", t)
    if m:
        n, unit = int(m.group(1)), m.group(2)[0]
        minutes = n * (1 if unit == "m" else 60 if unit == "h" else 1440)
        if not MIN_EVERY_MINUTES <= minutes <= MAX_EVERY_MINUTES:
            raise ValueError("Repeat between every 15 minutes and every 7 days.")
        return {"type": "every", "minutes": minutes}
    if re.search(r"\b(tomorrow|today|tonight|next|once)\b", t):
        raise ValueError("Schedules repeat. Say which days and a time, like 'every day at 8'.")

    hour = minute = None
    rest = t
    m = re.search(r"\b(noon|midday|midnight)\b", rest)
    if m:
        hour, minute = (0 if m.group(1) == "midnight" else 12), 0
        rest = rest[:m.start()] + " " + rest[m.end():]
    else:
        m = re.search(r"\b(\d{1,2})(?:[:.h](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?(?![\w:])", rest)
        if m:
            hour, minute = int(m.group(1)), int(m.group(2) or 0)
            half = (m.group(3) or "").replace(".", "")
            if half:
                if not 1 <= hour <= 12:
                    raise ValueError(f"'{m.group(0).strip()}' is not a time.")
                hour = (0 if hour == 12 else hour) + (12 if half == "pm" else 0)
            if hour > 23 or minute > 59:
                raise ValueError(f"'{m.group(0).strip()}' is not a time.")
            rest = rest[:m.start()] + " " + rest[m.end():]

    days: set = set()
    words = rest.split()
    unknown = []
    i = 0
    while i < len(words):
        w = words[i]
        two = w + " " + words[i + 1] if i + 1 < len(words) else ""
        if two in ("every day", "each day", "business days", "work days"):
            days |= set(_ALL_DAYS) if two in ("every day", "each day") else {0, 1, 2, 3, 4}
            i += 2
            continue
        if w in ("daily", "everyday"):
            days |= set(_ALL_DAYS)
        elif w in ("weekdays", "weekday", "workdays", "workday"):
            days |= {0, 1, 2, 3, 4}
        elif w in ("weekends", "weekend"):
            days |= {5, 6}
        elif w.rstrip("s") in _DAY_WORDS or w in _DAY_WORDS:
            days.add(_DAY_WORDS.get(w, _DAY_WORDS.get(w.rstrip("s"))))
        elif w not in _FILLER:
            unknown.append(w)
        i += 1
    if unknown:
        raise ValueError(f"I could not read '{' '.join(unknown)}'. Try 'weekdays at 7:30' or 'every 2 hours'.")
    if hour is None:
        raise ValueError("Add a time, like 'at 8:00' or 'at 6pm'.")
    return {"type": "daily", "hour": hour, "minute": minute, "days": sorted(days) if days else list(_ALL_DAYS)}


def describe_when(when: dict) -> str:
    """The plain-language reading of a timing; parse_when reads it back."""
    if when.get("type") == "every":
        n = int(when["minutes"])
        if n == 60:
            return "Every hour"
        if n % 60 == 0:
            return f"Every {n // 60} hours"
        return f"Every {n} minutes"
    at = f"{int(when['hour']):02d}:{int(when['minute']):02d}"
    days = sorted(set(when.get("days") or _ALL_DAYS))
    if days == _ALL_DAYS:
        return f"Every day at {at}"
    if days == [0, 1, 2, 3, 4]:
        return f"Weekdays at {at}"
    if days == [5, 6]:
        return f"Weekends at {at}"
    names = [DAY_NAMES[d] for d in days]
    joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return f"{joined} at {at}"


def job_when(sched: dict) -> dict:
    """The job's timing; older jobs keep hour, minute and days at the top level."""
    w = sched.get("when")
    if isinstance(w, dict) and w.get("type") in ("daily", "every"):
        return w
    return {"type": "daily", "hour": int(sched.get("hour", 8)), "minute": int(sched.get("minute", 0)),
            "days": list(sched.get("days") or _ALL_DAYS)}


def _ts(value):
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _job_start(sched: dict):
    """When the job began to count: created, or last switched on."""
    stamps = [x for x in (_ts(sched.get("created")), _ts(sched.get("enabled_at"))) if x]
    return max(stamps) if stamps else None


def _last_due(when: dict, now: datetime):
    """The latest daily due time at or before `now` (looking back a week)."""
    days = set(when.get("days") or _ALL_DAYS)
    for back in range(8):
        day = (now - timedelta(days=back)).date()
        if day.weekday() not in days:
            continue
        cand = datetime(day.year, day.month, day.day, int(when["hour"]), int(when["minute"]))
        if cand <= now:
            return cand
    return None


def due(sched: dict, now: datetime | None = None) -> bool:
    """Should this job fire now? Daily: its latest due time is not yet covered by
    a run, falls after the job's start, and is from today or at most
    CATCHUP_HOURS ago. Every: the interval since the last run (or the start) has
    passed. A failed attempt is retried after RETRY_MINUTES."""
    if not sched.get("enabled"):
        return False
    now = now or datetime.now()
    attempt = _ts(sched.get("last_attempt"))
    if attempt and now - attempt < timedelta(minutes=RETRY_MINUTES):
        return False
    when = job_when(sched)
    last_run, start = _ts(sched.get("last_run")), _job_start(sched)
    if when["type"] == "every":
        base = max([x for x in (last_run, start) if x], default=None)
        return base is None or now - base >= timedelta(minutes=int(when["minutes"]))
    last_due = _last_due(when, now)
    if last_due is None or (last_run and last_run >= last_due) or (start and last_due < start):
        return False
    return last_due.date() == now.date() or now - last_due <= timedelta(hours=CATCHUP_HOURS)


def next_run(sched: dict, now: datetime | None = None):
    """When the job fires next, for display (None while it is off)."""
    if not sched.get("enabled"):
        return None
    now = now or datetime.now()
    if due(sched, now):
        return now
    when = job_when(sched)
    if when["type"] == "every":
        base = max([x for x in (_ts(sched.get("last_run")), _job_start(sched)) if x], default=now)
        return max(base + timedelta(minutes=int(when["minutes"])), now)
    days = set(when.get("days") or _ALL_DAYS)
    for ahead in range(8):
        day = (now + timedelta(days=ahead)).date()
        cand = datetime(day.year, day.month, day.day, int(when["hour"]), int(when["minute"]))
        if day.weekday() in days and cand > now:
            return cand
    return None


# ── Jobs: kinds, validation, view (P3.3) ─────────────────────────────────────

KINDS = ("crew", "skill", "prompt")
DELIVER = ("notification", "today", "gdoc")
_NEVER_SCHEDULED = {"ask_user", "stuck", "scheduler", "plugin_approve"}


def job_kind(sched: dict) -> str:
    k = sched.get("kind")
    return k if k in KINDS else "crew"


def job_title(sched: dict) -> str:
    kind = job_kind(sched)
    label = str(sched.get("label") or "").strip()
    if label:
        return label[:80]
    if kind == "prompt":
        return (str(sched.get("prompt") or "Prompt").strip().splitlines() or ["Prompt"])[0][:80]
    if kind == "skill":
        return f"{sched.get('skill')}: {str(sched.get('task') or '').strip()}"[:80].rstrip(": ")
    return str(sched.get("topic") or sched.get("crew") or "Scheduled task")[:80]


def schedulable_skill(name: str) -> bool:
    """A skill a job may run unattended: registered, not destructive (those keep
    their strict consent), not one that needs the owner per call over MCP HTTP,
    and not an interactive or trust-changing one."""
    if not name or name in _NEVER_SCHEDULED:
        return False
    try:
        import codec_consent
        from codec_config import _HTTP_CONSENT_REQUIRED, _HTTP_ONLY_BLOCKED
        from codec_dispatch import registry
        if name not in registry.names():
            return False
        if codec_consent.is_destructive_skill(name) or name in _HTTP_CONSENT_REQUIRED or name in _HTTP_ONLY_BLOCKED:
            return False
    except Exception:
        return False
    return True


def schedulable_skills() -> list:
    try:
        from codec_dispatch import registry
        if not registry.names():
            registry.scan()
        return [{"name": n, "description": registry.get_description(n)}
                for n in sorted(registry.names()) if schedulable_skill(n)]
    except Exception:
        return []


def clean_job_input(body: dict, existing: dict | None = None) -> dict:
    """The job fields from a create or update request, checked. Raises
    ValueError with a message for the page. Unknown keys are dropped."""
    if not isinstance(body, dict):
        raise ValueError("Send the job as a JSON object.")
    out: dict = {}
    kind = body.get("kind") or (job_kind(existing) if existing else ("crew" if "crew" in body else None))
    if kind not in KINDS:
        raise ValueError("Choose what to run: a prompt, a skill or a crew.")
    out["kind"] = kind
    merged = dict(existing or {})
    merged.update(body)
    if kind == "prompt":
        prompt = str(merged.get("prompt") or "").strip()
        if not prompt or len(prompt) > 4000:
            raise ValueError("Write what CODEC should do (up to 4000 characters).")
        out["prompt"] = prompt
    elif kind == "skill":
        name = str(merged.get("skill") or "").strip()
        if not schedulable_skill(name):
            raise ValueError(f"'{name}' cannot run on a schedule: pick a skill from the list.")
        out["skill"] = name
        out["task"] = str(merged.get("task") or "").strip()[:2000]
    else:
        crew = str(merged.get("crew") or "").strip()
        if not re.fullmatch(r"[a-z0-9_]{1,64}", crew):
            raise ValueError("Pick a crew.")
        out["crew"] = crew
        out["topic"] = str(merged.get("topic") or "").strip()[:2000]
    if "when" in body and isinstance(body["when"], str):
        out["when"] = parse_when(body["when"])
    elif "when" in body and isinstance(body["when"], dict):
        out["when"] = parse_when(describe_when(body["when"]))
    elif any(k in body for k in ("hour", "minute", "days")):
        days = body.get("days", merged.get("days")) or list(_ALL_DAYS)
        if not isinstance(days, list) or not all(isinstance(d, int) and 0 <= d <= 6 for d in days):
            raise ValueError("Days are numbers from 0 (Monday) to 6 (Sunday).")
        hour, minute = int(body.get("hour", merged.get("hour", 8))), int(body.get("minute", merged.get("minute", 0)))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("That is not a time of day.")
        out["when"] = {"type": "daily", "hour": hour, "minute": minute, "days": sorted(set(days))}
    elif existing is None:
        raise ValueError("Say when, for example 'weekdays at 7:30' or 'every 2 hours'.")
    if "label" in body:
        out["label"] = str(body.get("label") or "").strip()[:80]
    if "deliver" in body:
        deliver = [d for d in (body.get("deliver") or []) if d in DELIVER]
        out["deliver"] = deliver or ["notification"]
    for flag in ("speak", "only_if_changed", "continuity", "enabled"):
        if flag in body:
            out[flag] = bool(body[flag])
    if out.get("when", {}).get("type") == "daily":
        w = out["when"]
        out.update(hour=w["hour"], minute=w["minute"], days=w["days"])  # older readers
    return out


def create_job(body: dict) -> dict:
    fields = clean_job_input(body)
    now = datetime.now().isoformat()
    job = {"id": f"sched_{int(time.time())}_{os.urandom(2).hex()}", "created": now, "enabled": False,
           "last_run": None, "deliver": ["notification"]}
    job.update(fields)
    if job.get("enabled"):
        job["enabled_at"] = now
    _update_schedules(lambda schedules: schedules.append(job))
    return job


def update_job(sched_id: str, body: dict):
    """Apply a checked update; returns the job, or None when it does not exist."""
    def mutate(schedules):
        for s in schedules:
            if s.get("id") == sched_id:
                if s.get("managed"):  # e.g. the Morning briefing: set up in Settings; here only on/off
                    body_ = {k: v for k, v in body.items() if k == "enabled"}
                    if not body_:
                        raise ValueError("This job is set up in Settings.")
                    fields = {"enabled": bool(body_["enabled"])}
                else:
                    fields = clean_job_input(body, existing=s)
                if fields.get("enabled") and not s.get("enabled"):
                    s["enabled_at"] = datetime.now().isoformat()
                s.update(fields)
                return dict(s)
        return None
    return _update_schedules(mutate)


def job_view(sched: dict) -> dict:
    """The job plus what the page shows: kind, reading, next run."""
    view = dict(sched)
    view["kind"] = job_kind(sched)
    view["when"] = job_when(sched)
    view["summary"] = describe_when(view["when"])
    view["title"] = job_title(sched)
    nr = next_run(sched)
    view["next_run"] = nr.isoformat(timespec="minutes") if nr else None
    view.setdefault("deliver", ["notification"])
    return view


# ── Runs: execute, record, deliver (P3.3) ────────────────────────────────────

MAX_OUTPUT = 20000
_RUNS_MAX_BYTES = 5 * 1024 * 1024


def record_run(sched_id: str, title: str, status: str, output: str = "", *, changed=None,
               doc_url=None, manual: bool = False) -> dict:
    """Append one run to RUNS_LOG (JSON lines, 0600) with its full output."""
    import codec_jsonstore
    entry = {"timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "schedule_id": sched_id,
             "title": title, "status": status, "output": str(output or "")[:MAX_OUTPUT],
             "changed": changed, "doc_url": doc_url, "manual": manual}
    os.makedirs(os.path.dirname(RUNS_LOG), exist_ok=True)
    with codec_jsonstore.file_lock(RUNS_LOG):
        fd = os.open(RUNS_LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(json.dumps(entry) + "\n")
        try:
            os.chmod(RUNS_LOG, 0o600)
            if os.path.getsize(RUNS_LOG) > _RUNS_MAX_BYTES:
                with open(RUNS_LOG) as f:
                    keep = f.readlines()[-2000:]
                tmp = RUNS_LOG + ".tmp"
                with open(tmp, "w") as f:
                    f.writelines(keep)
                os.chmod(tmp, 0o600)
                os.replace(tmp, RUNS_LOG)
        except OSError:
            pass
    return entry


def _parse_run_line(line: str):
    line = line.strip()
    if not line:
        return None
    try:
        o = json.loads(line)
        if isinstance(o, dict):
            return {"timestamp": o.get("timestamp"), "schedule_id": o.get("schedule_id"),
                    "title": o.get("title") or o.get("schedule_id") or "", "status": o.get("status") or "",
                    "output": o.get("output", o.get("body_preview", "")) or "", "changed": o.get("changed"),
                    "doc_url": o.get("doc_url"), "manual": bool(o.get("manual"))}
    except ValueError:
        pass
    parts = [p.strip() for p in line.split("|")]
    if len(parts) >= 3:  # the oldest "timestamp | crew | status | duration" lines
        return {"timestamp": parts[0], "schedule_id": None, "title": parts[1], "status": parts[2],
                "output": parts[3] if len(parts) > 3 else "", "changed": None, "doc_url": None, "manual": False}
    return None


def read_runs(limit: int = 100, sched_id: str | None = None) -> list:
    """Runs newest first, parsed; old JSON (body_preview) and '|' lines too."""
    try:
        with open(RUNS_LOG) as f:
            lines = f.readlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        entry = _parse_run_line(line)
        if entry and (sched_id is None or entry.get("schedule_id") == sched_id):
            out.append(entry)
            if len(out) >= limit:
                break
    return out


def last_output(sched_id: str):
    """The latest successful run's output, or None."""
    for entry in read_runs(limit=500, sched_id=sched_id):
        if entry.get("status") == "success":
            return entry.get("output") or ""
    return None


def _config() -> dict:
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _run_prompt(sched: dict, context: str = ""):
    import codec_llm
    from codec_config import get_llm_api_key
    config = _config()
    user = str(sched.get("prompt") or "")
    if context:
        user += "\n\n" + context
    try:
        answer = codec_llm.call(
            [{"role": "system", "content": "You are CODEC, running a scheduled task for the owner on their Mac. "
                                          "Answer directly and completely, with no preamble."},
             {"role": "user", "content": user}],
            base_url=config.get("llm_base_url", "http://localhost:8083/v1"),
            model=config.get("llm_model", "mlx-community/Qwen3.6-35B-A3B-4bit"),
            api_key=get_llm_api_key() or "", max_tokens=4000, temperature=0.4, timeout=300,
            extra_kwargs=config.get("llm_kwargs") or {}, raise_on_error=True)
    except Exception as e:
        return False, f"The local model did not answer: {e}"
    answer = re.sub(r"<think>[\s\S]*?</think>", "", str(answer or "")).strip()
    return (True, answer) if answer else (False, "The local model returned an empty answer.")


def _run_skill_job(sched: dict):
    name = str(sched.get("skill") or "")
    if not schedulable_skill(name):
        return False, f"'{name}' cannot run on a schedule (it changes things on the Mac or needs you each time)."
    try:
        from codec_dispatch import run_skill
        result = run_skill({"name": name}, str(sched.get("task") or ""), app="CODEC Schedule")
    except Exception as e:
        return False, f"The {name} skill failed: {e}"
    if result is None:
        return False, f"The {name} skill did not handle this task."
    return True, str(result)


def _execute(sched: dict, previous):
    """Run the job once. Returns (ok, output)."""
    kind = job_kind(sched)
    context = ""
    if sched.get("continuity") and previous and kind != "skill":
        context = ("Your previous result for this task was:\n" + previous[:4000]
                   + "\n\nUse it as context and say what changed since then.")
    if kind == "prompt":
        return _run_prompt(sched, context)
    if kind == "skill":
        return _run_skill_job(sched)
    return _run_crew_output(sched, context)


def _plain(text: str) -> str:
    """Markdown to plain words for speaking."""
    s = re.sub(r"```[\s\S]*?(?:```|$)", " ", str(text or ""))
    s = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"https?://\S+", "the link", s)
    s = re.sub(r"[`*_#>|]+", " ", s)
    return " ".join(s.split())


def _speak_on_mac(text: str) -> None:
    """Say the result through Kokoro on the Mac (never over a live voice call)."""
    if os.path.exists(VOICE_SESSION_PATH):
        return
    words = _plain(text)[:900]
    cut = max(words.rfind(". "), words.rfind("! "), words.rfind("? "))
    words = words[:cut + 1] if cut > 200 else words
    if not words:
        return
    import subprocess
    import tempfile
    config = _config()
    try:
        r = requests.post(config.get("tts_url", "http://localhost:8085/v1/audio/speech"),
                          json={"model": config.get("tts_model", "mlx-community/Kokoro-82M-bf16"), "input": words,
                                "voice": config.get("tts_voice", "am_adam"),
                                "speed": float(config.get("tts_speed", 1.0))}, timeout=120)
        if r.status_code != 200 or not r.content:
            return
        fd, path = tempfile.mkstemp(suffix=".mp3")
        with os.fdopen(fd, "wb") as f:
            f.write(r.content)
        try:
            subprocess.run(["afplay", path], timeout=600, capture_output=True)
        finally:
            os.unlink(path)
    except Exception as e:
        log.warning(f"  Speaking the result failed: {e}")


def _deliver(sched: dict, title: str, output: str):
    """Send a successful result where the job asked. Returns the Google Doc URL."""
    deliver = [d for d in (sched.get("deliver") or ["notification"]) if d in DELIVER + ("briefing",)] or ["notification"]
    doc_url = None
    if "briefing" in deliver:  # P3.1: the Morning briefing's own delivery (card, push, script to say)
        try:
            import codec_briefing
            codec_briefing.deliver(sched, output)
        except Exception as e:
            log.warning(f"  Briefing delivery failed: {e}")
    if "gdoc" in deliver:
        try:
            from codec_gdocs import create_google_doc
            doc_url = create_google_doc(f"CODEC — {title} — {datetime.now().strftime('%b %d, %Y')}", output)
        except Exception as e:
            log.warning(f"  Google Doc failed: {e}")
    if "notification" in deliver:
        _notify(title, (doc_url + "\n\n" if doc_url else "") + output, status="success",
                schedule_id=sched.get("id"))
    if "today" in deliver:
        try:
            import codec_today
            codec_today.add_card(title, output, kind="schedule", source=sched.get("id"), url=doc_url)
        except Exception as e:
            log.warning(f"  Today card failed: {e}")
    if sched.get("speak"):
        threading.Thread(target=_speak_on_mac, args=(output,), name="codec-schedule-speak", daemon=True).start()
    return doc_url


def _digest(text: str) -> str:
    return hashlib.sha256(" ".join(str(text or "").split()).encode("utf-8")).hexdigest()


def run_scheduled(sched: dict, *, manual: bool = False) -> dict:
    """Run a job now (timed or "Run now"), record it, deliver it."""
    import secrets as _secrets
    sched_id = sched.get("id")
    title = job_title(sched)
    cid = _secrets.token_hex(6)
    t0 = time.monotonic()
    log.info(f"Scheduled run: {title}{' (manual)' if manual else ''}")
    log_event("schedule_fire", "codec-scheduler", f"Schedule fired: {title}",
              extra={"schedule_id": sched_id, "label": sched.get("label"), "crew": sched.get("crew"),
                     "kind": job_kind(sched), "manual": manual},
              correlation_id=cid)
    previous = last_output(sched_id) if sched_id else None
    ok, output = _execute(sched, previous)
    changed = bool(ok) and (previous is None or _digest(previous) != _digest(output))
    doc_url = None
    if ok and (changed or not sched.get("only_if_changed")):
        doc_url = _deliver(sched, title, output)
    elif not ok:
        _notify(title, output, status="error", schedule_id=sched_id)
    status = "success" if ok else "error"
    record_run(sched_id, title, status, output, changed=changed if ok else None, doc_url=doc_url, manual=manual)
    log_event("schedule_done", "codec-scheduler", f"Schedule done: {title}",
              outcome="ok" if ok else "error", duration_ms=(time.monotonic() - t0) * 1000.0,
              extra={"schedule_id": sched_id, "title": title, "changed": changed},
              correlation_id=cid)
    if ok and sched_id:
        def mark(schedules):
            for s in schedules:
                if s.get("id") == sched_id:
                    s["last_run"] = datetime.now().isoformat()
        _update_schedules(mark)
    return {"ok": ok, "changed": changed, "doc_url": doc_url, "status": status}


def check_and_run(now: datetime | None = None):
    """Run every job that is due (see `due`). The attempt time is saved first,
    so a slow or failing job is not started again for RETRY_MINUTES."""
    now = now or datetime.now()
    stamp = now.isoformat()

    def claim(schedules):
        picked = []
        for s in schedules:
            if due(s, now):
                if s.get("managed") == "briefing" and _briefing_busy():
                    continue  # P3.1: waits while an image job or low memory (caught up later)
                s["last_attempt"] = stamp
                picked.append(dict(s))
        return picked

    for sched in _update_schedules(claim):
        try:
            run_scheduled(sched)
        except Exception as e:
            log.error(f"Scheduled run failed: {e}")
    try:  # P3.1: say a waiting briefing at the first activity
        import codec_briefing
        codec_briefing.tick(now)
    except Exception as e:
        log.debug(f"Briefing tick failed: {e}")


def _briefing_busy() -> bool:
    try:
        import codec_briefing
        return bool(codec_briefing.busy())
    except Exception:
        return False


_PID_FILE = os.path.expanduser("~/.codec/scheduler.pid")


def _acquire_pid_lock() -> bool:
    """Ensure only one scheduler daemon runs. Returns True if lock acquired."""
    # Check if existing PID is still alive
    if os.path.exists(_PID_FILE):
        try:
            with open(_PID_FILE) as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)  # signal 0 = check if alive
            log.warning(f"Scheduler already running (PID {old_pid}). Exiting.")
            return False
        except (ProcessLookupError, ValueError):
            pass  # stale PID file, we can take over
        except PermissionError:
            log.warning("Scheduler PID exists and is owned by another user. Exiting.")
            return False
    # Write our PID
    os.makedirs(os.path.dirname(_PID_FILE), exist_ok=True)
    with open(_PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    return True


def _release_pid_lock():
    try:
        os.remove(_PID_FILE)
    except FileNotFoundError:
        pass


def run_daemon(check_interval: int = 60):
    """Run check_and_run every minute, aligned to the start of each minute."""
    if not _acquire_pid_lock():
        sys.exit(0)
    try:
        schedules = load_schedules()
        log.info(f"Scheduler daemon starting (PID {os.getpid()}) — {len(schedules)} schedule(s) loaded")
        while True:
            try:
                check_and_run()
            except Exception as e:
                log.error(f"Scheduler loop error: {e}")
            # Sleep until the next minute boundary to avoid drift
            now = time.time()
            sleep_secs = check_interval - (now % check_interval)
            time.sleep(max(1, sleep_secs))
    finally:
        _release_pid_lock()


# ── CODEC Skill (voice control) ──────────────────────────────────────────────

SKILL_NAME = "scheduler"
SKILL_TRIGGERS = [
    "schedule agent", "schedule crew", "run every morning",
    "run every monday", "schedule daily", "run daily briefing",
    "set up schedule", "every morning at", "every monday",
    "schedule competitor analysis", "run briefing at",
]
SKILL_DESCRIPTION = "Schedule CODEC agent crews to run automatically on a cron schedule"

_DAY_MAP = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
    "weekdays": [0, 1, 2, 3, 4], "weekends": [5, 6],
    "every day": [0, 1, 2, 3, 4, 5, 6], "daily": [0, 1, 2, 3, 4, 5, 6],
}

_CREW_MAP = {
    "daily briefing": "daily_briefing",
    "briefing": "daily_briefing",
    "competitor": "competitor_analysis",
    "competitor analysis": "competitor_analysis",
    "social media": "social_media",
    "code review": "code_review",
    "data analysis": "data_analysis",
}


def _parse_schedule_intent(task: str) -> dict | None:
    """Parse natural language like 'run my daily briefing every morning at 8'."""
    import re
    tl = task.lower()

    # Detect crew
    crew = "daily_briefing"
    for phrase, name in _CREW_MAP.items():
        if phrase in tl:
            crew = name
            break

    # Detect hour
    hour = 8
    m = re.search(r"at (\d{1,2})(?::(\d{2}))?\s*(?:am|pm)?", tl)
    if m:
        hour = int(m.group(1))
        minute_str = m.group(2)
        minute = int(minute_str) if minute_str else 0
        if "pm" in tl and hour < 12:
            hour += 12
    else:
        minute = 0

    # Detect days
    days = [0, 1, 2, 3, 4, 5, 6]
    for phrase, val in _DAY_MAP.items():
        if phrase in tl:
            days = val if isinstance(val, list) else [val]
            break

    return {"crew": crew, "hour": hour, "minute": minute, "days": days}


def run(task: str, context: str = "") -> str:
    """Voice-triggered schedule creation."""
    tl = task.lower()

    if "list" in tl or "show" in tl or "what schedule" in tl:
        schedules = load_schedules()
        if not schedules:
            return "No schedules set up yet. Say 'schedule daily briefing at 8am' to create one."
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        lines = [f"{len(schedules)} schedule(s):"]
        for s in schedules:
            days_str = ", ".join(day_names[d] for d in s.get("days", []))
            status = "on" if s.get("enabled") else "off"
            lines.append(f"  {s['crew']} at {s['hour']:02d}:{s['minute']:02d} [{days_str}] ({status})")
        return "\n".join(lines)

    if "remove" in tl or "delete" in tl or "cancel" in tl:
        schedules = load_schedules()
        if schedules:
            remove_schedule(schedules[-1]["id"])
            return f"Removed last schedule: {schedules[-1]['crew']}"
        return "No schedules to remove."

    intent = _parse_schedule_intent(task)
    if not intent:
        return "I couldn't parse that schedule. Try: 'run daily briefing every morning at 8'"

    s = add_schedule(
        intent["crew"],
        cron_hour=intent["hour"],
        cron_minute=intent["minute"],
        days=intent["days"],
    )
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    days_str = ", ".join(day_names[d] for d in s["days"])
    return (
        f"Scheduled: {s['crew']} will run at {s['hour']:02d}:{s['minute']:02d} "
        f"on {days_str}. Say 'list schedules' to see all."
    )


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        run_daemon()
    elif sys.argv[1] == "list":
        schedules = load_schedules()
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        if not schedules:
            print("No schedules.")
        for s in schedules:
            days_str = ", ".join(day_names[d] for d in s.get("days", []))
            status = "✅" if s.get("enabled") else "❌"
            print(f"  {status} {s['id']}: {s['crew']} at {s['hour']:02d}:{s['minute']:02d} [{days_str}]")
    elif sys.argv[1] == "add" and len(sys.argv) > 2:
        crew = sys.argv[2]
        hour = 8
        minute = 0
        days = None
        i = 3
        while i < len(sys.argv):
            if sys.argv[i] == "--hour" and i + 1 < len(sys.argv):
                hour = int(sys.argv[i + 1]); i += 2
            elif sys.argv[i] == "--minute" and i + 1 < len(sys.argv):
                minute = int(sys.argv[i + 1]); i += 2
            elif sys.argv[i] == "--days" and i + 1 < len(sys.argv):
                days = [int(d) for d in sys.argv[i + 1].split(",")]; i += 2
            else:
                i += 1
        s = add_schedule(crew, cron_hour=hour, cron_minute=minute, days=days)
        print(f"Added: {s['id']} — {crew} at {hour:02d}:{minute:02d}")
    elif sys.argv[1] == "remove" and len(sys.argv) > 2:
        if remove_schedule(sys.argv[2]):
            print(f"Removed {sys.argv[2]}")
        else:
            print(f"Not found: {sys.argv[2]}")
    elif sys.argv[1] == "run":
        check_and_run()
    else:
        run_daemon()
