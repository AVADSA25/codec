"""CODEC Scheduler — Cron-like scheduling for agent crews and commands."""
import json
import os
import time
import logging
import sys
from datetime import datetime

import requests

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [SCHEDULER] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scheduler")

SCHEDULE_PATH = os.path.expanduser("~/.codec/schedules.json")
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
    with open(SCHEDULE_PATH, "w") as f:
        json.dump(schedules, f, indent=2)


# ── Management ──────────────────────────────────────────────────────────────

def add_schedule(
    crew_name: str,
    topic: str = "",
    cron_hour: int = 8,
    cron_minute: int = 0,
    days: list | None = None,
) -> dict:
    """Add a scheduled agent crew run. days: 0=Mon … 6=Sun, default every day."""
    schedules = load_schedules()
    schedule = {
        "id": f"sched_{int(time.time())}",
        "crew": crew_name,
        "topic": topic,
        "hour": cron_hour,
        "minute": cron_minute,
        "days": days if days is not None else [0, 1, 2, 3, 4, 5, 6],
        "enabled": False,
        "last_run": None,
        "created": datetime.now().isoformat(),
    }
    schedules.append(schedule)
    save_schedules(schedules)
    log.info(f"Schedule added: {crew_name} at {cron_hour:02d}:{cron_minute:02d}")
    return schedule


def remove_schedule(sched_id: str) -> bool:
    schedules = load_schedules()
    before = len(schedules)
    schedules = [s for s in schedules if s["id"] != sched_id]
    save_schedules(schedules)
    return len(schedules) < before


def toggle_schedule(sched_id: str, enabled: bool) -> bool:
    schedules = load_schedules()
    for s in schedules:
        if s["id"] == sched_id:
            s["enabled"] = enabled
            save_schedules(schedules)
            return True
    return False


# ── Execution ────────────────────────────────────────────────────────────────

def _notify(title, body, status="success", schedule_id=None):
    """Save notification to dashboard and send macOS notification."""
    import uuid as _uuid, subprocess as _sp, re as _re
    # Extract Google Doc URL if present (crew returns it as first line)
    doc_url = None
    doc_match = _re.search(r'(https://docs\.google\.com/document/d/[^\s]+)', body)
    if doc_match:
        doc_url = doc_match.group(1)
        # Clean body: remove raw URL line, add markdown link
        body = _re.sub(r'https://docs\.google\.com/document/d/[^\s]+\n*', '', body).strip()
        body = f"📄 [View Full Report]({doc_url})\n\n{body}"
    # 1. Save to notifications.json (same format as dashboard)
    notif_path = os.path.expanduser("~/.codec/notifications.json")
    try:
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
        os.makedirs(os.path.dirname(notif_path), exist_ok=True)
        with open(notif_path, "w") as f:
            json.dump(notifications, f, indent=2)
    except Exception as e:
        log.warning(f"  Failed to save notification: {e}")
    # 2. macOS notification
    mac_body = f"Report ready — tap to view" if doc_url else body[:120]
    try:
        _sp.run(["osascript", "-e",
            f'display notification "{mac_body}" with title "CODEC Task" subtitle "{title}"'],
            capture_output=True, timeout=5)
    except Exception:
        pass


def _run_crew(sched: dict):
    """Fire off a crew via the background job endpoint and optionally poll."""
    payload: dict = {"crew": sched["crew"]}
    if sched.get("topic"):
        payload["topic"] = sched["topic"]

    title = sched.get("topic", sched["crew"])
    _headers = {"Content-Type": "application/json", "x-internal": "codec"}
    try:
        r = requests.post(
            f"{DASHBOARD_URL}/api/agents/run",
            json=payload,
            headers=_headers,
            timeout=30,
        )
        if r.status_code == 200:
            data = r.json()
            job_id = data.get("job_id")
            if job_id:
                log.info(f"  Job started: {job_id} — polling for result…")
                # Poll up to 10 min
                for _ in range(120):
                    time.sleep(5)
                    sr = requests.get(
                        f"{DASHBOARD_URL}/api/agents/status/{job_id}",
                        headers={"x-internal": "codec"},
                        timeout=10,
                    )
                    if sr.status_code == 200:
                        job_data = sr.json()
                        st = job_data.get("status")
                        if st not in ("running", "pending"):
                            result_text = job_data.get("result", "")
                            if isinstance(result_text, dict):
                                result_text = result_text.get("result", str(result_text))
                            log.info(f"  ✅ {sched['crew']} finished: {st}")
                            _notify(title, str(result_text)[:500] if result_text else "Task completed.",
                                    status="success" if st == "complete" else "error",
                                    schedule_id=sched.get("id", sched["crew"]))
                            return True
            else:
                log.info(f"  ✅ {sched['crew']} completed synchronously")
                _notify(title, "Task completed successfully.",
                        schedule_id=sched.get("id", sched["crew"]))
                return True
        else:
            log.warning(f"  ⚠️ /api/agents/run returned {r.status_code}")
            _notify(title, f"Failed: server returned {r.status_code}", status="error",
                    schedule_id=sched.get("id", sched["crew"]))
    except Exception as e:
        log.error(f"  ❌ Crew run failed: {e}")
        _notify(title, f"Error: {e}", status="error",
                schedule_id=sched.get("id", sched["crew"]))
    return False


def check_and_run():
    """Check every minute whether any schedules should fire.

    Uses "past scheduled time" logic so tasks fire even if the scheduler
    starts after the exact minute.  ``last_run`` is only set after a
    successful execution; ``last_attempt`` prevents rapid re-firing within
    the same minute.
    """
    schedules = load_schedules()
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    now_minutes = now.hour * 60 + now.minute  # minutes since midnight

    for sched in schedules:
        if not sched.get("enabled"):
            continue
        if now.weekday() not in sched.get("days", list(range(7))):
            continue

        sched_minutes = sched["hour"] * 60 + sched["minute"]
        if now_minutes < sched_minutes:
            continue  # not yet reached scheduled time today

        # Already successfully ran today — skip
        last_run = sched.get("last_run")
        if last_run and last_run[:10] == today_str:
            continue

        # Prevent rapid re-firing within the same minute
        last_attempt = sched.get("last_attempt")
        if last_attempt and last_attempt[:16] == now.strftime("%Y-%m-%dT%H:%M"):
            continue

        # Record attempt timestamp (prevents double-fire from race / same-minute loop)
        sched["last_attempt"] = now.isoformat()
        save_schedules(schedules)

        log.info(f"🚀 Scheduled run: {sched['crew']} — {sched.get('topic', '')}")
        success = _run_crew(sched)

        # Only mark last_run on success so failed tasks are retried next minute
        if success:
            schedules = load_schedules()  # reload in case file changed during long run
            for s in schedules:
                if s["id"] == sched["id"]:
                    s["last_run"] = datetime.now().isoformat()
                    break
            save_schedules(schedules)


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
            status = "✅" if s.get("enabled") else "❌"
            lines.append(f"  {status} {s['crew']} at {s['hour']:02d}:{s['minute']:02d} [{days_str}]")
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
