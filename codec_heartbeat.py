"""CODEC Heartbeat — periodic check of logs, memory, and pending tasks"""
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
                log.info(f"  [{ts[:16]}] {content[:100]}")
        return rows
    except Exception as e:
        log.error(f"Task check failed: {e}")
        return []

def _check_one_service(name: str, url: str) -> tuple:
    """Check a single service endpoint. Returns (name, status_string)."""
    try:
        r = requests.get(url, timeout=5)
        status = "✅" if r.status_code in (200, 404, 405) else f"⚠️ {r.status_code}"
    except Exception:
        status = "❌ DOWN"
        log_event("service_down", "codec-heartbeat",
                  f"Service down: {name}",
                  outcome="error", level="error",
                  extra={"service": name, "url": url})
    return name, status


def check_system_health():
    """Verify all CODEC services are running (checks run in parallel).

    Only HTTP-exposing services are probed here. PM2-supervised daemons
    (codec-observer, codec-agent-runner) rely on PM2's autorestart for
    crash recovery — see AGENTS.md §3 "Background Execution".
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
    with ThreadPoolExecutor(max_workers=len(services)) as pool:
        futures = {
            pool.submit(_check_one_service, name, url): name
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
        size_warn = " ⚠️ LARGE" if db_size_mb > 100 else ""
        log.info(f"Memory: {total} entries, {sessions} sessions, {db_size_mb:.1f} MB{size_warn}, latest: {latest[0][:16] if latest else 'none'}")

        if db_size_mb > 100:
            try:
                from codec_keychain import get_internal_token
                _token = get_internal_token() or ""
                requests.post("http://localhost:8090/api/notifications",
                              json={"message": f"💾 Memory DB is {db_size_mb:.0f} MB — consider running cleanup", "type": "warning", "source": "heartbeat"},
                              headers={"x-internal-token": _token},
                              timeout=5)
            except Exception:
                pass
    except Exception as e:
        log.error(f"Memory stats failed: {e}")


def _backup_one_db(prefix: str, db_path: str, backup_dir: str, today: str):
    """Daily SQLite backup (safe under WAL via the backup API), keep last 7."""
    backup_path = os.path.join(backup_dir, f"{prefix}_{today}.db")
    if os.path.exists(backup_path) or not os.path.exists(db_path):
        return
    try:
        src = sqlite3.connect(db_path, timeout=5.0)
        src.execute("PRAGMA busy_timeout=5000")
        dst = sqlite3.connect(backup_path, timeout=5.0)
        dst.execute("PRAGMA busy_timeout=5000")
        src.backup(dst)
        dst.close()
        src.close()

        # Keep only last 7 backups per prefix
        backups = sorted([f for f in os.listdir(backup_dir)
                          if f.startswith(f"{prefix}_") and f.endswith(".db")])
        for old in backups[:-7]:
            try:
                os.unlink(os.path.join(backup_dir, old))
            except Exception:
                pass

        size_mb = os.path.getsize(backup_path) / (1024 * 1024)
        log.info(f"{prefix} backup: {backup_path} ({size_mb:.1f} MB)")
    except Exception as e:
        log.warning(f"{prefix} backup failed: {e}")


def _backup_agents_state(backup_dir: str, today: str):
    """Daily tar.gz of ~/.codec/agents/ (plans, grants, state, messages),
    keep last 7. Small (KBs) but irreplaceable after an agent run."""
    agents_dir = os.path.expanduser("~/.codec/agents")
    tar_path = os.path.join(backup_dir, f"agents_{today}.tar.gz")
    if os.path.exists(tar_path) or not os.path.isdir(agents_dir):
        return
    try:
        import tarfile
        with tarfile.open(tar_path, "w:gz") as tf:
            tf.add(agents_dir, arcname="agents")
        tars = sorted([f for f in os.listdir(backup_dir)
                       if f.startswith("agents_") and f.endswith(".tar.gz")])
        for old in tars[:-7]:
            try:
                os.unlink(os.path.join(backup_dir, old))
            except Exception:
                pass
        log.info(f"agents backup: {tar_path}")
    except Exception as e:
        log.warning(f"agents backup failed: {e}")


def backup_memory_db():
    """Daily backups to ~/.codec/backups/.

    2026-07 log review: only memory.db was covered, but day-to-day chat
    history actually lives in qchat.db (and Vibe IDE history in vibe.db) —
    neither was backed up. Now all three SQLite stores + the agents/
    runtime state get a dated backup, 7-day retention each.
    """
    backup_dir = os.path.expanduser("~/.codec/backups")
    os.makedirs(backup_dir, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")

    _backup_one_db("memory", DB_PATH, backup_dir, today)
    _backup_one_db("qchat", os.path.expanduser("~/.codec/qchat.db"), backup_dir, today)
    _backup_one_db("vibe", os.path.expanduser("~/.codec/vibe.db"), backup_dir, today)
    _backup_agents_state(backup_dir, today)

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


def _pm2_jlist() -> list:
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
                msg = (f"🔁 PM2 restart storm: '{name}' restarted {delta}× since "
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
        counts[name] = n

    try:
        os.makedirs(os.path.dirname(_RESTART_STATE_PATH), exist_ok=True)
        with open(_RESTART_STATE_PATH, "w") as f:
            json.dump({"counts": counts, "last_alert": last_alert}, f)
    except Exception:
        pass
    return storms


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
                log.warning(f"  🛑 BLOCKED dangerous auto-task: {task[:80]}")
                executed.append(row_id)  # mark as handled so we don't retry
                continue

            log.info(f"  🚀 Auto-executing: {task[:80]}")
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
    alert_state_path = os.path.expanduser("~/.codec/alert_state.json")
    try:
        with open(alert_state_path) as f:
            state = json.load(f)
    except Exception:
        state = {}
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
                            arrow = "📈" if change_pct > 0 else "📉"
                            msg = f"{arrow} {name}: ${price:,.2f} ({change_pct:+.1f}% since last check)"
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
                import importlib.util
                _gmail_path = os.path.join(os.path.dirname(__file__), "skills", "google_gmail.py")
                _spec = importlib.util.spec_from_file_location("google_gmail", _gmail_path)
                _gmail = importlib.util.module_from_spec(_spec)
                _spec.loader.exec_module(_gmail)
                result = _gmail.run("check unread emails")
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
                    msg = f"📧 {name}: {count} unread email{'s' if count != 1 else ''}{preview}"
                    log.info(f"  {msg}")
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
                    msg = f"💾 {name}: {used_pct:.0f}% used — only {free_gb:.1f} GB free"
                    log.info(f"  🚨 ALERT: {msg}")
                    triggered.append(msg)
                else:
                    log.info(f"  {name}: {used_pct:.0f}% used, {free_gb:.1f} GB free — OK")
            except Exception as e:
                log.warning(f"  Alert '{name}' failed: {e}")

    # Save state
    try:
        os.makedirs(os.path.dirname(alert_state_path), exist_ok=True)
        with open(alert_state_path, "w") as f:
            json.dump(state, f)
    except Exception:
        pass
    # Send triggered alerts: save to notifications.json + macOS notification
    if triggered:
        import uuid as _uuid
        notif_path = os.path.expanduser("~/.codec/notifications.json")
        for msg in triggered:
            # macOS notification (visible immediately)
            try:
                subprocess.run(["osascript", "-e",
                    f'display notification "{msg[:120]}" with title "CODEC Alert" sound name "Glass"'],
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


def heartbeat():
    """Run one heartbeat cycle."""
    global _last_cleanup
    log.info("═══ CODEC Heartbeat ═══")
    check_system_health()
    check_pm2_restart_storms()
    # Run alert-based service monitoring (Telegram/Email/Slack)
    try:
        from codec_alerts import check_services_and_alert
        check_services_and_alert()
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
    # Daily memory backup + cleanup
    backup_memory_db()
    now = datetime.now()
    if _last_cleanup is None or (now - _last_cleanup).days >= 1:
        try:
            from codec_memory import CodecMemory
            mem = CodecMemory()
            mem.cleanup()
            _last_cleanup = now
            log.info("Daily memory cleanup complete (90-day retention)")
        except Exception as e:
            log.warning(f"Memory cleanup failed: {e}")
    log.info("═══ Heartbeat complete ═══")
    log_event("heartbeat_tick", "codec-heartbeat",
              "Heartbeat tick completed",
              extra={"tasks_run": len(tasks) if hasattr(tasks, '__len__') else None})
    return tasks

def run_daemon(interval_minutes=20):
    """Run heartbeat every N minutes."""
    log.info(f"Heartbeat daemon starting (every {interval_minutes}min)")
    while True:
        heartbeat()
        time.sleep(interval_minutes * 60)

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        heartbeat()
    else:
        run_daemon(20)
