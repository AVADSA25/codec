"""CODEC Heartbeat — periodic check of logs, memory, and pending tasks"""
import time, sqlite3, os, json, logging, requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] [HEARTBEAT] %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger('heartbeat')

DB_PATH = os.path.expanduser("~/.q_memory.db")
CONFIG_PATH = os.path.expanduser("~/.codec/config.json")

def check_pending_tasks():
    """Check memory for tasks that were saved for later"""
    try:
        conn = sqlite3.connect(DB_PATH)
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
        r = requests.get(url, timeout=3)
        status = "✅" if r.status_code in (200, 404, 405) else f"⚠️ {r.status_code}"
    except Exception as e:
        status = "❌ DOWN"
    return name, status


def check_system_health():
    """Verify all CODEC services are running (checks run in parallel)."""
    services = {
        "LLM": "http://localhost:8081/v1/models",
        "Whisper": "http://localhost:8084/",
        "Kokoro": "http://localhost:8085/v1/models",
        "Dashboard": "http://localhost:8090/",
        "Vision": "http://localhost:8082/v1/models",
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
    """Report memory database stats"""
    try:
        conn = sqlite3.connect(DB_PATH)
        total = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        sessions = conn.execute("SELECT COUNT(DISTINCT session_id) FROM conversations").fetchone()[0]
        latest = conn.execute("SELECT timestamp FROM conversations ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        log.info(f"Memory: {total} entries, {sessions} sessions, latest: {latest[0][:16] if latest else 'none'}")
    except Exception as e:
        log.error(f"Memory stats failed: {e}")

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


def execute_pending_tasks():
    """Find and execute tasks saved during voice/chat conversations."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cutoff = (datetime.now() - timedelta(hours=24)).isoformat()

        rows = conn.execute("""
            SELECT id, content, timestamp FROM conversations
            WHERE timestamp > ?
            AND role = 'assistant'
            AND (
                content LIKE '%logged the task%'
                OR content LIKE '%saved%task%'
                OR content LIKE '%for CODEC to execute%'
                OR content LIKE '%queued%'
                OR content LIKE '%will do that for you%'
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

            log.info(f"  🚀 Auto-executing: {task[:80]}")
            try:
                r = requests.post(
                    "http://localhost:8090/api/command",
                    json={"command": task, "source": "heartbeat"},
                    timeout=60,
                )
                if r.status_code == 200:
                    log.info(f"  ✅ Task queued successfully")
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
                import subprocess
                script = 'tell application "Mail" to get the unread count of inbox'
                result = subprocess.run(
                    ["osascript", "-e", script],
                    capture_output=True, text=True, timeout=10
                )
                count = int(result.stdout.strip()) if result.stdout.strip().isdigit() else 0
                last_count = state.get("email_unread", 0)
                if count > 0 and count != last_count:
                    msg = f"📧 {name}: {count} unread email{'s' if count != 1 else ''}"
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
    # Send triggered alerts to dashboard notification
    if triggered:
        for msg in triggered:
            try:
                requests.post("http://localhost:8090/api/notifications",
                              json={"message": msg, "type": "alert", "source": "heartbeat"},
                              timeout=5)
            except Exception:
                pass
    return triggered


def heartbeat():
    """Run one heartbeat cycle."""
    global _last_cleanup
    log.info("═══ CODEC Heartbeat ═══")
    check_system_health()
    check_memory_stats()
    tasks = check_pending_tasks()
    if tasks:
        execute_pending_tasks()
    # Configurable alerts (BTC price, etc.)
    check_alerts()
    # Daily memory cleanup — delete entries older than 90 days
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
    return tasks

def run_daemon(interval_minutes=30):
    """Run heartbeat every N minutes"""
    log.info(f"Heartbeat daemon starting (every {interval_minutes}min)")
    while True:
        heartbeat()
        time.sleep(interval_minutes * 60)

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        heartbeat()
    else:
        run_daemon(30)
