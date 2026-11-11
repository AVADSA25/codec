"""CODEC Heartbeat — periodic check of logs, memory, and pending tasks"""
import copy
import fcntl
import re
import time
import sqlite3
import os
import json
import logging
import requests
import subprocess  # F-4: used in the alert path (osascript display notification) — was unimported
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] [HEARTBEAT] %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger('heartbeat')

# Audit emits route through the unified log_event adapter (real, not no-op)
# per docs/PHASE1-STEP1-DESIGN.md.
from codec_audit import log_event

try:
    import sys as _sys
    _repo = os.path.dirname(os.path.abspath(__file__))
    if _repo not in _sys.path:
        _sys.path.insert(0, _repo)
    from codec_config import DB_PATH, CONFIG_PATH
except ImportError:
    DB_PATH = os.path.expanduser("~/.codec/memory.db")
    CONFIG_PATH = os.path.expanduser("~/.codec/config.json")

def check_pending_tasks():
    """Check memory for tasks that were saved for later"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5.0); conn.execute("PRAGMA busy_timeout=5000")
        # Find messages containing "task" or "later" or "remind" from last 24h
        cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
        rows = conn.execute("""
            SELECT content, timestamp FROM conversations
            WHERE timestamp > ?
            AND (content LIKE '%task%' OR content LIKE '%later%' OR content LIKE '%remind%' OR content LIKE '%todo%')
            AND role = 'assistant'
            ORDER BY id DESC LIMIT 5
        """, (cutoff,)).fetchall()
        conn.close()

        if rows:
            log.info(f"Found {len(rows)} pending items in memory")
            for content, ts in rows:
                log.info(f"  [{ts[:16]}] {len(content or '')} characters")  # P-032: lengths, never the text
        return rows
    except Exception as e:
        log.error(f"Task check failed: {e}")
        return []

def _check_one_service(name: str, url: str, starting: bool = False) -> tuple:
    """Check a single service endpoint. Returns (name, status_string).

    `starting` (P-059): the service's PM2 app started under 5 min ago and may
    still be loading, so no answer reads "starting" and is no service_down."""
    try:
        r = requests.get(url, timeout=5)
        status = "ok" if r.status_code in (200, 404, 405) else f"HTTP {r.status_code}"
    except Exception:
        if starting:
            return name, "starting"
        status = "DOWN"
        log_event("service_down", "codec-heartbeat",
                  f"Service down: {name}",
                  outcome="error", level="error",
                  extra={"service": name, "url": url})
    return name, status


# The PM2 app behind each check_system_health probe, for the start grace (P-059).
_HEALTH_PM2_APPS = {"LLM/Vision": "qwen3.6", "Whisper": "whisper-stt",
                    "Kokoro": "kokoro-82m", "Dashboard": "codec-dashboard"}


def _apps_starting(procs, now_ts=None) -> set:
    """Probe names whose PM2 app started under 5 min ago, by the rule of
    codec_models.pm2_app_loading on the heartbeat's `pm2 jlist` list."""
    if not procs:
        return set()
    try:
        import codec_models
        return {name for name, app in _HEALTH_PM2_APPS.items()
                if codec_models.pm2_app_loading(procs, app, now_ts)}
    except Exception:
        return set()


def check_system_health(procs=None, now_ts=None):
    """Verify all CODEC services are running (checks run in parallel).

    Only HTTP-exposing services are probed here. PM2-supervised daemons
    (codec-observer, codec-agent-runner) rely on PM2's autorestart for
    crash recovery — see AGENTS.md §3 "Background Execution".

    `procs` is the heartbeat's `pm2 jlist` list (P-059): a service whose PM2
    app started under 5 min ago is still loading, so its silence is not a
    service_down event. `now_ts` (epoch seconds) is the clock, for tests.
    """
    # NOTE: LLM and Vision are served by the same qwen process on :8083 —
    # probe it ONCE. The former duplicate "Vision" entry double-counted
    # every 8083 blip as two service_down audit events (2026-07 log
    # review: 30 of 35 service_down events were LLM+Vision pairs).
    services = {
        "LLM/Vision": "http://localhost:8083/v1/models",
        "Whisper": "http://localhost:8084/health",
        "Kokoro": "http://localhost:8085/v1/models",
        "Dashboard": "http://localhost:8090/",
    }
    starting = _apps_starting(procs, now_ts)
    with ThreadPoolExecutor(max_workers=len(services)) as pool:
        futures = {
            pool.submit(_check_one_service, name, url, name in starting): name
            for name, url in services.items()
        }
        for future in as_completed(futures):
            name, status = future.result()
            log.info(f"  {name}: {status}")

def check_memory_stats():
    """Report memory database stats + size monitoring"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5.0); conn.execute("PRAGMA busy_timeout=5000")
        total = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        sessions = conn.execute("SELECT COUNT(DISTINCT session_id) FROM conversations").fetchone()[0]
        latest = conn.execute("SELECT timestamp FROM conversations ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()

        # Database size monitoring
        db_size_mb = os.path.getsize(DB_PATH) / (1024 * 1024) if os.path.exists(DB_PATH) else 0
        size_warn = " (large)" if db_size_mb > 100 else ""
        log.info(f"Memory: {total} entries, {sessions} sessions, {db_size_mb:.1f} MB{size_warn}, latest: {latest[0][:16] if latest else 'none'}")

        if db_size_mb > 100:
            try:
                from codec_keychain import get_internal_token
                _token = get_internal_token() or ""
                requests.post("http://localhost:8090/api/notifications",
                              json={"message": f"Warning: Memory DB is {db_size_mb:.0f} MB — consider running cleanup", "type": "warning", "source": "heartbeat"},
                              headers={"x-internal-token": _token},
                              timeout=5)
            except Exception:
                pass
    except Exception as e:
        log.error(f"Memory stats failed: {e}")


def backup_memory_db():
    """Daily backups to ~/.codec/backups/ through codec_backup, the one writer
    (docs/BACKUP-RETENTION-DESIGN.md): memory.db (30 kept), qchat.db and vibe.db
    (7 kept), each copy checked with PRAGMA quick_check before it replaces
    anything, plus the state tar (agents and settings files, 7 kept). 0600."""
    try:
        import codec_backup
        for r in codec_backup.daily_backups():
            if not r.get("ok") and not r.get("skipped"):
                log.warning(f"{r['prefix']} backup rejected: {r.get('check')} (older copies kept)")
    except Exception as e:
        log.warning(f"Backups failed: {e}")

# ── PM2 restart-storm detection (2026-07 log review) ─────────────────────────
#
# ava-litellm crash-looped 34,207 times over ~3 weeks with zero alerts —
# PM2's autorestart hides a permanently-failing service behind status
# "online". Every heartbeat we snapshot per-process restart counters and
# alert when an autorestart-enabled process burned >= _RESTART_STORM_DELTA
# restarts since the previous heartbeat (~20 min). Cron-style jobs
# (autorestart=false, e.g. intake-*, sentora-backup) are excluded — their
# counters increment by design on every scheduled run.

_PM2_BIN = "/opt/homebrew/bin/pm2"
_RESTART_STATE_PATH = os.path.expanduser("~/.codec/pm2_restart_state.json")
_RESTART_STORM_DELTA = 5           # restarts per heartbeat interval
_RESTART_STORM_REALERT_S = 6 * 3600  # re-alert a persisting storm every 6h


def _supervisor() -> str:
    """'pm2' or 'launchd' (P-032, codec_supervisor.kind); 'pm2' if that cannot be read."""
    try:
        import codec_supervisor
        return codec_supervisor.kind()
    except Exception:
        return "pm2"


def _logs_hint(name: str) -> str:
    """Where an alert text sends the owner to read a service's output (P-032)."""
    try:
        import codec_supervisor
        return codec_supervisor.logs_hint(name)
    except Exception:
        return f"pm2 logs {name}"


def _pm2_jlist() -> list:
    if _supervisor() == "launchd":  # P-032: a buyer's Mac runs the fleet as LaunchAgents, in PM2's shape
        import codec_supervisor
        return codec_supervisor.launchd_jlist()
    try:
        env = os.environ.copy()
        env["PATH"] = "/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:" + env.get("PATH", "")
        out = subprocess.check_output([_PM2_BIN, "jlist"], timeout=15, env=env)
        return json.loads(out)
    except Exception as e:
        log.warning(f"pm2 jlist failed: {e}")
        return []


def check_pm2_restart_storms(procs=None) -> list:
    """Detect crash-looping PM2 processes. Returns [(name, delta), ...].
    `procs` is injectable for tests (defaults to live `pm2 jlist`)."""
    if procs is None:
        procs = _pm2_jlist()
    if not procs:
        return []
    try:
        with open(_RESTART_STATE_PATH) as f:
            state = json.load(f)
    except Exception:
        state = {}
    counts = state.get("counts", {})
    last_alert = state.get("last_alert", {})
    now = datetime.now()
    storms = []
    launchd = _supervisor() == "launchd"  # P-032: launchd's own words, and a line for each restart

    for p in procs:
        env = p.get("pm2_env", {}) or {}
        if not env.get("autorestart"):
            continue  # cron jobs restart by design
        name = p.get("name", "")
        n = int(env.get("restart_time", 0) or 0)
        prev = counts.get(name)
        if prev is not None and (n - prev) >= _RESTART_STORM_DELTA:
            delta = n - prev
            storms.append((name, delta))
            realert_ok = True
            if name in last_alert:
                try:
                    elapsed = (now - datetime.fromisoformat(last_alert[name])).total_seconds()
                    realert_ok = elapsed >= _RESTART_STORM_REALERT_S
                except Exception:
                    pass
            if realert_ok:
                last_alert[name] = now.isoformat()
                msg = (f"🔁 {'launchd' if launchd else 'PM2'} restart storm: '{name}' restarted {delta}× since "
                       f"the last heartbeat ({n} total) — likely crash-looping.")
                log.warning(f"  {msg}")
                log_event("pm2_restart_storm", "codec-heartbeat", msg,
                          outcome="warning", level="warning",
                          extra={"process": name, "delta": delta, "total_restarts": n})
                try:
                    from codec_alerts import send_alert
                    send_alert("warning", msg)
                except Exception:
                    pass
        elif launchd and prev is not None and n > prev:
            # KeepAlive brought it back: it was down since the last heartbeat (P-032)
            log.warning(f"  launchd restarted {name} {n - prev}x since the last heartbeat "
                        f"(last exit code {env.get('exit_code', 'unknown')})")
        counts[name] = n

    try:
        os.makedirs(os.path.dirname(_RESTART_STATE_PATH), exist_ok=True)
        with open(_RESTART_STATE_PATH, "w") as f:
            json.dump({"counts": counts, "last_alert": last_alert}, f)
    except Exception:
        pass
    return storms


# CODEC's own PM2 apps: every "codec-*" name plus these.
_CODEC_PM2_APPS = ("open-codec", "whisper-stt", "kokoro-82m")
_AUTO_PULL_LOG = os.path.expanduser("~/.codec/logs/auto_pull.log")
DAILY_STATUS_AT = (7, 30)  # local time; the first heartbeat after it sends


def _is_codec_app(name: str) -> bool:
    return name.startswith("codec-") or name in _CODEC_PM2_APPS


def check_errored_codec_apps(procs=None) -> list:
    """Alert when a CODEC PM2 app is `errored`: its crash loop hit the PM2 cap
    (min_uptime + max_restarts) and PM2 gave up restarting it. One message per
    app, repeated at most every 6 h, and one when it runs again."""
    from codec_alerts import alert_once, alert_resolved
    if procs is None:
        procs = _pm2_jlist()
    errored = []
    for p in procs or []:
        name = p.get("name", "")
        if not _is_codec_app(name):
            continue
        status = (p.get("pm2_env") or {}).get("status")
        key = f"pm2_errored:{name}"
        if status == "errored":
            errored.append(name)
            # P-032: under launchd, "errored" is a KeepAlive service launchd is not starting again
            what = ("is down and launchd is not starting it again" if _supervisor() == "launchd"
                    else "crashed repeatedly and PM2 gave up restarting it")
            alert_once(key, "critical",
                       f"CODEC ALERT: {name} {what}. "
                       f"It needs a look ({_logs_hint(name)}).")
        elif status == "online":
            alert_resolved(key, f"CODEC RECOVERED: {name} is running again.")
    return errored


# ── Health beats (P-017, docs/HEALTH-BEATS-DESIGN.md) ────────────────────────
# PM2 shows a bridge "online" while every round of its loop fails or while it hangs. Each bridge writes
# a beat a round (codec_health); the observer's beat is its newest observation_tick audit line.

_OBSERVER_APP = "codec-observer"
_OFF = ("false", "0", "no", "off")


def _pm2_app(procs, name):
    return next((p for p in procs or [] if p.get("name") == name), None)


def _observer_should_tick(procs) -> bool:
    """codec-observer is online in PM2, switched on (PM2 env and config) and not paused."""
    env = (_pm2_app(procs, _OBSERVER_APP) or {}).get("pm2_env") or {}
    if env.get("status") != "online":
        return False
    flag = env.get("OBSERVER_ENABLED", (env.get("env") or {}).get("OBSERVER_ENABLED", "true"))
    if str(flag).strip().lower() in _OFF:
        return False
    try:
        import codec_observer
        return (codec_observer._load_config().get("enabled", True) is not False
                and codec_observer.paused_until() is None)
    except Exception:
        return False


def check_health_beats(procs=None, now_ts=None) -> list:
    """Alert when a beat is silent (older than 3x its cadence, at least 15 min) or failing (20 rounds
    in a row ended in an error): once, again at most every 6 h, and one message when it clears.

    `procs` is the heartbeat's `pm2 jlist` list: an app it does not show online is stopped and not
    checked, an app in its 5-minute start grace is skipped this round (P-059), and an app it shows
    online again is checked by age even when its file still holds the stop mark of the run before.
    Without a list the files alone decide (a stop on purpose marks its file). A bad file is skipped
    and the others are still checked. Returns the open problem keys."""
    import codec_audit
    import codec_health
    import codec_models
    from codec_alerts import alert_once, alert_resolved
    now_ts = time.time() if now_ts is None else now_ts
    beats = codec_health.read_beats()
    if _observer_should_tick(procs):
        obs = codec_health.observer_beat(str(codec_audit._AUDIT_LOG), now_ts)
        if obs is not None:
            beats[_OBSERVER_APP] = obs
    problems = []
    for name, beat in sorted(beats.items()):
        try:
            verdict = codec_health.verdict(beat, now_ts)
            if procs:
                status = ((_pm2_app(procs, name) or {}).get("pm2_env") or {}).get("status")
                if status != "online":
                    verdict = "stopped"
                elif codec_models.pm2_app_loading(procs, name, now_ts):
                    continue
                elif verdict == "stopped":
                    # PM2 runs it again, past its start grace, and this run has not reported yet:
                    # the stop mark is the last run's, so only its age counts (silent or ok).
                    verdict = codec_health.verdict(dict(beat, stopped=False, consecutive_errors=0), now_ts)
            silent, failing = f"silent:{name}", f"failing:{name}"
            if verdict == "stopped":
                for key in (silent, failing):
                    alert_resolved(key, f"CODEC: {name} is stopped, so it is no longer checked.")
            elif verdict == "silent":
                age_min = int((now_ts - float(beat["ts"])) // 60)
                cadence = int(codec_health._num(beat.get("cadence_s")) or codec_health.WRITE_EVERY_S)
                msg = (f"CODEC ALERT: {name} has not reported for {age_min} min "
                       f"(it reports every {cadence} s). It may be stuck: check {_logs_hint(name)}.")
                problems.append(silent)
                alert_once(silent, "critical", msg)
            else:
                alert_resolved(silent, f"CODEC RECOVERED: {name} reports again.")
                if verdict == "failing":
                    msg = (f"CODEC ALERT: {name} is running but failing: its last "
                           f"{int(codec_health._num(beat.get('consecutive_errors')) or 0)} rounds ended in an "
                           f"error ({codec_health.error_class(beat.get('last_error_class')) or 'Error'}). "
                           f"Check {_logs_hint(name)}.")
                    problems.append(failing)
                    alert_once(failing, "warning", msg)
                else:
                    alert_resolved(failing, f"CODEC RECOVERED: {name} works again.")
        except Exception as e:
            log.warning(f"health beat check failed for {name}: {type(e).__name__}")
    return problems


def _last_auto_pull() -> str:
    """'06:00 up_to_date' from the last line of auto_pull.log, or why not."""
    try:
        with open(_AUTO_PULL_LOG) as f:
            last = f.read().strip().splitlines()[-1]
    except Exception:
        return "no run logged"
    stamp, _, rest = last.partition(" ")
    outcome = rest.split("outcome=", 1)[1].split()[0] if "outcome=" in rest else "unknown"
    when = stamp[11:16] if len(stamp) >= 16 else stamp
    if not stamp.startswith(datetime.now().date().isoformat()):
        when = stamp[:10] + " " + when
    return f"{when} {outcome}"


def daily_status_text(procs=None) -> str:
    """One plain line: apps online, last auto-pull, free disk, open problems."""
    import shutil
    from codec_alerts import open_problems
    if procs is None:
        procs = _pm2_jlist()
    apps = [p for p in procs or [] if _is_codec_app(p.get("name", ""))]
    online = sum(1 for p in apps if (p.get("pm2_env") or {}).get("status") == "online")
    free_gb = shutil.disk_usage("/").free / (1024 ** 3)
    labels = []
    for key in open_problems():
        kind, _, what = key.partition(":")
        labels.append(f"{what} errored" if kind == "pm2_errored" else
                      f"{what} down" if kind == "down" else
                      f"{what} {kind}" if kind in ("silent", "failing") else key.replace("_", " "))
    return (f"CODEC daily, {datetime.now().strftime('%d %b')}: {online}/{len(apps)} apps online, "
            f"auto-pull {_last_auto_pull()}, {free_gb:.0f} GB free, "
            + (f"open problems: {', '.join(labels)}." if labels else "no open problems."))


def maybe_send_daily_status(now=None, procs=None) -> bool:
    """Send the daily status once per day, from the first heartbeat after
    07:30. It is also CODEC's proof of life: if it does not arrive, the Mac or
    the heartbeat is down."""
    import codec_alerts
    import codec_jsonstore
    now = now or datetime.now()
    if (now.hour, now.minute) < DAILY_STATUS_AT:
        return False
    today = now.date().isoformat()
    decided = {"send": False}

    def _mutate(state):
        state = state if isinstance(state, dict) else {}
        if state.get("daily_status") != today:
            state["daily_status"] = today
            decided["send"] = True
        return state

    codec_jsonstore.read_modify_write(codec_alerts.ALERTS_SENT_PATH, _mutate)
    if decided["send"]:
        codec_alerts.send_alert("info", daily_status_text(procs))
    return decided["send"]


def extract_task_from_message(content: str) -> str:
    """Extract actionable task from assistant's confirmation message."""
    import re
    patterns = [
        r'logged the task to (.+?)(?:\.|$)',
        r'task.*?to (.+?)(?:\.|$)',
        r'queued (.+?) for',
        r'execute.*?(?:to |: )(.+?)(?:\.|$)',
        r'will (.+?) for you(?:\.|$)',
        r'going to (.+?)(?:\.|$)',
    ]
    for pattern in patterns:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            task = match.group(1).strip()
            if 5 < len(task) < 200:
                return task
    return ""


def _is_dangerous(cmd):
    """Check command against centralized dangerous patterns.

    B2 / SR-19: hard-fails to "block" (returns True) if codec_config is
    unavailable. The previous stale fallback list covered only 11 patterns
    vs. PR-2G's hardened ~50-layer detector — a misconfigured Python path
    would silently use the weaker gate and let bypasses through. Modern
    heartbeat task auto-execution is rare; failing closed is the right
    safety default.
    """
    try:
        import sys as _sys
        _repo = os.path.dirname(os.path.abspath(__file__))
        if _repo not in _sys.path:
            _sys.path.insert(0, _repo)
        from codec_config import is_dangerous
        return is_dangerous(cmd)
    except ImportError:
        import logging as _logging
        _logging.getLogger("codec.heartbeat").critical(
            "codec_config unavailable in heartbeat — refusing all auto-tasks "
            "(fail-safe). Restore codec_config import to re-enable.")
        return True  # fail-CLOSED: refuse to auto-execute anything


def execute_pending_tasks():
    """Find and execute tasks saved during voice/chat conversations.

    Security: Only matches messages with explicit 'CODEC_TASK:' prefix or
    very specific logged-task patterns. All extracted tasks are checked
    against DANGEROUS_PATTERNS before execution.
    """
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5.0); conn.execute("PRAGMA busy_timeout=5000")
        cutoff = (datetime.now() - timedelta(hours=24)).isoformat()

        # Tightened patterns: require explicit prefix or very specific phrasing
        rows = conn.execute("""
            SELECT id, content, timestamp FROM conversations
            WHERE timestamp > ?
            AND role = 'assistant'
            AND (
                content LIKE '%CODEC_TASK:%'
                OR content LIKE '%logged the task to %'
                OR content LIKE '%queued for execution:%'
            )
            ORDER BY id DESC LIMIT 5
        """, (cutoff,)).fetchall()
        conn.close()

        if not rows:
            return

        executed_path = os.path.expanduser("~/.codec/executed_tasks.json")
        try:
            with open(executed_path) as f:
                executed = json.load(f)
        except Exception:
            executed = []

        for row_id, content, ts in rows:
            if row_id in executed:
                continue

            task = extract_task_from_message(content)
            if not task:
                continue

            # ── Safety gate: check extracted task against dangerous patterns ──
            if _is_dangerous(task):
                log.warning(f"  🛑 BLOCKED dangerous auto-task ({len(task)} characters)")
                executed.append(row_id)  # mark as handled so we don't retry
                continue

            log.info(f"  🚀 Auto-executing a task ({len(task)} characters)")
            try:
                r = requests.post(
                    "http://localhost:8090/api/command",
                    json={"command": task, "source": "heartbeat"},
                    timeout=60,
                )
                if r.status_code == 200:
                    log.info("  ✅ Task queued successfully")
                    executed.append(row_id)
                elif r.status_code == 403:
                    log.warning("  🛑 Task blocked by /api/command safety check")
                    executed.append(row_id)
                else:
                    log.warning(f"  ⚠️ /api/command returned {r.status_code}")
            except Exception as e:
                log.error(f"  ❌ Task execution failed: {e}")

        with open(executed_path, "w") as f:
            json.dump(executed[-100:], f)

    except Exception as e:
        log.error(f"execute_pending_tasks error: {e}")


_last_cleanup = None


# ── Configurable Alerts ──────────────────────────────────────────────────
def check_alerts():
    """Run user-configured alerts from config.json heartbeat_alerts list.
    Supported types:
      - price: Crypto via CoinGecko (asset, threshold_pct, direction)
      - email_check: Unread email count via local AppleScript (macOS Mail)
      - disk_usage: Disk space warning when usage exceeds threshold_pct
    """
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
    except Exception:
        return
    alerts = cfg.get("heartbeat_alerts", [])
    if not alerts:
        return
    try:  # P-083: the very file (and lock) codec_alerts writes, so the two merge, never race
        import codec_alerts
        alert_state_path = codec_alerts.ALERT_STATE_PATH
    except ImportError:
        alert_state_path = os.path.expanduser("~/.codec/alert_state.json")
    try:
        with open(alert_state_path) as f:
            state = json.load(f)
    except Exception:
        state = {}
    if not isinstance(state, dict):
        state = {}
    base = copy.deepcopy(state)  # P-083: what this run loaded, for the merge at the end
    triggered = []
    for alert in alerts:
        # Skip disabled alerts
        if alert.get("enabled") is False:
            continue
        atype = alert.get("type", "")
        name = alert.get("name", "Unknown")
        if atype == "price":
            asset = alert.get("asset", "bitcoin")
            threshold_pct = alert.get("threshold_pct", 5)
            direction = alert.get("direction", "any")  # up, down, any
            try:
                r = requests.get(f"https://api.coingecko.com/api/v3/simple/price?ids={asset}&vs_currencies=usd", timeout=10)
                price = r.json().get(asset, {}).get("usd")
                if price is None:
                    continue
                last_price = state.get(f"price_{asset}")
                if last_price:
                    change_pct = ((price - last_price) / last_price) * 100
                    if abs(change_pct) >= threshold_pct:
                        if direction == "any" or (direction == "up" and change_pct > 0) or (direction == "down" and change_pct < 0):
                            msg = f"{name}: ${price:,.2f} ({change_pct:+.1f}% since last check)"
                            log.info(f"  🚨 ALERT: {msg}")
                            triggered.append(msg)
                            state[f"price_{asset}"] = price  # reset baseline
                    else:
                        log.info(f"  {name}: ${price:,.2f} ({change_pct:+.1f}%) — within threshold")
                else:
                    state[f"price_{asset}"] = price
                    log.info(f"  {name}: ${price:,.2f} (baseline set)")
            except Exception as e:
                log.warning(f"  Alert '{name}' failed: {e}")

        elif atype == "email_check":
            try:
                # P-098: through codec_dispatch (switch, load gate, hooks, audit).
                from codec_dispatch import run_feature
                result = run_feature("google_gmail", "check unread emails", "heartbeat")
                if result is None:
                    log.info(f"  {name}: skipped (google_gmail is switched off or missing)")
                    continue
                # Count emails from "Found N emails:" pattern
                import re as _re
                count_match = _re.search(r'Found (\d+) emails?:', str(result))
                count = int(count_match.group(1)) if count_match else 0
                last_count = state.get("email_unread", 0)
                if count > 0 and count != last_count:
                    # Extract first sender/subject
                    preview = ""
                    first_line = _re.search(r'\* (.+?)(?:\n|$)', str(result))
                    if first_line:
                        preview = f" — {first_line.group(1)[:60]}"
                    msg = f"{name}: {count} unread email{'s' if count != 1 else ''}{preview}"
                    log.info(f"  {name}: {count} unread email{'s' if count != 1 else ''}")  # P-032: no preview
                    triggered.append(msg)
                elif count == 0:
                    log.info(f"  {name}: Inbox clear")
                state["email_unread"] = count
            except Exception as e:
                log.warning(f"  Alert '{name}' failed: {e}")

        elif atype == "disk_usage":
            try:
                import shutil
                usage = shutil.disk_usage("/")
                used_pct = (usage.used / usage.total) * 100
                threshold = alert.get("threshold_pct", 90)
                free_gb = usage.free / (1024**3)
                if used_pct >= threshold:
                    msg = f"{name}: {used_pct:.0f}% used — only {free_gb:.1f} GB free"
                    log.info(f"  🚨 ALERT: {msg}")
                    triggered.append(msg)
                else:
                    log.info(f"  {name}: {used_pct:.0f}% used, {free_gb:.1f} GB free — OK")
            except Exception as e:
                log.warning(f"  Alert '{name}' failed: {e}")

    # Save state. P-083: under the file's lock, and only the keys this run changed, so the
    # consecutive-failure counters codec_alerts wrote meanwhile are kept.
    try:
        import codec_jsonstore
        codec_jsonstore.read_modify_write(
            alert_state_path, lambda current: codec_jsonstore.merge_changes(current, base, state))
    except Exception:
        pass
    # Send triggered alerts: save to notifications.json + macOS notification
    if triggered:
        import uuid as _uuid
        notif_path = os.path.expanduser("~/.codec/notifications.json")
        for msg in triggered:
            # macOS notification (visible immediately)
            try:
                import codec_applescript
                subprocess.run(["osascript", "-e", codec_applescript.notification_script(
                    msg[:120], "CODEC Alert", sound="Glass")],
                    capture_output=True, timeout=5)
            except Exception:
                pass
            # Save to notifications.json for dashboard bell icon. Fix #9 Phase 2:
            # hold the cross-process file_lock across the load→insert→write so
            # this daemon can't clobber a concurrent dashboard / scheduler write.
            try:
                import codec_jsonstore
                with codec_jsonstore.file_lock(notif_path):
                    try:
                        with open(notif_path) as f:
                            notifs = json.load(f)
                    except (FileNotFoundError, json.JSONDecodeError):
                        notifs = []
                    notifs.insert(0, {
                        "id": f"notif_{_uuid.uuid4().hex[:10]}",
                        "type": "task_report",
                        "title": "Heartbeat Alert",
                        "body": msg,
                        "status": "warning",
                        "created": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                        "read": False,
                        "schedule_id": "heartbeat",
                    })
                    codec_jsonstore.atomic_write_json(notif_path, notifs)
            except Exception:
                pass
    return triggered


# P-082: ~/.codec/overlay_events.jsonl is append-only and the Swift overlay re-reads the
# whole file every 0.15 s. Once nothing has written to it for OVERLAY_QUIET_S, keep its
# last OVERLAY_KEEP_LINES lines (atomic replace; the poller resets its offset when the
# line count drops). The path is the writer's own, codec_overlays._EVENTS.
# docs/ATOMIC-STATE-FILES-DESIGN.md
OVERLAY_KEEP_LINES = 100
OVERLAY_QUIET_S = 60
_OVERLAY_TAIL_BYTES = 1 << 20  # read at most the last 1 MB, whatever the file's size


def trim_overlay_events(path=None, now_ts=None) -> int:
    """Cut the overlay events file to its last OVERLAY_KEEP_LINES lines.

    Only when the file has not changed for OVERLAY_QUIET_S and holds more lines than
    that. The new file is a temp file (0600) in the same folder put in place with
    os.replace; no folder is created or chmodded. Returns the bytes removed, 0 when
    nothing was done. Never raises."""
    import tempfile
    tmp = None
    try:
        if path is None:
            import codec_overlays
            path = codec_overlays._EVENTS
        st = os.stat(path)
        now_ts = time.time() if now_ts is None else now_ts
        if now_ts - st.st_mtime < OVERLAY_QUIET_S:
            return 0
        start = max(0, st.st_size - _OVERLAY_TAIL_BYTES)
        with open(path, "rb") as f:
            f.seek(start)
            parts = f.read(st.st_size - start).split(b"\n")
        if start:
            parts = parts[1:]  # the first line read may start mid-line
        lines = [p for p in parts if p.strip()]
        if not start and len(lines) <= OVERLAY_KEEP_LINES:
            return 0
        kept = b"\n".join(lines[-OVERLAY_KEEP_LINES:]) + b"\n" if lines else b""
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".",
                                   prefix=".overlay_events.", suffix=".tmp")
        with os.fdopen(fd, "wb") as f:
            f.write(kept)
            f.flush()
            os.fsync(f.fileno())
        again = os.stat(path)
        if (again.st_size, again.st_mtime_ns) != (st.st_size, st.st_mtime_ns):
            return 0  # a writer appended meanwhile; the next heartbeat tries again
        os.replace(tmp, path)
        tmp = None
        removed = st.st_size - len(kept)
        log.info(f"Overlay events trimmed to the last {min(len(lines), OVERLAY_KEEP_LINES)} "
                 f"lines ({removed} bytes removed)")
        return removed
    except FileNotFoundError:
        return 0
    except Exception as e:
        log.warning(f"Overlay events trim failed: {e}")
        return 0
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def trim_service_logs(folder=None) -> list:
    """P-032: launchd never rotates the services' logs (~/Library/Logs/CODEC/<name>.out|.err), so on
    launchd each log file over 10 MB keeps its last 5 MB (codec_log_trim). PM2 keeps its own logs:
    there this does nothing unless `folder` is given. Returns [(name, before, after)]. Never raises."""
    try:
        if folder is None:
            if _supervisor() != "launchd":
                return []
            import codec_supervisor
            folder = codec_supervisor.LOG_DIR
        import codec_log_trim
        done = codec_log_trim.trim_folder(folder)
        for name, before, after in done:
            log.info(f"Log trimmed: {name} {before} -> {after} bytes")
        return done
    except Exception as e:
        log.warning(f"Log trim failed: {type(e).__name__}")
        return []


# ── Memory pressure (P-083, docs/LOOP-OWNER-DESIGN.md) ───────────────────────
# The model server's memory is GPU-backed: PM2's RSS shows ~60 MB while it holds 20 GB, and it grows
# when it is not restarted. `footprint` (phys_footprint) is the honest figure; swap is the Mac's.
MEMORY_STATE_PATH = os.path.expanduser("~/.codec/memory_check.json")
MEM_SWAP_PCT = 80.0          # swap used, percent of the swap total (config memory_alert.swap_pct)
MEM_FOOTPRINT_RATIO = 1.5    # model server footprint / model size (config memory_alert.footprint_ratio)
MEM_HIGH_TICKS = 2           # high ticks in a row before mem_pressure alerts
_MEM_CMD_TIMEOUT_S = 10
_SYSCTL, _MEMORY_PRESSURE, _FOOTPRINT = "/usr/sbin/sysctl", "/usr/bin/memory_pressure", "/usr/bin/footprint"
_UNIT = {"": 1, "B": 1, "K": 1024, "KB": 1024, "M": 1024 ** 2, "MB": 1024 ** 2,
         "G": 1024 ** 3, "GB": 1024 ** 3, "T": 1024 ** 4, "TB": 1024 ** 4}


def _mem_cmd(args) -> "str | None":
    """stdout of a read-only memory tool, or None (missing tool, error exit, timeout)."""
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=_MEM_CMD_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return r.stdout if r.returncode == 0 else None


def parse_swapusage(text) -> "tuple | None":
    """(used_mb, total_mb) from `sysctl vm.swapusage`, or None."""
    m = re.search(r"total\s*=\s*([\d.]+)\s*([KMGT]?)\b.*?used\s*=\s*([\d.]+)\s*([KMGT]?)\b", text or "")
    if not m:
        return None
    mb = _UNIT["M"]
    try:
        return float(m.group(3)) * _UNIT[m.group(4)] / mb, float(m.group(1)) * _UNIT[m.group(2)] / mb
    except ValueError:
        return None


def parse_memory_pressure(text) -> "int | None":
    """System-wide free percentage from `memory_pressure -Q`, or None."""
    m = re.search(r"free percentage:\s*(\d+)\s*%", text or "")
    return int(m.group(1)) if m else None


def parse_footprint(text) -> "int | None":
    """phys_footprint in bytes from `footprint -p <pid>` (bytes or formatted units), or None."""
    m = re.search(r"phys_footprint:\s*([\d.]+)\s*([KMGT]?B)\b", text or "")
    if not m:
        return None
    try:
        return int(float(m.group(1)) * _UNIT[m.group(2)])
    except ValueError:
        return None


def _model_server_pid(procs, cfg) -> "int | None":
    """pid of the online PM2 model server app (config model_pm2_process, default qwen3.6)."""
    try:
        import codec_models
        app = _pm2_app(procs, codec_models._pm2_process_name(cfg)) or {}
    except Exception:
        return None
    pid = app.get("pid")
    if (app.get("pm2_env") or {}).get("status") != "online" or not isinstance(pid, int) or pid <= 0:
        return None
    return pid


def _model_bytes(cfg) -> "int | None":
    """Size of the model the local server holds: extra_models size_gb, else its weights on disk."""
    try:
        import codec_models
        model = codec_models.local_server_model(cfg)[1]
        for em in cfg.get("extra_models") or []:
            if isinstance(em, dict) and em.get("id") == model and isinstance(em.get("size_gb"), (int, float)):
                return int(em["size_gb"] * 1e9) or None
        if os.path.isabs(model):
            total = sum(os.path.getsize(os.path.join(model, f)) for f in os.listdir(model)
                        if f.endswith(".safetensors"))
        else:
            total = codec_models._snapshot_bytes(
                os.path.join(codec_models.HF_CACHE, "models--" + model.replace("/", "--")))
        return int(total) or None
    except Exception:
        return None


def _memory_limits(cfg) -> tuple:
    raw = cfg.get("memory_alert") if isinstance(cfg.get("memory_alert"), dict) else {}

    def num(key, default, lo, hi):
        v = raw.get(key, default)
        return min(max(float(v), lo), hi) if isinstance(v, (int, float)) and not isinstance(v, bool) else default
    return num("swap_pct", MEM_SWAP_PCT, 50.0, 100.0), num("footprint_ratio", MEM_FOOTPRINT_RATIO, 1.1, 5.0)


def _memory_advice(reading, app) -> str:
    """What to do about a mem_pressure alert. A restart of the model server gives memory back only
    when the server has grown past its model (footprint high); at its normal size it frees about the
    gap between footprint and model, so quitting other apps helps more."""
    free = reading.get("free_pct")
    free_txt = f" {free}% of memory is free." if isinstance(free, int) else ""
    if reading.get("model_footprint") == "high":
        return f"Restarting the model server (pm2 restart {app}) gives its memory back.{free_txt}"
    if reading.get("model_footprint") == "ok":
        return (f"The model server is at its normal size ({reading['footprint_ratio']:.2f}x its model), "
                f"so restarting it gives back little; quitting apps you do not use helps more.{free_txt}")
    return (f"Quitting apps you do not use helps; restarting the model server (pm2 restart {app}) "
            f"helps only if it has grown.{free_txt}")


def memory_check(procs=None, now_ts=None) -> dict:
    """Read swap, memory pressure and the model server's footprint; alert `mem_pressure` once after
    MEM_HIGH_TICKS ticks in a row with swap above 80% or the footprint above 1.5x the model, and one
    recovery message when a tick is fine again. A figure that cannot be read is 'unknown' and never
    alerts. The reading goes to MEMORY_STATE_PATH, which GET /api/status shows."""
    import codec_jsonstore
    from codec_alerts import alert_once, alert_resolved
    now_ts = time.time() if now_ts is None else now_ts
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        cfg = cfg if isinstance(cfg, dict) else {}
    except Exception:
        cfg = {}
    if procs is None:
        procs = _pm2_jlist()
    swap_limit, ratio_limit = _memory_limits(cfg)
    reading = {"checked_at": datetime.fromtimestamp(now_ts).isoformat(timespec="seconds"),
               "swap": "unknown", "swap_pct": None, "swap_used_mb": None, "swap_total_mb": None,
               "free_pct": parse_memory_pressure(_mem_cmd([_MEMORY_PRESSURE, "-Q"])),
               "model_footprint": "unknown", "model_footprint_gb": None, "model_gb": None,
               "footprint_ratio": None}
    swap = parse_swapusage(_mem_cmd([_SYSCTL, "vm.swapusage"]))
    if swap is not None:
        used, total = swap
        pct = round(used * 100.0 / total, 1) if total > 0 else 0.0
        reading.update(swap="high" if pct > swap_limit else "ok", swap_pct=pct,
                       swap_used_mb=round(used, 1), swap_total_mb=round(total, 1))
    pid = _model_server_pid(procs, cfg)
    used_bytes = parse_footprint(_mem_cmd([_FOOTPRINT, "-p", str(pid), "-f", "bytes", "--noCategories"])) \
        if pid else None
    size = _model_bytes(cfg) if used_bytes else None
    if used_bytes:
        reading["model_footprint_gb"] = round(used_bytes / 1e9, 1)
    if used_bytes and size:
        ratio = round(used_bytes / size, 2)
        reading.update(model_footprint="high" if ratio > ratio_limit else "ok", footprint_ratio=ratio,
                       model_gb=round(size / 1e9, 1))
    figures = (reading["swap"], reading["model_footprint"])
    reading["level"] = "high" if "high" in figures else "ok" if "ok" in figures else "unknown"

    def _mutate(state):
        state = state if isinstance(state, dict) else {}
        ticks = state.get("high_ticks")
        ticks = ticks if isinstance(ticks, int) and not isinstance(ticks, bool) and ticks > 0 else 0
        if reading["level"] == "high":
            ticks += 1
        elif reading["level"] == "ok":
            ticks = 0
        return dict(reading, high_ticks=ticks)

    try:
        reading = codec_jsonstore.read_modify_write(MEMORY_STATE_PATH, _mutate)
    except Exception as e:
        log.warning(f"Memory check state not saved: {type(e).__name__}")
        reading = _mutate({})
    parts = []
    if reading["swap"] == "high":
        parts.append(f"swap {reading['swap_pct']:.0f}% used ({reading['swap_used_mb'] / 1024:.1f} of "
                     f"{reading['swap_total_mb'] / 1024:.1f} GB)")
    if reading["model_footprint"] == "high":
        parts.append(f"the model server holds {reading['model_footprint_gb']:.1f} GB, "
                     f"{reading['footprint_ratio']:.1f}x its {reading['model_gb']:.1f} GB model")
    log.info(f"Memory: level {reading['level']}, swap {reading['swap_pct']}%, "
             f"model footprint {reading['model_footprint_gb']} GB, free {reading['free_pct']}%")
    if reading["level"] == "high" and reading["high_ticks"] >= MEM_HIGH_TICKS:
        try:
            import codec_models
            app = codec_models._pm2_process_name(cfg)
        except Exception:
            app = "qwen3.6"
        alert_once("mem_pressure", "warning",
                   f"CODEC ALERT: memory is tight on this Mac: {' and '.join(parts)}. Chat may slow down. "
                   + _memory_advice(reading, app))
    elif reading["level"] == "ok":
        alert_resolved("mem_pressure", "CODEC RECOVERED: memory is back to normal.")
    return reading


def memory_status() -> dict:
    """The last memory_check reading for GET /api/status; level unknown before the first one."""
    try:
        with open(MEMORY_STATE_PATH) as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("level"):
            return data
    except Exception:
        pass
    return {"level": "unknown", "checked_at": None}


def _heartbeat_cycle():
    """Run one heartbeat cycle (heartbeat() decides whether this process may)."""
    global _last_cleanup
    log.info("═══ CODEC Heartbeat ═══")
    procs = _pm2_jlist()  # one `pm2 jlist` for the PM2 checks and the start grace (P-059)
    check_system_health(procs)
    check_pm2_restart_storms(procs)
    for check in (lambda: check_errored_codec_apps(procs), lambda: check_health_beats(procs),  # P-017
                  lambda: memory_check(procs),  # P-083
                  lambda: maybe_send_daily_status(procs=procs)):
        try:
            check()
        except Exception as e:
            log.warning(f"Owner alert check failed: {e}")
    # Run alert-based service monitoring (Telegram/Email/Slack)
    try:
        from codec_alerts import check_services_and_alert
        check_services_and_alert(procs)
    except ImportError:
        pass
    except Exception as e:
        log.warning(f"Alert check failed: {e}")
    check_memory_stats()
    tasks = check_pending_tasks()
    if tasks:
        execute_pending_tasks()
    # Configurable alerts (BTC price, etc.)
    check_alerts()
    trim_overlay_events()  # P-082: keep the Swift overlay's events file short
    trim_service_logs()  # P-032: launchd's service logs at most 10 MB (nothing on PM2)
    # P3.5: the proactive check-in (off unless switched on in Settings; docs/P3.5-DESIGN.md)
    try:
        import codec_checkin
        log.info(f"Check-in: {codec_checkin.run_checkin()}")
    except Exception as e:
        log.warning(f"Check-in failed: {e}")
    # Daily memory backup + cleanup
    backup_memory_db()
    now = datetime.now()
    if _last_cleanup is None or (now - _last_cleanup).days >= 1:
        # Stated retention (config.json:retention, Settings > Memory; D-38 defaults chats 90,
        # clipboard 30, webcam photos 30 days). Counts land in backup status.
        try:
            import codec_backup
            res = codec_backup.run_retention()
            _last_cleanup = now
            log.info("Daily retention: " + ", ".join(
                f"{k} {v.get('deleted', 0)} deleted" + (f" ({v.get('skipped') or v.get('error')})"
                                                         if v.get('skipped') or v.get('error') else "")
                for k, v in res.items()))
        except Exception as e:
            log.warning(f"Retention clean-up failed: {e}")
    log.info("═══ Heartbeat complete ═══")
    log_event("heartbeat_tick", "codec-heartbeat",
              "Heartbeat tick completed",
              extra={"tasks_run": len(tasks) if hasattr(tasks, '__len__') else None})
    return tasks


# ── One heartbeat at a time on this Mac (P-083, docs/LOOP-OWNER-DESIGN.md) ────
# codec-dashboard runs the heartbeat every 30 min (the owner). A hand-added PM2 app codec-heartbeat
# ran it again every 20 min, so probes, restarts, the mail check and the alert counters ran twice.
HEARTBEAT_LOCK_PATH = os.path.expanduser("~/.codec/heartbeat.lock")
HEARTBEAT_STAMP_PATH = os.path.expanduser("~/.codec/heartbeat_last.json")
HEARTBEAT_INTERVAL_S = 30 * 60  # codec-dashboard's loop; a run within half of it is skipped


def _stamp_age(now_ts) -> "float | None":
    """Seconds since the last heartbeat started, or None (no stamp, a broken one, or one in the future)."""
    try:
        with open(HEARTBEAT_STAMP_PATH) as f:
            started = float(json.load(f)["started_at"])
    except Exception:
        return None
    age = now_ts - started
    return age if age >= 0 else None


def heartbeat(*, min_gap_s=None):
    """Run one heartbeat cycle, unless another one runs now (HEARTBEAT_LOCK_PATH is held, by any
    process or thread) or one started less than `min_gap_s` ago (default half HEARTBEAT_INTERVAL_S,
    by HEARTBEAT_STAMP_PATH). A skipped call returns None at once; a run returns the cycle's tasks.
    If the lock file cannot be opened the cycle runs anyway: a heartbeat is never lost to it."""
    gap = HEARTBEAT_INTERVAL_S / 2 if min_gap_s is None else min_gap_s
    fd = None
    try:
        os.makedirs(os.path.dirname(HEARTBEAT_LOCK_PATH) or ".", exist_ok=True)
        fd = os.open(HEARTBEAT_LOCK_PATH, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as e:
        log.warning(f"Heartbeat lock unavailable ({type(e).__name__}); running without it")
    try:
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                log.info("Heartbeat skipped: another heartbeat is running on this Mac")
                return None
            except OSError as e:  # a folder that cannot lock: run, as before this guard
                log.warning(f"Heartbeat lock not taken ({type(e).__name__}); running without it")
        now_ts = time.time()
        age = _stamp_age(now_ts)
        if gap > 0 and age is not None and age < gap:
            log.info(f"Heartbeat skipped: the last one started {int(age // 60)} min ago")
            return None
        try:
            import codec_jsonstore
            codec_jsonstore.atomic_write_json(HEARTBEAT_STAMP_PATH,
                                              {"started_at": now_ts, "pid": os.getpid()})
        except Exception as e:
            log.warning(f"Heartbeat stamp not written: {type(e).__name__}")
        return _heartbeat_cycle()
    finally:
        if fd is not None:
            os.close(fd)  # closing the descriptor releases the lock


# P-059: after a boot or deploy the first check waits this long, so services
# that start with the Mac are not probed (and restarted) while they load.
# Used by this daemon and by codec-dashboard's _bg_heartbeat.
FIRST_TICK_DELAY_S = 120


def run_daemon(interval_minutes=20):
    """Run heartbeat every N minutes, the first FIRST_TICK_DELAY_S after start."""
    log.info(f"Heartbeat daemon starting (every {interval_minutes}min)")
    time.sleep(FIRST_TICK_DELAY_S)
    while True:
        heartbeat()
        time.sleep(interval_minutes * 60)

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        heartbeat(min_gap_s=0)  # by hand: still never next to a running one
    else:
        run_daemon(20)
