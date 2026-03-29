"""CODEC Heartbeat — periodic check of logs, memory, and pending tasks"""
import time, sqlite3, os, json, logging, requests
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

def check_system_health():
    """Verify all CODEC services are running"""
    services = {
        "LLM": "http://localhost:8081/v1/models",
        "Whisper": "http://localhost:8084/health",
        "Kokoro": "http://localhost:8085/v1/models",
        "Dashboard": "http://localhost:8090/",
        "Vision": "http://localhost:8082/v1/models",
    }
    for name, url in services.items():
        try:
            r = requests.get(url, timeout=3)
            status = "✅" if r.status_code == 200 else f"⚠️ {r.status_code}"
        except:
            status = "❌ DOWN"
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

def heartbeat():
    """Run one heartbeat cycle"""
    log.info("═══ CODEC Heartbeat ═══")
    check_system_health()
    check_memory_stats()
    tasks = check_pending_tasks()
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
