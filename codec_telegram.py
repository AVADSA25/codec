"""CODEC Telegram Bot — receive Telegram messages, process through CODEC, reply.

Uses Telegram Bot API long polling (getUpdates). Routes messages through
CODEC's skill system + LLM fallback.

Triggers: messages starting with "Hey CODEC", "/codec", or any DM to the bot.

Usage:
    python3 codec_telegram.py              # Run standalone
    pm2 start ecosystem.config.js --only codec-telegram

Config in ~/.codec/config.json:
    "telegram": {
        "bot_token": "YOUR_BOT_TOKEN",
        "allowed_chat_ids": [],        // empty = allow all
        "require_trigger": false       // true = require "Hey CODEC" or "/codec" prefix
    }
"""

import os
import re
import json
import time
import logging
import requests
import sqlite3
from datetime import datetime

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [Telegram] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("codec-telegram")

# ── Version ──────────────────────────────────────────────────────────────────
VERSION = "2.1.0"

# ── Paths ────────────────────────────────────────────────────────────────────
CONFIG_PATH = os.path.expanduser("~/.codec/config.json")
MEMORY_DB   = os.path.expanduser("~/.codec/memory.db")
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


def get_telegram_config(cfg):
    tg = cfg.get("telegram", {})
    return {
        "bot_token": tg.get("bot_token", ""),
        "allowed_chat_ids": tg.get("allowed_chat_ids", []),  # empty = allow all
        "require_trigger": tg.get("require_trigger", False),  # DMs don't need trigger by default
        "max_response_length": tg.get("max_response_length", 4000),
    }


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


# ── Audit ────────────────────────────────────────────────────────────────────
def audit(msg):
    try:
        os.makedirs(os.path.dirname(AUDIT_LOG), exist_ok=True)
        with open(AUDIT_LOG, "a") as f:
            f.write(f"[{datetime.now().isoformat()}] TELEGRAM: {msg}\n")
    except Exception:
        pass


# ── Telegram Bot API ─────────────────────────────────────────────────────────
class TelegramBot:
    def __init__(self, token):
        self.token = token
        self.api = f"https://api.telegram.org/bot{token}"
        self.offset = 0  # last update_id + 1

    def get_me(self):
        """Verify bot token and get bot info."""
        r = requests.get(f"{self.api}/getMe", timeout=10)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Bot token invalid: {data}")
        return data["result"]

    def get_updates(self, timeout=30):
        """Long-poll for new messages."""
        try:
            r = requests.get(
                f"{self.api}/getUpdates",
                params={"offset": self.offset, "timeout": timeout, "allowed_updates": '["message"]'},
                timeout=timeout + 10,
            )
            data = r.json()
            if not data.get("ok"):
                log.error(f"getUpdates error: {data}")
                return []
            return data.get("result", [])
        except requests.exceptions.Timeout:
            return []  # Normal for long polling
        except Exception as e:
            log.error(f"getUpdates failed: {e}")
            time.sleep(3)
            return []

    def send_message(self, chat_id, text, reply_to=None):
        """Send a text message."""
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        try:
            r = requests.post(f"{self.api}/sendMessage", json=payload, timeout=15)
            data = r.json()
            if not data.get("ok"):
                # Retry without Markdown if parsing failed
                if "can't parse" in str(data).lower():
                    payload["parse_mode"] = None
                    r = requests.post(f"{self.api}/sendMessage", json=payload, timeout=15)
                    data = r.json()
                if not data.get("ok"):
                    log.error(f"sendMessage error: {data}")
                    return False
            return True
        except Exception as e:
            log.error(f"sendMessage failed: {e}")
            return False

    def send_typing(self, chat_id):
        """Show typing indicator."""
        try:
            requests.post(f"{self.api}/sendChatAction",
                          json={"chat_id": chat_id, "action": "typing"}, timeout=5)
        except Exception:
            pass

    def send_voice(self, chat_id, audio_path, caption=None):
        """Send a voice note (.ogg opus file)."""
        try:
            with open(audio_path, "rb") as f:
                files = {"voice": (os.path.basename(audio_path), f, "audio/ogg")}
                data = {"chat_id": chat_id}
                if caption:
                    data["caption"] = caption[:1024]
                r = requests.post(f"{self.api}/sendVoice", data=data, files=files, timeout=30)
                return r.json().get("ok", False)
        except Exception as e:
            log.warning(f"sendVoice failed: {e}")
            return False

    def get_file(self, file_id):
        """Get file download URL."""
        try:
            r = requests.get(f"{self.api}/getFile", params={"file_id": file_id}, timeout=10)
            data = r.json()
            if data.get("ok"):
                path = data["result"]["file_path"]
                return f"https://api.telegram.org/file/bot{self.token}/{path}"
        except Exception as e:
            log.warning(f"getFile failed: {e}")
        return None

    def download_file(self, file_url, dest_path):
        """Download a file from Telegram servers."""
        try:
            r = requests.get(file_url, timeout=30)
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            with open(dest_path, "wb") as f:
                f.write(r.content)
            return True
        except Exception as e:
            log.warning(f"File download failed: {e}")
            return False


# ── CODEC Skill dispatch ────────────────────────────────────────────────────
_dispatch_loaded = False
_check_skill = None
_run_skill = None


def _load_dispatch():
    global _dispatch_loaded, _check_skill, _run_skill
    if _dispatch_loaded:
        return _check_skill is not None
    _dispatch_loaded = True
    try:
        from codec_dispatch import check_skill, run_skill
        _check_skill = check_skill
        _run_skill = run_skill
        log.info("Skill dispatch loaded")
        return True
    except Exception as e:
        log.warning(f"Skill dispatch unavailable ({e}) — LLM-only mode")
        return False


def try_skill(text):
    if not _load_dispatch():
        return (None, None)
    try:
        skill = _check_skill(text)
        if skill:
            _SKIP = {"open_terminal", "run_command", "vibe_code", "deep_chat",
                      "memory_search", "ask_mike_to_build"}
            if skill["name"] in _SKIP:
                return (None, None)
            result = _run_skill(skill, text)
            if result:
                return (skill["name"], str(result))
    except Exception as e:
        log.warning(f"Skill error: {e}")
    return (None, None)


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
    """Fetch from 9 premium RSS feeds in parallel."""
    import xml.etree.ElementTree as ET
    from concurrent.futures import ThreadPoolExecutor, as_completed

    headlines = []

    def _fetch_one(name, url):
        try:
            r = requests.get(url, timeout=8, headers={"User-Agent": "CODEC/2.1"})
            root = ET.fromstring(r.content)
            items = []
            for item in root.iter("item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                if title:
                    items.append((title, name, link))
            if not items:
                ns = {"a": "http://www.w3.org/2005/Atom"}
                for entry in root.findall(".//a:entry", ns):
                    title = (entry.findtext("a:title", namespaces=ns) or "").strip()
                    link_el = entry.find("a:link", ns)
                    link = link_el.get("href", "") if link_el is not None else ""
                    if title:
                        items.append((title, name, link))
            return items[:5]
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(_fetch_one, n, u): n for n, u in _RSS_FEEDS}
        for fut in as_completed(futures, timeout=15):
            try:
                headlines.extend(fut.result())
            except Exception:
                pass

    seen = set()
    unique = []
    for title, source, link in headlines:
        key = title.lower()[:60]
        if key not in seen:
            seen.add(key)
            unique.append((title, source, link))
    return unique


def _gather_briefing_data():
    """Gather ALL data for a premium daily briefing."""
    data = {}

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

    try:
        headlines = _fetch_rss_headlines()
        if headlines:
            news_lines = []
            for i, (title, source, link) in enumerate(headlines[:15], 1):
                news_lines.append(f"{i}) {title}\n   ({source} — {link})")
            data["news"] = "\n".join(news_lines)
    except Exception as e:
        log.debug(f"Briefing RSS: {e}")

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
    return "\n\n".join(f"[{k.upper()}]\n{v}" for k, v in data.items())


def _generate_voice_briefing(text, chat_id):
    """Generate a TTS voice note from the briefing text using Kokoro."""
    try:
        cfg = load_config()
        tts_url = cfg.get("tts_url", "http://localhost:8085/v1/audio/speech")
        tts_model = cfg.get("tts_model", "mlx-community/Kokoro-82M-bf16")
        tts_voice = cfg.get("briefing_voice", "af_heart")  # Female voice for briefings

        # Strip markdown/formatting for cleaner speech
        clean = re.sub(r'[━─├└│*_`#]', '', text)
        clean = re.sub(r'https?://\S+', '', clean)  # remove URLs
        clean = re.sub(r'\([^)]*\)', '', clean)  # remove (Source — link)
        clean = re.sub(r'\n{2,}', '. ', clean)
        clean = re.sub(r'\n', ' ', clean)
        clean = re.sub(r'\s{2,}', ' ', clean).strip()
        # Limit to ~60 seconds of speech (~150 words) — Kokoro can't handle long text
        words = clean.split()
        if len(words) > 180:
            clean = " ".join(words[:180]) + ". End of briefing."

        # Chunk into segments if still long (Kokoro max ~500 chars per call)
        audio_chunks = []
        sentences = re.split(r'(?<=[.!?])\s+', clean)
        chunk = ""
        for sent in sentences:
            if len(chunk) + len(sent) > 450:
                if chunk:
                    audio_chunks.append(chunk.strip())
                chunk = sent
            else:
                chunk += " " + sent
        if chunk.strip():
            audio_chunks.append(chunk.strip())

        audio_path = os.path.expanduser(f"~/.codec/briefing_voice_{chat_id}.mp3")
        os.makedirs(os.path.dirname(audio_path), exist_ok=True)

        with open(audio_path, "wb") as f:
            for i, chunk_text in enumerate(audio_chunks):
                try:
                    r = requests.post(
                        tts_url,
                        json={"model": tts_model, "input": chunk_text, "voice": tts_voice},
                        timeout=30,
                    )
                    if r.status_code == 200 and r.content:
                        f.write(r.content)
                    else:
                        log.debug(f"TTS chunk {i} failed: {r.status_code}")
                except Exception as e:
                    log.debug(f"TTS chunk {i} error: {e}")

        if os.path.getsize(audio_path) > 0:
            log.info(f"🎙️ Voice briefing generated: {os.path.getsize(audio_path)} bytes, {len(audio_chunks)} chunks")
            return audio_path
        else:
            os.unlink(audio_path)
            log.warning("TTS produced empty audio")
    except Exception as e:
        log.warning(f"Voice briefing failed: {e}")
    return None


def _run_deep_report(chat_id):
    """Run full daily_briefing_crew → Google Docs."""
    log.info(f"🔬 Running deep briefing crew for chat {chat_id}...")
    try:
        import sys
        repo_dir = os.path.dirname(os.path.abspath(__file__))
        if repo_dir not in sys.path:
            sys.path.insert(0, repo_dir)
        from codec_agents import daily_briefing_crew
        crew = daily_briefing_crew()
        result = crew.run()
        if result:
            result_str = str(result).strip()
            for line in result_str.split("\n"):
                if "docs.google.com" in line:
                    summary = result_str[result_str.find(line) + len(line):].strip()[:300]
                    return f"📄 *Daily Briefing ready!*\n\n{line.strip()}\n\n{summary or 'Full report inside.'}"
            return f"📄 Daily Briefing:\n\n{result_str[:800]}"
        return "Briefing crew returned no result. Try `briefing` for a quick version."
    except Exception as e:
        log.error(f"Deep report error: {e}")
        return f"Deep report failed: {str(e)[:100]}"


# ── LLM call ────────────────────────────────────────────────────────────────
def call_llm(text, llm_cfg, conversation_history=None, system_prompt_override=None):
    if system_prompt_override:
        sys_prompt = system_prompt_override
    else:
        now_str = datetime.now().strftime("%A %B %d, %Y at %H:%M")
        sys_prompt = (
            f"You are CODEC, a personal AI assistant replying via Telegram. "
            f"Today is {now_str}. Be concise and direct. "
            f"Keep replies under 3 sentences unless more detail is needed. "
            f"You can use Markdown formatting. Be natural and helpful."
        )

    messages = [{"role": "system", "content": sys_prompt}]
    if conversation_history:
        messages.extend(conversation_history[-8:])
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
    payload.update({k: v for k, v in llm_cfg["kwargs"].items() if k != "chat_template_kwargs"})

    try:
        r = requests.post(
            f"{llm_cfg['base_url']}/chat/completions",
            json=payload, headers=headers, timeout=120,
        )
        data = r.json()
        if "error" in data:
            log.error(f"LLM error: {data['error']}")
            return None
        if "choices" not in data or not data["choices"]:
            log.error(f"LLM no choices: {str(data)[:200]}")
            return None
        content = (data["choices"][0]["message"].get("content") or "").strip()
        content = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()
        return content if content else None
    except requests.exceptions.Timeout:
        log.error("LLM timeout")
        return None
    except Exception as e:
        log.error(f"LLM call failed: {e}")
        return None


# ── Vision (photo messages) ─────────────────────────────────────────────────
def process_image(filepath, llm_cfg, caption=""):
    if not filepath or not os.path.exists(filepath):
        return None
    try:
        import base64
        with open(filepath, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        prompt = caption if caption else "What's in this image? Describe concisely."
        messages = [
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                {"type": "text", "text": prompt},
            ]},
        ]
        r = requests.post(
            f"{llm_cfg['vision_url']}/chat/completions",
            json={"model": llm_cfg["vision_model"], "messages": messages, "max_tokens": 300},
            headers={"Content-Type": "application/json"}, timeout=60,
        )
        data = r.json()
        if "choices" in data and data["choices"]:
            return data["choices"][0]["message"].get("content", "").strip()
    except Exception as e:
        log.warning(f"Vision failed: {e}")
    return None


# ── Audio transcription (voice messages) ────────────────────────────────────
def transcribe_audio(filepath, llm_cfg):
    if not filepath or not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "rb") as f:
            r = requests.post(
                llm_cfg["whisper_url"],
                files={"file": (os.path.basename(filepath), f)},
                data={"model": "whisper-1"}, timeout=60,
            )
        data = r.json()
        return data.get("text", "").strip() or None
    except Exception as e:
        log.warning(f"Transcription failed: {e}")
    return None


# ── Conversation history (per chat) ─────────────────────────────────────────
_conversations = {}


def get_history(chat_id):
    return _conversations.get(chat_id, [])


def add_history(chat_id, role, content):
    if chat_id not in _conversations:
        _conversations[chat_id] = []
    _conversations[chat_id].append({"role": role, "content": content})
    if len(_conversations[chat_id]) > 20:
        _conversations[chat_id] = _conversations[chat_id][-20:]


# ── Save to CODEC memory DB ─────────────────────────────────────────────────
def save_to_memory(chat_id, user_text, assistant_text):
    try:
        os.makedirs(os.path.dirname(MEMORY_DB), exist_ok=True)
        conn = sqlite3.connect(MEMORY_DB)
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT, timestamp TEXT, role TEXT, content TEXT
            )
        """)
        session_id = f"telegram-{chat_id}"
        ts = datetime.now().isoformat()
        c.execute("INSERT INTO conversations (session_id, timestamp, role, content) VALUES (?,?,?,?)",
                  (session_id, ts, "user", user_text[:2000]))
        c.execute("INSERT INTO conversations (session_id, timestamp, role, content) VALUES (?,?,?,?)",
                  (session_id, ts, "assistant", assistant_text[:2000]))
        conn.commit()
        conn.close()
    except Exception as e:
        log.debug(f"Memory save error: {e}")


# ── Process message ─────────────────────────────────────────────────────────
def process_message(bot, update, tg_cfg, llm_cfg):
    msg = update.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    msg_id = msg.get("message_id")
    user = msg.get("from", {})
    username = user.get("username", "")
    first_name = user.get("first_name", "")

    if not chat_id:
        return

    # Chat ID filter
    allowed = tg_cfg.get("allowed_chat_ids", [])
    if allowed and chat_id not in allowed:
        log.info(f"Chat {chat_id} not in allowlist — ignored")
        return

    # ── Extract text ─────────────────────────────────────────────────────
    text = msg.get("text", "")
    caption = msg.get("caption", "")

    # Handle /start command
    if text == "/start":
        bot.send_message(chat_id,
            "Hey! I'm *CODEC*, your personal AI assistant. 🤖\n\n"
            "Just send me a message and I'll help you out.\n"
            "I can handle text, photos, and voice messages.",
            reply_to=msg_id)
        return

    # ── Handle photo ─────────────────────────────────────────────────────
    if msg.get("photo"):
        bot.send_typing(chat_id)
        # Get largest photo
        photo = msg["photo"][-1]
        file_url = bot.get_file(photo["file_id"])
        if file_url:
            tmp = os.path.expanduser(f"~/.codec/tmp_tg_{chat_id}.jpg")
            if bot.download_file(file_url, tmp):
                result = process_image(tmp, llm_cfg, caption=caption or "")
                if result:
                    bot.send_message(chat_id, result, reply_to=msg_id)
                    add_history(chat_id, "user", f"[Photo] {caption}" if caption else "[Photo]")
                    add_history(chat_id, "assistant", result)
                    save_to_memory(chat_id, f"[Photo] {caption}", result)
                    audit(f"PHOTO chat={chat_id} user={username}")
                    return
                try:
                    os.unlink(tmp)
                except Exception:
                    pass
        bot.send_message(chat_id, "Sorry, I couldn't process that image.", reply_to=msg_id)
        return

    # ── Handle voice/audio ───────────────────────────────────────────────
    voice = msg.get("voice") or msg.get("audio")
    if voice:
        bot.send_typing(chat_id)
        file_url = bot.get_file(voice["file_id"])
        if file_url:
            ext = ".ogg" if msg.get("voice") else ".mp3"
            tmp = os.path.expanduser(f"~/.codec/tmp_tg_{chat_id}{ext}")
            if bot.download_file(file_url, tmp):
                transcript = transcribe_audio(tmp, llm_cfg)
                if transcript:
                    text = transcript
                    log.info(f"🎤 Voice transcribed: {transcript[:60]}")
                try:
                    os.unlink(tmp)
                except Exception:
                    pass

    if not text:
        return

    # ── Trigger filter (if enabled) ──────────────────────────────────────
    if tg_cfg.get("require_trigger", False):
        _TRIGGERS = ["hey codec", "/codec"]
        text_lower = text.lower()
        matched = False
        for trigger in _TRIGGERS:
            if text_lower.startswith(trigger):
                text = text[len(trigger):].strip().lstrip(",").lstrip(":").strip()
                matched = True
                break
        if not matched:
            return

    # Strip bot commands like /codec@Codec_mf_bot
    if text.startswith("/codec"):
        text = re.sub(r'^/codec(@\w+)?\s*', '', text).strip()
    if not text:
        bot.send_message(chat_id, "Hey! What can I help you with?", reply_to=msg_id)
        return

    log.info(f"📨 From {first_name} (@{username}): {text[:80]}")
    audit(f"RECEIVED chat={chat_id} user={username} text={text[:100]}")

    # Show typing
    bot.send_typing(chat_id)

    # ── Smart intents: daily briefing & deep report ──────────────────────
    text_lower = text.lower().strip()
    _BRIEFING = ["good morning", "briefing", "gm", "daily briefing", "morning report"]
    _DEEP = ["full report", "deep briefing", "deep report", "full briefing",
             "detailed report", "google doc report"]

    if any(text_lower == t or text_lower.startswith(t + " ") or text_lower.startswith(t + ",")
           for t in _BRIEFING):
        # Quick briefing with real data
        briefing_data = _gather_briefing_data()
        now_str = datetime.now().strftime("%A %B %d, %Y at %H:%M")
        enhanced_prompt = (
            f"You are CODEC, a premium AI executive assistant. Today is {now_str}.\n\n"
            f"REAL DATA:\n{briefing_data}\n\n"
            f"Create a GORGEOUS Telegram briefing. EXACT format:\n\n"
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
            f"[1-2 line kicker] ([Source] — [link])\n\n"
            f"2) [Headline]\n"
            f"[kicker] ([Source] — [link])\n\n"
            f"... (continue to 10, BLANK LINE between each item)\n\n"
            f"NEWS RANKING:\n"
            f"#1-2: World/Geopolitics | #3-4: Markets/Business | #5-6: Security/Cyber\n"
            f"#7-8: Science/Climate | #9: Positive | #10: WTF Fact\n"
            f"Max 2 AI items. Max 2 US items. 4+ global regions.\n"
            f"INCLUDE source name + link for every item.\n"
            f"IMPORTANT: Put a BLANK LINE between each numbered news item for readability.\n\n"
            f"### 📧 Inbox\n"
            f"[X] unread\n\n"
            f"### ✅ Tasks\n"
            f"- [Items]\n\n"
            f"### ⚡ Quote\n"
            f"\"[Quote]\" — [Author]\n\n"
            f"### 😈 Joke of the day\n"
            f"[One sharp line]\n\n"
            f"RULES: ZERO fabrication. Use ONLY provided data. Include SOURCE + LINK for news. "
            f"Skip empty sections. Under 3000 chars. Sharp, world-weary tone."
        )
        history = get_history(chat_id)
        reply = call_llm(text, llm_cfg, conversation_history=history,
                         system_prompt_override=enhanced_prompt)
        if reply:
            bot.send_message(chat_id, reply, reply_to=msg_id)
            # Generate and send voice briefing
            voice_path = _generate_voice_briefing(reply, chat_id)
            if voice_path:
                bot.send_voice(chat_id, voice_path, caption="CODEC Briefing (audio)")
                try:
                    os.unlink(voice_path)
                except Exception:
                    pass
            add_history(chat_id, "user", text)
            add_history(chat_id, "assistant", reply)
            save_to_memory(chat_id, text, reply)
            return

    if any(t in text_lower for t in _DEEP):
        bot.send_message(chat_id, "🔬 Running deep briefing crew... This takes 1-2 min.", reply_to=msg_id)
        reply = _run_deep_report(chat_id)
        if reply:
            bot.send_message(chat_id, reply, reply_to=msg_id)
            add_history(chat_id, "user", text)
            add_history(chat_id, "assistant", reply)
            save_to_memory(chat_id, text, reply)
            return

    # ── Try skills ───────────────────────────────────────────────────────
    skill_name, skill_result = try_skill(text)
    if skill_result:
        reply = skill_result
        log.info(f"⚡ Skill '{skill_name}' handled")
    else:
        # ── LLM fallback ────────────────────────────────────────────────
        history = get_history(chat_id)
        reply = call_llm(text, llm_cfg, conversation_history=history)

    if not reply:
        reply = "Sorry, I couldn't process that right now. Try again in a moment."

    # Truncate for Telegram (4096 char limit)
    max_len = min(tg_cfg.get("max_response_length", 4000), 4000)
    if len(reply) > max_len:
        reply = reply[:max_len - 3] + "..."

    # ── Send reply ───────────────────────────────────────────────────────
    success = bot.send_message(chat_id, reply, reply_to=msg_id)
    if success:
        audit(f"SENT chat={chat_id} text={reply[:100]}")
    else:
        log.error(f"Failed to send reply to chat {chat_id}")

    # ── Save history ─────────────────────────────────────────────────────
    add_history(chat_id, "user", text)
    add_history(chat_id, "assistant", reply)
    save_to_memory(chat_id, text, reply)


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    print(f"""
╔══════════════════════════════════════════════╗
║       CODEC Telegram  v{VERSION}               ║
║  Listening for Telegram messages...          ║
╚══════════════════════════════════════════════╝
    """)

    cfg = load_config()
    tg_cfg = get_telegram_config(cfg)
    llm_cfg = get_llm_config(cfg)

    token = tg_cfg["bot_token"]
    if not token:
        log.error("No bot_token in config. Set telegram.bot_token in ~/.codec/config.json")
        return

    bot = TelegramBot(token)

    # Verify token
    try:
        me = bot.get_me()
        log.info(f"Bot: @{me['username']} ({me['first_name']})")
    except Exception as e:
        log.error(f"Bot token verification failed: {e}")
        return

    audit("SERVICE_START")
    log.info(f"LLM: {llm_cfg['model']}")
    if tg_cfg.get("require_trigger"):
        log.info("Trigger required: 'Hey CODEC' or '/codec'")
    else:
        log.info("All DMs processed (no trigger required)")

    try:
        while True:
            updates = bot.get_updates(timeout=30)
            for update in updates:
                bot.offset = update["update_id"] + 1
                try:
                    process_message(bot, update, tg_cfg, llm_cfg)
                except Exception as e:
                    log.error(f"Message processing error: {e}")
    except KeyboardInterrupt:
        log.info("Shutting down...")
        audit("SERVICE_STOP")


if __name__ == "__main__":
    main()
