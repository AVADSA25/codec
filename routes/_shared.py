"""Shared state and utilities used by route modules.

Extracted from codec_dashboard.py to avoid circular imports.
Both codec_dashboard.py and routes/*.py import from here.
"""
import os, json, threading, logging, secrets, time, hmac, uuid, sqlite3
from datetime import datetime, timedelta

log = logging.getLogger("codec_dashboard")

DASHBOARD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.expanduser("~/.codec/config.json")
TASK_QUEUE = os.path.expanduser("~/.codec/task_queue.txt")
DB_PATH = os.path.expanduser("~/.q_memory.db")
AUDIT_LOG = os.path.expanduser("~/.codec/audit.log")
NOTIFICATIONS_PATH = os.path.expanduser("~/.codec/notifications.json")
SCHEDULE_RUNS_LOG = os.path.expanduser("~/.codec/schedule_runs.log")

_NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}


def _get_skills_dir():
    """Single source of truth for skills directory -- loads from codec_config."""
    try:
        from codec_config import SKILLS_DIR
        return SKILLS_DIR
    except ImportError:
        return os.path.join(DASHBOARD_DIR, "skills")


def _audit_write(line: str):
    """Append to audit log with restricted permissions."""
    os.makedirs(os.path.dirname(AUDIT_LOG), exist_ok=True)
    with open(AUDIT_LOG, "a") as f:
        f.write(line)
    try:
        os.chmod(AUDIT_LOG, 0o600)
    except OSError:
        pass


# ── Notification helpers ──

_notif_lock = threading.Lock()


def _load_notifications():
    """Load notifications from disk, seeding sample data on first access."""
    try:
        with open(NOTIFICATIONS_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        samples = [
            {
                "id": f"notif_{uuid.uuid4().hex[:10]}",
                "type": "task_report",
                "title": "Daily Morning Briefing",
                "body": "Completed successfully. 5 action items identified, market overview compiled, calendar conflicts flagged.",
                "status": "success",
                "created": "2026-03-29T08:00:00",
                "read": True,
                "schedule_id": "daily_briefing"
            },
            {
                "id": f"notif_{uuid.uuid4().hex[:10]}",
                "type": "task_report",
                "title": "Security Scan",
                "body": "No vulnerabilities found. All 142 dependencies are up to date, no CVEs detected.",
                "status": "success",
                "created": "2026-03-29T12:00:00",
                "read": True,
                "schedule_id": "security_scan"
            },
            {
                "id": f"notif_{uuid.uuid4().hex[:10]}",
                "type": "task_report",
                "title": "AI News Digest",
                "body": "33 stories collected from 5 sources. Top story: new open-weight model benchmarks released.",
                "status": "success",
                "created": "2026-03-29T18:30:00",
                "read": False,
                "schedule_id": "ai_news_digest"
            },
            {
                "id": f"notif_{uuid.uuid4().hex[:10]}",
                "type": "task_report",
                "title": "Weekly Code Review Summary",
                "body": "Analyzed 12 PRs across 3 repos. 2 require attention: stale dependency warnings in codec-core and missing tests in dashboard module.",
                "status": "success",
                "created": "2026-03-28T09:00:00",
                "read": True,
                "schedule_id": "weekly_code_review"
            }
        ]
        _write_notifications(samples)
        return samples


def _write_notifications(notifications):
    """Persist notifications list to disk."""
    os.makedirs(os.path.dirname(NOTIFICATIONS_PATH), exist_ok=True)
    with open(NOTIFICATIONS_PATH, "w") as f:
        json.dump(notifications, f, indent=2)


def _save_notification(title, body, status="success", schedule_id=None):
    """Create and persist a new notification, returning its id."""
    notif = {
        "id": f"notif_{uuid.uuid4().hex[:10]}",
        "type": "task_report",
        "title": title,
        "body": body,
        "status": status,
        "created": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "read": False,
        "schedule_id": schedule_id
    }
    with _notif_lock:
        notifications = _load_notifications()
        notifications.insert(0, notif)
        _write_notifications(notifications)
    return notif["id"]


def _append_schedule_run_log(schedule_id, title, status, body_preview=""):
    """Append a run record to the schedule runs log."""
    os.makedirs(os.path.dirname(SCHEDULE_RUNS_LOG), exist_ok=True)
    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "schedule_id": schedule_id,
        "title": title,
        "status": status,
        "body_preview": body_preview[:200]
    }
    with open(SCHEDULE_RUNS_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ── Biometric (Touch ID) Auth ──

def _load_cfg():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}

_bio_cfg = _load_cfg()
AUTH_ENABLED = _bio_cfg.get("auth_enabled", False)
AUTH_SESSION_HOURS = _bio_cfg.get("auth_session_hours", 24)
AUTH_BINARY = os.path.join(DASHBOARD_DIR, "codec_auth", "codec_auth")
AUTH_PIN_HASH = _bio_cfg.get("auth_pin_hash", "")
AUTH_COOKIE_NAME = "codec_session"

# Persistent session store
_SESSION_FILE = os.path.expanduser("~/.codec/.auth_sessions.json")
_auth_sessions = {}
_auth_lock = threading.Lock()
_e2e_keys = {}
_E2E_KEYS_FILE = os.path.expanduser("~/.codec/.e2e_keys.json")


def _load_e2e_keys():
    """Load E2E keys from disk so they survive PM2 restarts."""
    global _e2e_keys
    try:
        import base64
        if os.path.isfile(_E2E_KEYS_FILE):
            with open(_E2E_KEYS_FILE) as f:
                raw = json.load(f)
            for tok, key_b64 in raw.items():
                _e2e_keys[tok] = base64.b64decode(key_b64)
            log.info("Restored %d E2E key(s) from disk", len(_e2e_keys))
    except Exception as e:
        log.warning("Could not load E2E keys: %s", e)


def _save_e2e_keys():
    """Persist E2E keys to disk. Only keep keys for active sessions."""
    try:
        import base64
        os.makedirs(os.path.dirname(_E2E_KEYS_FILE), exist_ok=True)
        raw = {}
        for tok, key_bytes in _e2e_keys.items():
            if tok in _auth_sessions:
                raw[tok] = base64.b64encode(key_bytes).decode()
        with open(_E2E_KEYS_FILE, "w") as f:
            json.dump(raw, f)
        os.chmod(_E2E_KEYS_FILE, 0o600)
    except Exception as e:
        log.warning("Could not save E2E keys: %s", e)


def _load_sessions():
    """Load sessions from disk on startup."""
    global _auth_sessions
    try:
        if os.path.isfile(_SESSION_FILE):
            with open(_SESSION_FILE) as f:
                raw = json.load(f)
            now = datetime.now()
            for tok, data in raw.items():
                created = datetime.fromisoformat(data["created"])
                if now - created < timedelta(hours=AUTH_SESSION_HOURS):
                    _auth_sessions[tok] = {
                        "created": created,
                        "ip": data.get("ip", "unknown"),
                        "method": data.get("method", "unknown"),
                    }
            log.info("Restored %d auth session(s) from disk", len(_auth_sessions))
    except Exception as e:
        log.warning("Could not load auth sessions: %s", e)


def _save_sessions():
    """Persist current sessions to disk. Caller must hold _auth_lock."""
    try:
        os.makedirs(os.path.dirname(_SESSION_FILE), exist_ok=True)
        raw = {}
        for tok, data in _auth_sessions.items():
            raw[tok] = {
                "created": data["created"].isoformat(),
                "ip": data.get("ip", "unknown"),
                "method": data.get("method", "unknown"),
            }
        with open(_SESSION_FILE, "w") as f:
            json.dump(raw, f)
        os.chmod(_SESSION_FILE, 0o600)
    except Exception as e:
        log.warning("Could not save auth sessions: %s", e)


# Load on import
_load_sessions()
_load_e2e_keys()


def _is_auth_compiled():
    return os.path.isfile(AUTH_BINARY) and os.access(AUTH_BINARY, os.X_OK)


def _auth_available():
    """Check if any auth method is available (Touch ID binary or PIN configured)."""
    return _is_auth_compiled() or bool(AUTH_PIN_HASH)


def _is_totp_enabled():
    """Check if TOTP 2FA is configured and not disabled."""
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        return bool(cfg.get("totp_secret")) and not cfg.get("totp_disabled", False)
    except Exception:
        return False


def _verify_biometric_session(request):
    """Check if the request has a valid auth session cookie."""
    if not AUTH_ENABLED or not _auth_available():
        return True
    token = request.cookies.get(AUTH_COOKIE_NAME)
    with _auth_lock:
        if not token or token not in _auth_sessions:
            return False
        session = _auth_sessions[token]
        if datetime.now() - session["created"] > timedelta(hours=AUTH_SESSION_HOURS):
            del _auth_sessions[token]
            _save_sessions()
            return False
        if _is_totp_enabled() and not session.get("totp_verified"):
            return False
    return True


# ── Database helpers ──

_db_conn = None

def get_db():
    global _db_conn
    if _db_conn is None:
        _db_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _db_conn.execute("PRAGMA journal_mode=WAL")
        _db_conn.execute("PRAGMA busy_timeout=5000")
        _db_conn.row_factory = sqlite3.Row
    return _db_conn


# ── In-memory job stores ──

_pending_skills: dict = {}
_research_jobs: dict = {}
_agent_jobs: dict = {}

# PIN brute-force rate limiting
_pin_attempts: dict = {}

# Agents directory
_AGENTS_DIR = os.path.expanduser("~/.codec/agents")
os.makedirs(_AGENTS_DIR, exist_ok=True)
