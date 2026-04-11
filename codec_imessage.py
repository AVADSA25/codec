"""CODEC iMessage — receive iMessages, process through CODEC, reply automatically.

Polls macOS Messages SQLite DB for new incoming messages, dispatches through
CODEC's skill system + LLM fallback, and replies via AppleScript.

Handles: text messages, image attachments (→ vision), audio attachments (→ whisper).

Usage:
    python3 codec_imessage.py              # Run standalone
    pm2 start ecosystem.config.js --only codec-imessage  # Via PM2

Requirements:
    - macOS with Messages app configured
    - Full Disk Access for Terminal/Python (System Settings → Privacy)
    - ~/.codec/config.json with "imessage" config block
"""

import os
import re
import json
import time
import sqlite3
import logging
import subprocess
import requests
from datetime import datetime, timedelta
from pathlib import Path

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [iMessage] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("codec-imessage")

# ── Version ──────────────────────────────────────────────────────────────────
VERSION = "2.1.0"

# ── Paths ────────────────────────────────────────────────────────────────────
MESSAGES_DB = os.path.expanduser("~/Library/Messages/chat.db")
CONFIG_PATH = os.path.expanduser("~/.codec/config.json")
MEMORY_DB   = os.path.expanduser("~/.codec/memory.db")
STATE_FILE  = os.path.expanduser("~/.codec/imessage_state.json")
AUDIT_LOG   = os.path.expanduser("~/.codec/audit.log")

# ── Config ───────────────────────────────────────────────────────────────────
def load_config():
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                cfg = json.load(f)
        except Exception as e:
            log.warning(f"Config parse error: {e}")
    return cfg


def get_imessage_config(cfg):
    """Extract iMessage-specific config with defaults."""
    im = cfg.get("imessage", {})
    return {
        "enabled": im.get("enabled", True),
        "allowed_senders": im.get("allowed_senders", []),   # empty = allow all
        "blocked_senders": im.get("blocked_senders", []),
        "poll_interval": im.get("poll_interval", 3),        # seconds
        "max_response_length": im.get("max_response_length", 4000),
        "auto_reply": im.get("auto_reply", True),
        "debug": im.get("debug", False),
    }


# ── LLM Config (reuse from codec_config pattern) ────────────────────────────
def get_llm_config(cfg):
    return {
        "base_url": cfg.get("llm_base_url", "http://localhost:8081/v1"),
        "model": cfg.get("llm_model", "mlx-community/Qwen3.5-35B-A3B-4bit"),
        "api_key": cfg.get("llm_api_key", ""),
        "kwargs": cfg.get("llm_kwargs", {}),
        "vision_url": cfg.get("vision_base_url", "http://localhost:8082/v1"),
        "vision_model": cfg.get("vision_model", "mlx-community/Qwen2.5-VL-7B-Instruct-4bit"),
        "whisper_url": cfg.get("stt_url", "http://localhost:8084/v1/audio/transcriptions"),
    }


# ── State persistence (last processed message ROWID) ────────────────────────
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_rowid": 0, "started": datetime.now().isoformat()}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


# ── Audit logging ────────────────────────────────────────────────────────────
def audit(msg):
    try:
        os.makedirs(os.path.dirname(AUDIT_LOG), exist_ok=True)
        with open(AUDIT_LOG, "a") as f:
            f.write(f"[{datetime.now().isoformat()}] IMESSAGE: {msg}\n")
    except Exception:
        pass


# ── macOS Messages DB reading ───────────────────────────────────────────────
def _extract_attributed_body(blob):
    """Extract plain text from macOS attributedBody blob (NSArchiver format)."""
    if not blob:
        return None
    try:
        # The text is embedded in the binary plist blob after "NSString" marker
        # Try to extract readable text between known markers
        text = blob.decode("utf-8", errors="ignore")
        # Find the actual message text — it's usually after "NSMutableString" or similar
        # and before the formatting data. Common pattern: text sits between
        # streamtyped markers. Try multiple extraction methods.

        # Method 1: Look for the text after the last null-heavy section
        import re
        # Strip non-printable chars, find longest readable segment
        segments = re.split(r'[\x00-\x08\x0e-\x1f]{3,}', text)
        candidates = [s.strip() for s in segments if len(s.strip()) > 1]
        if candidates:
            # The actual message is usually the first substantial segment
            for c in candidates:
                # Clean up any remaining control chars
                clean = re.sub(r'[\x00-\x1f]', '', c).strip()
                if len(clean) > 1 and not clean.startswith(('NSMutable', 'NSString', 'NSOrig')):
                    return clean
    except Exception:
        pass
    return None


def get_new_messages(last_rowid):
    """Poll chat.db for messages newer than last_rowid.

    Returns list of dicts: {rowid, text, sender, date, is_from_me, attachments}
    """
    if not os.path.exists(MESSAGES_DB):
        log.error(f"Messages DB not found: {MESSAGES_DB}")
        return []

    messages = []
    try:
        # Connect read-only to avoid locking the Messages app DB
        conn = sqlite3.connect(f"file:{MESSAGES_DB}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        # Get new incoming messages (is_from_me=0)
        # NOTE: newer macOS stores text in attributedBody (blob) instead of text column
        c.execute("""
            SELECT
                m.ROWID,
                m.text,
                m.date,
                m.is_from_me,
                m.cache_has_attachments,
                COALESCE(h.id, '') as sender,
                m.attributedBody
            FROM message m
            LEFT JOIN handle h ON m.handle_id = h.ROWID
            WHERE m.ROWID > ?
              AND m.is_from_me = 0
            ORDER BY m.ROWID ASC
            LIMIT 20
        """, (last_rowid,))

        rows = c.fetchall()
        for row in rows:
            # Extract text — fall back to attributedBody if text is NULL
            text = row["text"]
            if not text and row["attributedBody"]:
                text = _extract_attributed_body(row["attributedBody"])

            msg = {
                "rowid": row["ROWID"],
                "text": text,
                "sender": row["sender"],
                "date": _convert_apple_date(row["date"]),
                "is_from_me": bool(row["is_from_me"]),
                "attachments": [],
            }

            # Fetch attachments if any
            if row["cache_has_attachments"]:
                try:
                    c.execute("""
                        SELECT a.filename, a.mime_type, a.transfer_name
                        FROM attachment a
                        JOIN message_attachment_join maj ON a.ROWID = maj.attachment_id
                        WHERE maj.message_id = ?
                    """, (row["ROWID"],))
                    for att in c.fetchall():
                        filename = att["filename"]
                        if filename:
                            # macOS stores with ~ prefix
                            filename = os.path.expanduser(filename)
                        msg["attachments"].append({
                            "filename": filename,
                            "mime_type": att["mime_type"] or "",
                            "name": att["transfer_name"] or "",
                        })
                except Exception as e:
                    log.debug(f"Attachment fetch error: {e}")

            # Skip messages with no text and no attachments
            if not msg["text"] and not msg["attachments"]:
                continue

            messages.append(msg)

        conn.close()
    except Exception as e:
        log.error(f"DB read error: {e}")

    return messages


def _convert_apple_date(apple_date):
    """Convert Apple's Core Data timestamp (nanoseconds since 2001-01-01) to datetime."""
    if not apple_date:
        return datetime.now()
    try:
        # Apple epoch: 2001-01-01 00:00:00 UTC
        # Messages DB uses nanoseconds since Apple epoch
        unix_ts = apple_date / 1_000_000_000 + 978307200
        return datetime.fromtimestamp(unix_ts)
    except Exception:
        return datetime.now()


# ── Sender filtering ────────────────────────────────────────────────────────
def is_sender_allowed(sender, im_cfg):
    """Check if sender is allowed based on allowlist/blocklist."""
    if not sender:
        return False

    blocked = im_cfg.get("blocked_senders", [])
    if blocked and sender in blocked:
        log.info(f"Blocked sender: {sender}")
        return False

    allowed = im_cfg.get("allowed_senders", [])
    if allowed and sender not in allowed:
        log.info(f"Sender not in allowlist: {sender}")
        return False

    return True


# ── CODEC Skill dispatch ────────────────────────────────────────────────────
_dispatch_available = True
_check_skill = None
_run_skill = None

def _load_dispatch():
    """Lazy-load codec_dispatch, handling pynput/GUI dependency issues."""
    global _dispatch_available, _check_skill, _run_skill
    if _check_skill is not None:
        return _dispatch_available
    try:
        from codec_dispatch import check_skill, run_skill
        _check_skill = check_skill
        _run_skill = run_skill
        return True
    except Exception as e:
        log.warning(f"Skill dispatch unavailable ({e}) — LLM-only mode")
        _dispatch_available = False
        return False


def try_skill(text):
    """Try matching a CODEC skill. Returns (skill_name, result) or (None, None)."""
    if not _load_dispatch():
        return (None, None)
    try:
        skill = _check_skill(text)
        if skill:
            # Skip skills that need a terminal/GUI
            _SKIP_SKILLS = {"open_terminal", "run_command", "vibe_code", "deep_chat",
                            "memory_search", "ask_mike_to_build"}
            if skill["name"] in _SKIP_SKILLS:
                return (None, None)
            result = _run_skill(skill, text)
            if result:
                return (skill["name"], str(result))
    except Exception as e:
        log.warning(f"Skill error: {e}")
    return (None, None)


# ── LLM call ────────────────────────────────────────────────────────────────
def call_llm(text, sender, llm_cfg, conversation_history=None, system_prompt_override=None):
    """Send text to CODEC's LLM and return response."""
    if system_prompt_override:
        sys_prompt = system_prompt_override
    else:
        now_str = datetime.now().strftime("%A %B %d, %Y at %H:%M")
        sys_prompt = (
            f"You are CODEC, a personal AI assistant replying via iMessage. "
            f"Today is {now_str}. Be concise — this is a text message conversation. "
            f"Keep replies under 3 sentences unless more detail is needed. "
            f"Be natural and conversational, like texting a smart friend."
        )

    messages = [{"role": "system", "content": sys_prompt}]

    # Add conversation history for context
    if conversation_history:
        messages.extend(conversation_history[-8:])  # last 8 exchanges

    messages.append({"role": "user", "content": text})

    headers = {"Content-Type": "application/json"}
    if llm_cfg["api_key"]:
        headers["Authorization"] = f"Bearer {llm_cfg['api_key']}"

    payload = {
        "model": llm_cfg["model"],
        "messages": messages,
        "max_tokens": 1500,
        "temperature": 0.7,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    # Merge extra kwargs (but protect chat_template_kwargs)
    payload.update({k: v for k, v in llm_cfg["kwargs"].items() if k != "chat_template_kwargs"})

    try:
        r = requests.post(
            f"{llm_cfg['base_url']}/chat/completions",
            json=payload,
            headers=headers,
            timeout=120,
        )
        data = r.json()

        if "error" in data:
            log.error(f"LLM error: {data['error']}")
            return None
        if "choices" not in data or not data["choices"]:
            log.error(f"LLM no choices: {str(data)[:200]}")
            return None

        content = (data["choices"][0]["message"].get("content") or "").strip()
        # Strip thinking tags
        content = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()
        return content if content else None
    except requests.exceptions.Timeout:
        log.error("LLM timeout")
        return None
    except Exception as e:
        log.error(f"LLM call failed: {e}")
        return None


# ── Vision (image attachments) ──────────────────────────────────────────────
def process_image(filepath, llm_cfg):
    """Send image to vision model and return description."""
    if not filepath or not os.path.exists(filepath):
        return None
    try:
        import base64
        with open(filepath, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()

        ext = Path(filepath).suffix.lower()
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                "gif": "image/gif", "heic": "image/heic", "webp": "image/webp"
                }.get(ext.lstrip("."), "image/jpeg")

        messages = [
            {"role": "system", "content": "Describe this image concisely in 1-2 sentences."},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_b64}"}},
                {"type": "text", "text": "What's in this image?"},
            ]},
        ]

        r = requests.post(
            f"{llm_cfg['vision_url']}/chat/completions",
            json={"model": llm_cfg["vision_model"], "messages": messages, "max_tokens": 200},
            headers={"Content-Type": "application/json"},
            timeout=60,
        )
        data = r.json()
        if "choices" in data and data["choices"]:
            return data["choices"][0]["message"].get("content", "").strip()
    except Exception as e:
        log.warning(f"Vision processing failed: {e}")
    return None


# ── Audio transcription (voice notes) ───────────────────────────────────────
def transcribe_audio(filepath, llm_cfg):
    """Transcribe audio attachment via Whisper."""
    if not filepath or not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "rb") as f:
            r = requests.post(
                llm_cfg["whisper_url"],
                files={"file": (os.path.basename(filepath), f)},
                data={"model": "whisper-1"},
                timeout=60,
            )
        data = r.json()
        return data.get("text", "").strip() or None
    except Exception as e:
        log.warning(f"Transcription failed: {e}")
    return None


# ── Send iMessage via AppleScript ───────────────────────────────────────────
def send_imessage(recipient, text):
    """Send an iMessage using AppleScript."""
    if not text or not recipient:
        return False

    # Escape for AppleScript
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

    # Try buddy-based send first, fall back to chat-based send
    script_buddy = f'''
    tell application "Messages"
        set targetService to 1st account whose service type = iMessage
        set targetBuddy to buddy "{recipient}" of targetService
        send "{escaped}" to targetBuddy
    end tell
    '''

    script_chat = f'''
    tell application "Messages"
        set targetChat to a reference to text chat id "iMessage;-;{recipient}"
        send "{escaped}" to targetChat
    end tell
    '''

    for script in [script_buddy, script_chat]:
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                log.info(f"Sent reply to {recipient}: {text[:60]}...")
                return True
            log.debug(f"AppleScript attempt failed: {result.stderr.strip()}")
        except subprocess.TimeoutExpired:
            log.debug("AppleScript attempt timed out")
        except Exception as e:
            log.debug(f"AppleScript attempt error: {e}")

    # Final fallback — use 'send' to existing conversation
    script_fallback = f'''
    tell application "Messages"
        send "{escaped}" to (1st chat whose participants contains (buddy "{recipient}" of (1st account whose service type = iMessage)))
    end tell
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", script_fallback],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            log.info(f"Sent reply to {recipient}: {text[:60]}...")
            return True
        else:
            log.error(f"AppleScript error: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        log.error("AppleScript timeout")
        return False
    except Exception as e:
        log.error(f"Send failed: {e}")
        return False


# ── Conversation history (per-sender) ───────────────────────────────────────
_conversations = {}  # sender → list of {"role", "content"}


def get_conversation(sender):
    return _conversations.get(sender, [])


def add_to_conversation(sender, role, content):
    if sender not in _conversations:
        _conversations[sender] = []
    _conversations[sender].append({"role": role, "content": content})
    # Keep last 20 messages per sender
    if len(_conversations[sender]) > 20:
        _conversations[sender] = _conversations[sender][-20:]


# ── Daily Briefing: premium data gathering ───────────────────────────────
_RSS_FEEDS = [
    ("Financial Times",  "https://www.ft.com/news-feed?format=rss"),
    ("The Economist",    "https://feeds2.feedburner.com/economist/full_print_edition"),
    ("Reuters",          "https://rsshub.app/reuters/world"),
    ("The Hacker News",  "https://feeds.feedburner.com/TheHackersNews"),
    ("MIT Tech Review",  "https://www.technologyreview.com/topic/artificial-intelligence/feed/"),
    ("The Decoder",      "https://the-decoder.com/feed/"),
    ("The Verge",        "https://www.theverge.com/rss/index.xml"),
    ("Ars Technica",     "https://feeds.arstechnica.com/arstechnica/index"),
    ("TechCrunch",       "https://techcrunch.com/feed/"),
]


def _fetch_rss_headlines():
    """Fetch headlines from premium RSS feeds — returns list of (title, source, link)."""
    import xml.etree.ElementTree as ET
    from concurrent.futures import ThreadPoolExecutor, as_completed

    headlines = []

    def _fetch_one(name, url):
        try:
            r = requests.get(url, timeout=8, headers={"User-Agent": "CODEC/2.1"})
            root = ET.fromstring(r.content)
            items = []
            # Standard RSS
            for item in root.iter("item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                if title:
                    items.append((title, name, link))
            # Atom fallback
            if not items:
                ns = {"a": "http://www.w3.org/2005/Atom"}
                for entry in root.findall(".//a:entry", ns):
                    title = (entry.findtext("a:title", namespaces=ns) or "").strip()
                    link_el = entry.find("a:link", ns)
                    link = link_el.get("href", "") if link_el is not None else ""
                    if title:
                        items.append((title, name, link))
            return items[:5]  # max 5 per source
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(_fetch_one, name, url): name for name, url in _RSS_FEEDS}
        for fut in as_completed(futures, timeout=15):
            try:
                headlines.extend(fut.result())
            except Exception:
                pass

    # Dedupe by title similarity
    seen = set()
    unique = []
    for title, source, link in headlines:
        key = title.lower()[:60]
        if key not in seen:
            seen.add(key)
            unique.append((title, source, link))
    return unique


def _gather_briefing_data():
    """Gather ALL data for a premium daily briefing — Lucy-quality output."""
    data = {}

    # ── CODEC Skills (weather, calendar, tasks, email) ──
    for skill_name, query, limit in [
        ("weather",         "weather in Marbella today",     400),
        ("google_calendar", "today's calendar events",       600),
        ("google_tasks",    "pending tasks",                 500),
        ("google_gmail",    "unread emails summary",         400),
    ]:
        try:
            mod = __import__(f"skills.{skill_name}", fromlist=["run"])
            result = mod.run(query)
            if result:
                data[skill_name] = str(result)[:limit]
        except Exception as e:
            log.debug(f"Briefing {skill_name}: {e}")

    # ── Premium RSS headlines (9 sources, parallel fetch) ──
    try:
        headlines = _fetch_rss_headlines()
        if headlines:
            news_lines = []
            for i, (title, source, link) in enumerate(headlines[:15], 1):
                news_lines.append(f"{i}) {title}\n   ({source} — {link})")
            data["news"] = "\n".join(news_lines)
    except Exception as e:
        log.debug(f"Briefing RSS: {e}")

    # ── Crypto markets (BTC, ETH, SOL) ──
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price"
            "?ids=bitcoin,ethereum,solana&vs_currencies=usd&include_24hr_change=true",
            timeout=8,
        )
        coins = r.json()
        lines = []
        for cid, sym in [("bitcoin", "BTC"), ("ethereum", "ETH"), ("solana", "SOL")]:
            c = coins.get(cid, {})
            if c:
                p, ch = c.get("usd", 0), c.get("usd_24h_change", 0)
                lines.append(f"{sym}: ${p:,.0f} {'▲' if ch >= 0 else '▼'} {abs(ch):.1f}%")
        if lines:
            data["markets"] = " | ".join(lines)
    except Exception:
        pass

    if not data:
        return "[NO_DATA]"

    sections = []
    for key, val in data.items():
        sections.append(f"[{key.upper()}]\n{val}")
    return "\n\n".join(sections)


# ── Deep Report: full crew → Google Docs ────────────────────────────────
def _run_deep_report(sender):
    """Run the full daily_briefing_crew to generate a comprehensive report saved to Google Docs.

    Returns a short message with the Google Docs link, or an error message.
    """
    log.info(f"🔬 Running deep daily briefing crew for {sender}...")
    try:
        import sys
        # Ensure codec repo is on path for crew imports
        repo_dir = os.path.dirname(os.path.abspath(__file__))
        if repo_dir not in sys.path:
            sys.path.insert(0, repo_dir)

        from codec_agents import daily_briefing_crew
        crew = daily_briefing_crew()
        result = crew.run()

        if result:
            result_str = str(result).strip()
            # The crew's final output starts with the Google Docs URL
            # Extract it for a clean iMessage
            lines = result_str.split("\n")
            doc_url = None
            summary = result_str[:400]

            for line in lines:
                line = line.strip()
                if "docs.google.com" in line:
                    doc_url = line
                    break

            if doc_url:
                # Everything after the URL line is the summary
                url_idx = result_str.find(doc_url)
                summary = result_str[url_idx + len(doc_url):].strip()[:300]
                return (
                    f"📄 Your Daily Briefing is ready!\n\n"
                    f"{doc_url}\n\n"
                    f"{summary if summary else 'Full report with calendar, tasks, weather, markets & news.'}"
                )
            else:
                return f"📄 Daily Briefing:\n\n{result_str[:450]}"
        else:
            return "Sorry, the briefing crew didn't return a result. Try 'briefing' for a quick version."

    except ImportError as e:
        log.warning(f"Deep report import error: {e}")
        return "Deep report unavailable — missing dependencies. Try 'briefing' for a quick version."
    except Exception as e:
        log.error(f"Deep report error: {e}")
        return f"Deep report failed: {str(e)[:100]}. Try 'briefing' for a quick version."


# ── Smart Agent: goal/priority tracking (per-sender) ──────────────────────
_GOALS = {}  # sender → {"priorities": [...], "goals": [...], "last_updated": str}


def _load_goals(sender):
    """Load goals from memory.db for cross-session persistence."""
    if sender in _GOALS:
        return _GOALS[sender]
    try:
        if os.path.exists(MEMORY_DB):
            conn = sqlite3.connect(MEMORY_DB)
            c = conn.cursor()
            c.execute("""
                CREATE TABLE IF NOT EXISTS agent_goals (
                    sender TEXT PRIMARY KEY,
                    data TEXT,
                    updated_at TEXT
                )
            """)
            c.execute("SELECT data FROM agent_goals WHERE sender = ?", (sender,))
            row = c.fetchone()
            conn.close()
            if row:
                _GOALS[sender] = json.loads(row[0])
                return _GOALS[sender]
    except Exception as e:
        log.debug(f"Goals load error: {e}")
    _GOALS[sender] = {"priorities": [], "goals": [], "last_updated": ""}
    return _GOALS[sender]


def _save_goals(sender):
    """Persist goals to memory.db."""
    if sender not in _GOALS:
        return
    try:
        os.makedirs(os.path.dirname(MEMORY_DB), exist_ok=True)
        conn = sqlite3.connect(MEMORY_DB)
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS agent_goals (
                sender TEXT PRIMARY KEY,
                data TEXT,
                updated_at TEXT
            )
        """)
        now = datetime.now().isoformat()
        _GOALS[sender]["last_updated"] = now
        c.execute(
            "INSERT OR REPLACE INTO agent_goals (sender, data, updated_at) VALUES (?, ?, ?)",
            (sender, json.dumps(_GOALS[sender]), now),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        log.debug(f"Goals save error: {e}")


def detect_intent(text, sender):
    """Detect smart agent intents and return (intent_name, enhanced_system_prompt) or (None, None).

    Checks for 3 scenarios:
    1. Daily Briefing — "good morning", "briefing", "gm"
    2. Restaurant Decider — dining keywords + context
    3. Accountability Agent — "check in", "goals", "accountability", "how am i doing"
    """
    text_lower = text.lower().strip()
    now_str = datetime.now().strftime("%A %B %d, %Y at %H:%M")
    goals_data = _load_goals(sender)

    # ── 1. Daily Briefing ───────────────────────────────────────────────
    _BRIEFING_TRIGGERS = ["good morning", "briefing", "gm", "daily briefing",
                          "daily report", "morning briefing", "morning report"]
    if any(text_lower == t or text_lower.startswith(t + " ") or text_lower.startswith(t + ",")
           for t in _BRIEFING_TRIGGERS):
        # Gather REAL data from CODEC skills — weather, calendar, tasks, news
        briefing_data = _gather_briefing_data()
        priority_ctx = ""
        if goals_data["priorities"]:
            priority_ctx = (
                f"\nUser's active priorities: {', '.join(goals_data['priorities'][-3:])}."
            )
        prompt = (
            f"You are CODEC, a premium AI executive assistant. Today is {now_str}.\n\n"
            f"REAL DATA:\n{briefing_data}\n\n"
            f"Create a GORGEOUS daily briefing. Follow this EXACT format:\n\n"
            f"CODEC Briefing — [Day, DD Mon YYYY] — [HH:MM] CET\n\n"
            f"### ☀️ Weather\n"
            f"[City]: [Temp]°C, [condition], [wind].\n\n"
            f"### 📅 Today\n"
            f"- [Event] ([time])\n"
            f"- Nothing scheduled today. (if empty)\n\n"
            f"### 📊 Markets\n"
            f"BTC $[price] [▲/▼][%] | ETH $[price] [▲/▼][%] | SOL $[price] [▲/▼][%]\n\n"
            f"### 🗞️ Top 10 (No fluff)\n\n"
            f"1) [Headline]\n"
            f"[1-2 line 'why it matters' kicker] ([Source] — [link])\n\n"
            f"2) [Headline]\n"
            f"[kicker] ([Source] — [link])\n\n"
            f"... (continue to 10, BLANK LINE between each item)\n\n"
            f"NEWS RANKING RULES:\n"
            f"- #1-2: World / Geopolitics (PRIORITY)\n"
            f"- #3-4: Markets / Business\n"
            f"- #5-6: Security / Cyber / Tech\n"
            f"- #7-8: Science / Climate\n"
            f"- #9: Positive / Humanitarian\n"
            f"- #10: 'What the F*ck Fact' (the weird one)\n"
            f"Max 2 items about AI. Max 2 about the US. Represent 4+ global regions.\n"
            f"Each item: headline + 1-2 line kicker + (SOURCE — URL)\n"
            f"IMPORTANT: Put a BLANK LINE between each numbered news item for readability.\n\n"
            f"### 📧 Inbox\n"
            f"[X] unread | [notable senders if any]\n\n"
            f"### ✅ Tasks\n"
            f"- [Pending items]\n\n"
            f"### ⚡ Quote\n"
            f"\"[Motivational quote]\" — [Author]\n\n"
            f"### 😈 Joke of the day\n"
            f"[One sharp, witty line]\n\n"
            f"RULES:\n"
            f"- ZERO fabrication. Use ONLY the provided data.\n"
            f"- Include SOURCE NAME and LINK for every news item.\n"
            f"- Skip sections with no data — don't mention missing data.\n"
            f"- Total output UNDER 3000 characters.\n"
            f"- If exceeding, compress aggressively.\n"
            f"- Be sharp and world-weary, like a seasoned correspondent."
            f"{priority_ctx}"
        )
        return ("daily_briefing", prompt)

    # ── 1b. Deep Report (full crew → Google Docs) ───────────────────────
    _DEEP_TRIGGERS = ["full report", "deep briefing", "deep report", "full briefing",
                      "detailed report", "send me the report", "google doc report"]
    if any(t in text_lower for t in _DEEP_TRIGGERS):
        return ("deep_report", None)  # Special: handled in process_message directly

    # ── 2. Restaurant Decider ───────────────────────────────────────────
    _DINING_KEYWORDS = ["restaurant", "dinner", "lunch", "eat", "food", "hungry",
                        "brunch", "breakfast", "supper", "dining"]
    _DINING_CONTEXT = ["vibe", "area", "people", "group", "date", "romantic",
                       "casual", "fancy", "cheap", "budget", "outdoor", "terrace",
                       "nearby", "downtown", "headcount", "neighborhood", "cuisine",
                       "italian", "japanese", "mexican", "thai", "indian", "french",
                       "spanish", "sushi", "tapas", "steak", "seafood", "vegetarian",
                       "vegan", "marbella", "puerto banus"]
    has_dining = any(kw in text_lower for kw in _DINING_KEYWORDS)
    has_context = any(kw in text_lower for kw in _DINING_CONTEXT)
    if has_dining and has_context:
        prompt = (
            f"You are CODEC, a personal AI assistant replying via iMessage. "
            f"Today is {now_str}. The user is looking for a restaurant recommendation.\n\n"
            f"Based on what they described, give ONE specific restaurant recommendation:\n"
            f"- Restaurant name (real, well-known place matching their area/vibe)\n"
            f"- Cuisine type\n"
            f"- Price range (use $ to $$$$)\n"
            f"- One-liner on why it fits their request\n\n"
            f"If they mention Marbella/Spain, recommend from that area. "
            f"Be decisive — pick ONE place, don't hedge. Keep it to 3-4 sentences max. "
            f"Format it cleanly for iMessage readability."
        )
        return ("restaurant_decider", prompt)

    # ── 3. Accountability Agent ─────────────────────────────────────────
    _ACCOUNTABILITY_TRIGGERS = ["check in", "checkin", "check-in", "goals",
                                "accountability", "how am i doing",
                                "how's my progress", "progress update"]
    if any(t in text_lower for t in _ACCOUNTABILITY_TRIGGERS):
        # Build goal/priority context
        goal_ctx = "The user has no previously recorded goals or priorities."
        items = []
        if goals_data["priorities"]:
            items.append(f"Priorities: {', '.join(goals_data['priorities'][-5:])}")
        if goals_data["goals"]:
            items.append(f"Goals: {', '.join(goals_data['goals'][-5:])}")
        if items:
            updated = goals_data.get("last_updated", "unknown")
            goal_ctx = (
                f"The user's tracked items (last updated {updated}):\n"
                + "\n".join(f"- {item}" for item in items)
            )

        prompt = (
            f"You are CODEC, a personal AI assistant replying via iMessage. "
            f"Today is {now_str}. The user wants an accountability check-in.\n\n"
            f"{goal_ctx}\n\n"
            f"Your response should:\n"
            f"1. Reference their specific goals/priorities by name\n"
            f"2. Ask about progress on each one (briefly)\n"
            f"3. Be encouraging but real — like a friend who holds you accountable\n\n"
            f"If they have no recorded goals, ask them to share 2-3 goals they want to track. "
            f"Keep it under 5 sentences. Direct, no fluff."
        )
        return ("accountability_agent", prompt)

    return (None, None)


def _extract_goals_from_reply(text, sender):
    """After the user replies, check if they stated priorities or goals and store them."""
    text_lower = text.lower()
    goals_data = _load_goals(sender)

    # Detect if this looks like a priority/goal statement (heuristic)
    _GOAL_SIGNALS = ["my priority", "my goal", "i want to", "i need to", "focus on",
                     "working on", "top priority", "main goal", "this week",
                     "today i", "planning to", "going to"]
    if any(sig in text_lower for sig in _GOAL_SIGNALS):
        # Store the whole message as a priority/goal entry
        entry = f"{text.strip()[:200]} ({datetime.now().strftime('%b %d')})"
        # Determine if it's more of a priority or goal
        if any(w in text_lower for w in ["priority", "today", "focus"]):
            goals_data["priorities"].append(entry)
            goals_data["priorities"] = goals_data["priorities"][-10:]  # keep last 10
        else:
            goals_data["goals"].append(entry)
            goals_data["goals"] = goals_data["goals"][-10:]
        _save_goals(sender)
        log.info(f"Stored goal/priority for {sender}: {entry[:60]}")


# ── Save to CODEC memory DB ─────────────────────────────────────────────────
def save_to_memory(sender, user_text, assistant_text):
    """Store the exchange in CODEC's memory.db for cross-channel recall."""
    try:
        os.makedirs(os.path.dirname(MEMORY_DB), exist_ok=True)
        conn = sqlite3.connect(MEMORY_DB)
        c = conn.cursor()
        # Ensure conversations table exists
        c.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                timestamp TEXT,
                role TEXT,
                content TEXT
            )
        """)
        session_id = f"imessage-{sender}"
        ts = datetime.now().isoformat()
        c.execute(
            "INSERT INTO conversations (session_id, timestamp, role, content) VALUES (?,?,?,?)",
            (session_id, ts, "user", user_text[:2000]),
        )
        c.execute(
            "INSERT INTO conversations (session_id, timestamp, role, content) VALUES (?,?,?,?)",
            (session_id, ts, "assistant", assistant_text[:2000]),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        log.debug(f"Memory save error: {e}")


# ── Main processing loop ────────────────────────────────────────────────────
def process_message(msg, im_cfg, llm_cfg):
    """Process a single incoming message and send reply."""
    sender = msg["sender"]
    text = msg["text"].strip()

    # Trigger filter — only respond to messages starting with a trigger phrase
    # Exception: briefing commands work without prefix (for daily use)
    _TRIGGERS = ["hey codec", "/codec"]
    _DIRECT_COMMANDS = ["good morning", "gm", "briefing", "daily briefing",
                        "morning report", "full report", "deep briefing",
                        "check in", "goals"]
    text_lower = text.lower().strip()
    matched_trigger = None

    # Check direct commands first (no prefix needed)
    is_direct = any(text_lower == cmd or text_lower.startswith(cmd + " ")
                     or text_lower.startswith(cmd + ",") for cmd in _DIRECT_COMMANDS)

    if not is_direct:
        for trigger in _TRIGGERS:
            if text_lower.startswith(trigger):
                matched_trigger = trigger
                break
        if not matched_trigger:
            return  # Silently ignore — not a CODEC message
        # Strip the trigger prefix from the actual message
        text = text[len(matched_trigger):].strip().lstrip(",").lstrip(":").strip()

    if not text and not msg["attachments"]:
        return

    log.info(f"📨 From {sender}: {text[:80]}")
    audit(f"RECEIVED from={sender} text={text[:100]}")

    # ── Handle attachments ───────────────────────────────────────────────
    attachment_context = []
    for att in msg.get("attachments", []):
        mime = att.get("mime_type", "")
        fpath = att.get("filename", "")

        if mime.startswith("image/") or fpath.lower().endswith((".jpg", ".jpeg", ".png", ".heic", ".gif", ".webp")):
            desc = process_image(fpath, llm_cfg)
            if desc:
                attachment_context.append(f"[Image: {desc}]")
                log.info(f"🖼️ Image processed: {desc[:60]}")

        elif mime.startswith("audio/") or fpath.lower().endswith((".m4a", ".mp3", ".wav", ".caf", ".opus")):
            transcript = transcribe_audio(fpath, llm_cfg)
            if transcript:
                text = transcript  # Replace text with transcription
                log.info(f"🎤 Voice note transcribed: {transcript[:60]}")

    # Combine text with attachment context
    if attachment_context:
        text = text + "\n" + "\n".join(attachment_context) if text else "\n".join(attachment_context)

    if not text:
        return

    # ── Smart agent intent detection (runs BEFORE generic LLM) ─────────
    _extract_goals_from_reply(text, sender)  # always check for goal/priority statements
    intent_name, enhanced_prompt = detect_intent(text, sender)
    if intent_name:
        log.info(f"Smart intent detected: {intent_name}")

    # ── Deep Report: trigger full crew → Google Docs ─────────────────────
    if intent_name == "deep_report":
        reply = _run_deep_report(sender)
        if reply:
            # Skip skill/LLM — we have the report
            log.info(f"📄 Deep report generated for {sender}")
            # Jump straight to send
            max_len = im_cfg.get("max_response_length", 500)
            if len(reply) > max_len:
                reply = reply[:max_len - 3] + "..."
            if im_cfg.get("auto_reply", True):
                send_imessage(sender, reply)
                audit(f"SENT to={sender} text={reply[:100]}")
            add_to_conversation(sender, "user", text)
            add_to_conversation(sender, "assistant", reply)
            save_to_memory(sender, text, reply)
            return

    # ── Try CODEC skills first ───────────────────────────────────────────
    skill_name, skill_result = try_skill(text)
    if skill_result:
        reply = skill_result
        log.info(f"⚡ Skill '{skill_name}' handled")
    else:
        # ── LLM call (with enhanced prompt if smart intent matched) ──
        history = get_conversation(sender)
        reply = call_llm(text, sender, llm_cfg, conversation_history=history,
                         system_prompt_override=enhanced_prompt)

    if not reply:
        reply = "Sorry, I couldn't process that right now. Try again in a moment."

    # Truncate if too long for iMessage
    max_len = im_cfg.get("max_response_length", 500)
    if len(reply) > max_len:
        reply = reply[:max_len - 3] + "..."

    # ── Send reply ───────────────────────────────────────────────────────
    if im_cfg.get("auto_reply", True):
        success = send_imessage(sender, reply)
        if success:
            audit(f"SENT to={sender} text={reply[:100]}")
        else:
            log.error(f"Failed to send reply to {sender}")

    # ── Save to conversation history + memory ────────────────────────────
    add_to_conversation(sender, "user", text)
    add_to_conversation(sender, "assistant", reply)
    save_to_memory(sender, text, reply)


# ── Main loop ────────────────────────────────────────────────────────────────
def main():
    print(f"""
╔══════════════════════════════════════════════╗
║       CODEC iMessage  v{VERSION}                ║
║  Listening for incoming iMessages...         ║
╚══════════════════════════════════════════════╝
    """)

    # Verify Messages DB access
    if not os.path.exists(MESSAGES_DB):
        log.error(f"Messages database not found at {MESSAGES_DB}")
        log.error("Make sure Messages app is configured and Full Disk Access is granted.")
        return

    cfg = load_config()
    im_cfg = get_imessage_config(cfg)
    llm_cfg = get_llm_config(cfg)

    if not im_cfg["enabled"]:
        log.info("iMessage integration disabled in config. Set imessage.enabled=true to activate.")
        return

    state = load_state()
    last_rowid = state.get("last_rowid", 0)

    # If first run, start from current max ROWID (don't process old messages)
    if last_rowid == 0:
        try:
            conn = sqlite3.connect(f"file:{MESSAGES_DB}?mode=ro", uri=True)
            c = conn.cursor()
            c.execute("SELECT MAX(ROWID) FROM message")
            row = c.fetchone()
            last_rowid = row[0] if row and row[0] else 0
            conn.close()
            log.info(f"First run — starting from ROWID {last_rowid}")
        except Exception as e:
            log.warning(f"Could not get max ROWID: {e}")

    poll_interval = im_cfg.get("poll_interval", 3)

    allowed = im_cfg.get("allowed_senders", [])
    if allowed:
        log.info(f"Allowed senders: {', '.join(allowed)}")
    else:
        log.info("All senders allowed (configure imessage.allowed_senders to restrict)")

    audit("SERVICE_START")
    log.info(f"Triggers: 'Hey CODEC' or '/codec' — all other messages ignored")
    log.info(f"Polling every {poll_interval}s | LLM: {llm_cfg['model']}")

    try:
        while True:
            try:
                new_msgs = get_new_messages(last_rowid)

                for msg in new_msgs:
                    # Update ROWID tracker regardless of processing
                    if msg["rowid"] > last_rowid:
                        last_rowid = msg["rowid"]

                    # Sender filter
                    if not is_sender_allowed(msg["sender"], im_cfg):
                        continue

                    # Process
                    try:
                        process_message(msg, im_cfg, llm_cfg)
                    except Exception as e:
                        log.error(f"Message processing error: {e}")

                # Save state after each poll cycle
                if new_msgs:
                    state["last_rowid"] = last_rowid
                    save_state(state)

            except Exception as e:
                log.error(f"Poll cycle error: {e}")

            time.sleep(poll_interval)

    except KeyboardInterrupt:
        log.info("Shutting down...")
        state["last_rowid"] = last_rowid
        save_state(state)
        audit("SERVICE_STOP")


if __name__ == "__main__":
    main()
