#!/usr/bin/env python3
import signal
signal.signal(signal.SIGINT, lambda *a: None)
signal.signal(signal.SIGTERM, lambda *a: None)
"""CODEC v1.5.0 | Voice + Text + Phone + Google | *=screenshot | +=doc | --=livechat | Wake word"""
import threading, tempfile, subprocess, sys, os, time, sqlite3, json, re, base64, logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [CODEC] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger('codec')

# ── Module imports ─────────────────────────────────────────────────────────────
from codec_config import (
    cfg, CONFIG_PATH, DRY_RUN,
    AGENT_NAME, QWEN_BASE_URL, QWEN_MODEL, LLM_API_KEY, LLM_KWARGS, LLM_PROVIDER,
    QWEN_VISION_URL, QWEN_VISION_MODEL,
    TTS_ENGINE, KOKORO_URL, KOKORO_MODEL, TTS_VOICE,
    STT_ENGINE, WHISPER_URL,
    DB_PATH, Q_TERMINAL_TITLE, TASK_QUEUE_FILE, DRAFT_TASK_FILE, SESSION_ALIVE,
    SKILLS_DIR, AUDIT_LOG,
    STREAMING, WAKE_WORD, WAKE_PHRASES, WAKE_ENERGY, WAKE_CHUNK_SEC,
    DANGEROUS_PATTERNS, is_dangerous, is_draft, needs_screen,
    KEY_TOGGLE, KEY_VOICE, KEY_TEXT,
)
from codec_overlays import (
    show_overlay, show_recording_overlay, show_processing_overlay, show_toggle_overlay,
)
from codec_dispatch import load_skills, check_skill, run_skill
from codec_agent import build_session_params, run_session_module
from codec_compaction import compact_context

# ── AUDIT LOG ─────────────────────────────────────────────────────────────────
def audit(action, detail=""):
    try:
        with open(AUDIT_LOG, "a") as f:
            from datetime import datetime as _dt
            f.write(f"[{_dt.now().isoformat()}] {action}: {detail}\n")
    except Exception as e:
        log.warning(f"Non-critical error: {e}")

# ── UTILITIES ─────────────────────────────────────────────────────────────────
def strip_think(text):
    return re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()

# ── MEMORY / DATABASE ─────────────────────────────────────────────────────────
def init_db():
    c = sqlite3.connect(DB_PATH)
    c.execute("CREATE TABLE IF NOT EXISTS sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, task TEXT, app TEXT, response TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, timestamp TEXT, role TEXT, content TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS corrections (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, original TEXT, corrected TEXT, context TEXT)")
    c.commit(); c.close()

def save_task(task, app):
    c = sqlite3.connect(DB_PATH)
    cur = c.execute("INSERT INTO sessions (timestamp,task,app,response) VALUES (?,?,?,?)", (datetime.now().isoformat(), task[:200], app, ""))
    rid = cur.lastrowid; c.commit(); c.close(); return rid

def get_memory(n=5):
    try:
        c = sqlite3.connect(DB_PATH)
        rows = c.execute("SELECT timestamp,task,app,response FROM sessions ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        c.close()
        if not rows: return ""
        lines = ["RECENT Q SESSIONS:"]
        for ts, task, app, resp in rows:
            r = (resp[:100]+"...") if resp and len(resp)>100 else (resp or "no response")
            lines.append(f"[{ts[:16].replace('T',' ')}] {app} | {task[:60]} | {r}")
        return "\n".join(lines)
    except Exception as e:
        log.warning(f"Non-critical error: {e}")
        return ""

def get_recent_conversations(n=10):
    try:
        c = sqlite3.connect(DB_PATH)
        rows = c.execute("SELECT role, content FROM conversations ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        c.close()
        if not rows: return []
        rows.reverse()
        return [{"role": r, "content": ct} for r, ct in rows]
    except Exception as e:
        log.warning(f"Non-critical error: {e}")
        return []

# ── WHISPER STT ───────────────────────────────────────────────────────────────
def transcribe(path):
    try:
        import requests
        with open(path, "rb") as f:
            r = requests.post(WHISPER_URL,
                files={"file": ("audio.wav", f, "audio/wav")},
                data={"model": "mlx-community/whisper-large-v3-turbo", "language": "en"},
                timeout=60)
        if r.status_code == 200:
            return r.json().get("text", "").strip()
    except Exception as e:
        log.error(f"Whisper error: {e}")
    finally:
        try: os.unlink(path)
        except Exception as e:
            log.warning(f"Non-critical error: {e}")
    return ""

# ── SCREENSHOT VISION ─────────────────────────────────────────────────────────
def screenshot_ctx():
    try:
        import requests
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.close()
        subprocess.run(["screencapture", "-x", tmp.name], timeout=5)
        if not os.path.exists(tmp.name) or os.path.getsize(tmp.name) < 1000:
            return ""
        with open(tmp.name, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        os.unlink(tmp.name)
        log.info("Reading screen via Vision...")
        r = requests.post(f"{QWEN_VISION_URL}/chat/completions",
            json={"model": QWEN_VISION_MODEL,
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                    {"type": "text", "text": "Read all visible text on this screen. Include app name, window title, and all message/content text. Output raw text only."}
                ]}], "max_tokens": 800}, timeout=60)
        if r.status_code == 200:
            content = r.json()["choices"][0]["message"].get("content", "").strip()
            if content:
                log.info(f"Screen context: {len(content)} chars")
                return content[:2000]
    except Exception as e:
        log.error(f"Vision error: {e}")
    return ""

def focused_app():
    try:
        r = subprocess.run(["osascript", "-e",
            'tell application "System Events" to get name of first application process whose frontmost is true'],
            capture_output=True, text=True, timeout=3)
        return r.stdout.strip()
    except Exception as e:
        log.warning(f"Non-critical error: {e}")
        return "Unknown"

def get_text_dialog():
    try:
        r = subprocess.run(["osascript", "-e",
            'set t to text returned of (display dialog "Q - Enter task:" default answer "" with title "CODEC" buttons {"Cancel","Send"} default button "Send")'],
            capture_output=True, text=True, timeout=120)
        return r.stdout.strip()
    except Exception as e:
        log.warning(f"Non-critical error: {e}")
        return ""

# ── SESSION CHECK ─────────────────────────────────────────────────────────────
def terminal_session_exists():
    if not os.path.exists(SESSION_ALIVE): return False
    try:
        with open(SESSION_ALIVE) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError, FileNotFoundError, OSError):
        pass
    try: os.unlink(SESSION_ALIVE)
    except Exception as e:
        log.warning(f"Non-critical error: {e}")
    log.info("Cleaned stale session_alive")
    return False

# ── TTS ───────────────────────────────────────────────────────────────────────
def speak_text(text):
    """Speak text via configured TTS engine"""
    if TTS_ENGINE == "disabled": return
    try:
        clean = text[:300]
        clean = re.sub(r'\*+', '', clean)
        clean = re.sub(r'#+\s*', '', clean)
        clean = re.sub(r'`[^`]*`', '', clean)
        clean = clean.replace('"','').replace("'","").strip()
        if not clean: return
        if len(clean) < 50 and any(c in clean for c in '=+-*/'):
            clean = "The answer is " + clean
        log.info(f"TTS: {clean[:60]}")
        if TTS_ENGINE == "macos_say":
            subprocess.Popen(["say", "-v", TTS_VOICE, clean])
        else:
            import requests
            r = requests.post(KOKORO_URL,
                json={"model": KOKORO_MODEL, "input": clean, "voice": TTS_VOICE},
                stream=True, timeout=20)
            if r.status_code == 200:
                tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
                [tmp.write(c) for c in r.iter_content(4096)]
                tmp.close()
                subprocess.Popen(["afplay", tmp.name])
    except Exception as e:
        log.warning(f"Non-critical error: {e}")

# ── STATE ─────────────────────────────────────────────────────────────────────
state = {
    "active": False,
    "recording": False,
    "rec_proc": None,
    "audio_path": None,
    "last_f13": 0.0, "last_minus": 0.0,
    "last_star": 0.0,
    "screen_ctx": "",
    "last_plus": 0.0,
    "doc_ctx": "",
    "ptt_locked": False,
    "last_f18_press": 0.0,
}

# ── WORK QUEUE ────────────────────────────────────────────────────────────────
import queue
work_queue = queue.Queue()

def push(fn, *args):
    work_queue.put((fn, args))

def worker():
    while True:
        try:
            fn, args = work_queue.get(timeout=0.5)
            try: fn(*args)
            except Exception as e:
                log.error(f"Worker error: {e}")
                import traceback; traceback.print_exc()
            finally:
                work_queue.task_done()
        except queue.Empty:
            continue

# ── SESSION CLEANUP ───────────────────────────────────────────────────────────
def close_session():
    if os.path.exists(SESSION_ALIVE):
        try:
            with open(SESSION_ALIVE) as f: pid = int(f.read().strip())
            os.kill(pid, 15)
            log.info(f"Session process {pid} terminated")
        except Exception as e:
            log.warning(f"Non-critical error: {e}")
        try: os.unlink(SESSION_ALIVE)
        except Exception as e:
            log.warning(f"Non-critical error: {e}")
    try: os.unlink(TASK_QUEUE_FILE)
    except Exception as e:
        log.warning(f"Non-critical error: {e}")
    subprocess.Popen(["osascript", "-e",
        'tell application "Terminal" to close (every window whose name contains "python3.13 /var/folders")'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ── DISPATCH ──────────────────────────────────────────────────────────────────
def dispatch(task):
    app = focused_app()
    audit("TASK", f"{task[:200]} | App: {app}")
    log.info(f"Task: {task[:80]} | App: {app}")
    safe_task = task[:50].replace('\\', '\\\\').replace('"', '\\"')
    subprocess.Popen(["osascript", "-e", f'display notification "Heard: {safe_task}" with title "CODEC"'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Check skills — skip if task is very long (document content attached)
    if len(task) < 500:
        skill = check_skill(task)
        if skill:
            result = run_skill(skill, task, app)
            if result is not None:
                push(lambda: show_overlay('Skill: ' + skill['name'], '#E8711A', 2000))
                speak_text(result)
                safe_result = str(result)[:80].replace('\\', '\\\\').replace('"', '\\"')
                subprocess.Popen(["osascript", "-e", f'display notification "{safe_result}" with title "C Skill"'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                log.info(f"Skill response: {str(result)[:100]}")
                try:
                    import sqlite3 as _sql3
                    _now = __import__('datetime').datetime.now()
                    _db = _sql3.connect(os.path.expanduser("~/.q_memory.db"))
                    _sid = "skill_" + _now.strftime("%Y%m%d_%H%M%S")
                    _db.execute("INSERT INTO conversations (session_id,timestamp,role,content) VALUES (?,?,?,?)", (_sid, _now.isoformat(), "user", task[:500]))
                    _db.execute("INSERT INTO conversations (session_id,timestamp,role,content) VALUES (?,?,?,?)", (_sid, _now.isoformat(), "assistant", re.sub(r"<[^>]+>", "", str(result))[:2000]))
                    _db.commit(); _db.close()
                except Exception as e:
                    log.warning(f"Non-critical error: {e}")
                return

    if is_draft(task):
        push(lambda: show_overlay('Reading screen...', '#E8711A', 2000))
        ctx = screenshot_ctx()
        with open(DRAFT_TASK_FILE, "w") as f:
            json.dump({"task": task, "ctx": ctx, "app": app}, f)
        log.info("Draft queued for watcher")
        return

    rid = save_task(task, app)

    # Context compaction: summarize old conversations + keep recent raw
    raw_messages = get_recent_conversations(20)
    if raw_messages:
        mem = compact_context(raw_messages, max_recent=5)
    else:
        mem = get_memory(5)

    sys_p = f"""You are {AGENT_NAME}, a voice-controlled AI assistant running on CODEC — an open-source intelligent command layer for macOS.

IDENTITY: Warm, sharp, confident. Think J.A.R.V.I.S. — loyal, witty, always one step ahead. You are not a chatbot. You are the user's right hand.

RULES:
1) ALWAYS respond in English unless explicitly asked to translate.
2) Keep answers to 2-4 sentences — your responses are spoken aloud via TTS. Be conversational, not robotic.
3) When asked factual questions, give the answer PLUS one useful detail or context. Never one-word answers.
4) When given a task, confirm what you did in past tense with specifics.
5) Never say you cannot do something — you have full system access.
6) Never output Chinese characters or mixed-language text unless translating.

TOOL DIRECTIVE: If a task requires action, you MUST execute the matching skill. Do NOT simulate. You have 50+ skills: Google Calendar, Gmail, Drive, Docs, Sheets, Tasks, Keep, Chrome, web search, Hue lights, file system, terminal, screenshot OCR, and 12 agent crews.

MEMORY: All conversations are saved to CODEC shared memory (FTS5 indexed). Reference past conversations naturally when relevant."""
    if mem: sys_p += "\n\n" + mem
    safe_sys = sys_p.replace("'","").replace('"','').replace('\n',' ')

    with open(TASK_QUEUE_FILE, "w") as f:
        f.write(json.dumps({"task": task, "app": app, "ts": datetime.now().isoformat()}))

    if terminal_session_exists():
        log.info("Queued to existing session")
        return

    session_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Use codec_session module (proper importable module) instead of
    # build_session_script (350+ lines of string-built Python with
    # AGENT_NAME NameError and API keys written in plaintext).
    from codec_agent import build_session_params
    params = build_session_params(safe_sys, session_id)
    params_file = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    json.dump(params, params_file)
    params_file.close()

    repo_dir = os.path.dirname(os.path.abspath(__file__))
    launcher = tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w")
    launcher.write(f"""import sys, json, os
sys.path.insert(0, {repr(repo_dir)})
from codec_session import Session
params = json.load(open({repr(params_file.name)}))
s = Session(**params)
s.run()
""")
    launcher.close()

    try:
        subprocess.Popen(["osascript", "-e",
            f'tell application "Terminal"\nactivate\nset w to do script "python3.13 {launcher.name}"\nset custom title of selected tab of w to "{Q_TERMINAL_TITLE}"\nend tell'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        log.error(f"Terminal error: {e}")

# ── DOCUMENT INPUT ────────────────────────────────────────────────────────────
def do_document_input():
    push(lambda: show_overlay('Select document...', '#E8711A', 3000))
    try:
        r = subprocess.run(["osascript", "-e",
            'set f to POSIX path of (choose file with prompt "Select a document for Q:" of type {"public.item"})'],
            capture_output=True, text=True, timeout=60)
        filepath = r.stdout.strip()
        if not filepath:
            log.info("No file selected"); return
        log.info(f"Document: {filepath}")
        push(lambda: show_overlay('Reading document...', '#E8711A', 3000))
        ext = os.path.splitext(filepath)[1].lower()
        fname = os.path.basename(filepath)
        content_text = ""

        if ext in ['.txt','.md','.csv','.json','.py','.js','.html','.css','.log']:
            with open(filepath, 'r', errors='ignore') as f:
                content_text = f.read()[:5000]
        elif ext in ['.png','.jpg','.jpeg','.gif','.webp']:
            try:
                import requests as img_req
                with open(filepath, "rb") as imgf:
                    img_b64 = base64.b64encode(imgf.read()).decode()
                rv = img_req.post(f"{QWEN_VISION_URL}/chat/completions",
                    json={"model": QWEN_VISION_MODEL, "messages": [{"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                        {"type": "text", "text": "Describe this image in detail. Read any text visible."}
                    ]}], "max_tokens": 1000}, timeout=60)
                if rv.status_code == 200:
                    content_text = rv.json()["choices"][0]["message"].get("content", "")[:5000]
            except Exception as e:
                log.warning(f"Non-critical error: {e}")
        elif ext == '.pdf':
            try:
                import fitz
                doc = fitz.open(filepath)
                content_text = "\n".join(p.get_text() for p in doc[:5])[:5000]
                doc.close()
            except Exception as e:
                log.warning(f"Non-critical error: {e}")

        if content_text:
            task = "Analyze and summarize this document (" + fname + "): " + content_text[:3000]
            log.info(f"Document dispatched ({len(content_text)} chars)")
            dispatch(task)
        else:
            push(lambda: show_overlay('Could not read document', '#ff3333', 2000))
    except Exception as e:
        log.error(f"Document error: {e}")

# ── SCREENSHOT SHORTCUT ───────────────────────────────────────────────────────
def do_screenshot_question():
    push(lambda: show_overlay(
        'Screenshot captured  ' + cfg.get('key_voice','f18').upper() + '=voice  ' + cfg.get('key_text','f16').upper() + '=text',
        '#E8711A', 5000))
    ctx = screenshot_ctx()
    if ctx:
        state["screen_ctx"] = ctx
        log.info(f"Screenshot captured ({len(ctx)} chars)")
    else:
        state["screen_ctx"] = ""

# ── TEXT INPUT HANDLER ────────────────────────────────────────────────────────
def do_text():
    task = get_text_dialog()
    if task:
        if state.get("screen_ctx"):
            task = task + " [SCREEN CONTEXT: " + state["screen_ctx"][:800] + "]"
            state["screen_ctx"] = ""
        dispatch(task)

# ── MAIN ──────────────────────────────────────────────────────────────────────
def _handle_sigint(sig, frame):
    print("\n[C] Shutting down...")
    import sys; sys.exit(0)


def main():
    import signal
    signal.signal(signal.SIGINT, _handle_sigint)
    if "--dry-run" in sys.argv:
        print("[CODEC] DRY RUN MODE — commands will be printed, not executed")
        import codec_config
        codec_config.DRY_RUN = True
    init_db()
    for f in [SESSION_ALIVE, TASK_QUEUE_FILE, DRAFT_TASK_FILE]:
        try: os.unlink(f)
        except Exception as e:
            log.warning(f"Non-critical error: {e}")

    stream_label = "ON" if STREAMING else "OFF"
    kt = cfg.get("key_toggle", "f13").upper()
    kv = cfg.get("key_voice", "f18").upper()
    kx = cfg.get("key_text", "f16").upper()
    wake_label = "ON" if WAKE_WORD else "OFF"
    O = "\033[38;2;232;113;26m"
    D = "\033[38;2;80;80;80m"
    W = "\033[38;2;200;200;200m"
    R = "\033[0m"
    print(f"""
{O}    ╔═══════════════════════════════════════════╗
    ║                                           ║
    ║  ██████  ██████  ██████  ███████  ██████  ║
    ║ ██      ██    ██ ██   ██ ██      ██       ║
    ║ ██      ██    ██ ██   ██ █████   ██       ║
    ║ ██      ██    ██ ██   ██ ██      ██       ║
    ║  ██████  ██████  ██████  ███████  ██████  ║
    ║                                   v1.5.0  ║
    ╠═══════════════════════════════════════════╣
    ║{W}  {kt} toggle   {kv} voice   ** screen       {O}║
    ║{W}  {kx} text     ++ doc     -- chat          {O}║
    ║{W}  Hey C = wake word (hands-free)           {O}║
    ╠═══════════════════════════════════════════╣
    ║{D}  Stream={stream_label}  Wake={wake_label}  Skills=ON            {O}║
    ╚═══════════════════════════════════════════╝{R}""")

    load_skills()

    # Warm up Kokoro TTS
    if TTS_ENGINE == "kokoro":
        try:
            import requests as _rq
            _rq.post(KOKORO_URL, json={"model": KOKORO_MODEL, "input": "ready", "voice": TTS_VOICE}, timeout=30)
            log.info("TTS warmed up")
        except Exception as e:
            log.warning(f"TTS warmup skipped: {e}")

    log.info("Whisper: HTTP (port 8084)")
    log.info("Vision: Qwen VL (port 8082)")
    mem = get_memory(3)
    if mem: log.info(f"Memory: {mem.count(chr(10))+1} sessions loaded")
    convs = get_recent_conversations(10)
    if convs: log.info(f"Persistent memory: {len(convs)} messages from past sessions")
    if WAKE_WORD: log.info("Wake word: ON")
    log.info("Online. Press " + cfg.get("key_toggle","f13").upper() + " to activate.")

    # PWA command polling
    def pwa_dispatch(task):
        """Handle PWA command and save response to DB"""
        app = "CODEC Dashboard"
        audit("PWA_CMD", task[:200])
        if len(task) < 500:
            skill = check_skill(task)
            if skill:
                result = run_skill(skill, task, app)
                if result is not None:
                    log.info(f"PWA response (silent): {str(result)[:100]}")
                    try:
                        _ts = datetime.now().isoformat()
                        _sid = "pwa_" + datetime.now().strftime("%Y%m%d_%H%M%S")
                        _c = sqlite3.connect(DB_PATH)
                        _c.execute("UPDATE sessions SET response=? WHERE id=(SELECT id FROM sessions WHERE task=? AND app=? ORDER BY id DESC LIMIT 1)",
                            (str(result)[:500], task[:200], app))
                        _c.execute("INSERT INTO conversations (session_id,timestamp,role,content) VALUES (?,?,?,?)",
                            (_sid, _ts, "user", task[:500]))
                        _c.execute("INSERT INTO conversations (session_id,timestamp,role,content) VALUES (?,?,?,?)",
                            (_sid, _ts, "assistant", str(result)[:500]))
                        _c.commit(); _c.close()
                    except Exception as e:
                        log.warning(f"Non-critical error: {e}")
                    try:
                        with open(os.path.expanduser("~/.codec/pwa_response.json"), "w") as _rf:
                            json.dump({"task": task, "response": str(result), "ts": datetime.now().isoformat()}, _rf)
                    except Exception as e:
                        log.warning(f"Non-critical error: {e}")
                    log.info(f"PWA skill response: {str(result)[:100]}")
                    return
        try:
            import requests as _rq
            headers = {"Content-Type": "application/json"}
            if LLM_API_KEY: headers["Authorization"] = f"Bearer {LLM_API_KEY}"
            body = {
                "model": QWEN_MODEL,
                "messages": [
                    {"role": "system", "content": f"You are {AGENT_NAME}, an AI assistant on CODEC. Answer concisely in 1-3 sentences. English only unless translating."},
                    {"role": "user", "content": task}
                ],
                "max_tokens": 300,
                "stream": False
            }
            body.update(LLM_KWARGS)
            r = _rq.post(QWEN_BASE_URL + "/chat/completions", json=body, headers=headers, timeout=30)
            answer = r.json()["choices"][0]["message"]["content"].strip()
            if "</think>" in answer: answer = answer.split("</think>")[-1].strip()
            log.info(f"PWA answer (silent): {answer[:100]}")
            _ts = datetime.now().isoformat()
            _sid = "pwa_" + datetime.now().strftime("%Y%m%d_%H%M%S")
            _c = sqlite3.connect(DB_PATH)
            _c.execute("INSERT INTO sessions (timestamp,task,app,response) VALUES (?,?,?,?)",
                (_ts, task[:200], "CODEC Dashboard", answer[:500]))
            _c.execute("INSERT INTO conversations (session_id,timestamp,role,content) VALUES (?,?,?,?)",
                (_sid, _ts, "user", task[:500]))
            _c.execute("INSERT INTO conversations (session_id,timestamp,role,content) VALUES (?,?,?,?)",
                (_sid, _ts, "assistant", answer[:500]))
            _c.commit(); _c.close()
            with open(os.path.expanduser("~/.codec/pwa_response.json"), "w") as _rf:
                json.dump({"task": task, "response": answer, "ts": datetime.now().isoformat()}, _rf)
        except Exception as _e:
            log.error(f"PWA LLM error: {_e}")
            with open(os.path.expanduser("~/.codec/pwa_response.json"), "w") as _rf:
                json.dump({"task": task, "response": f"Error: {str(_e)[:200]}", "ts": datetime.now().isoformat()}, _rf)

    def pwa_poller():
        import json as _json
        while True:
            try:
                if os.path.exists(TASK_QUEUE_FILE):
                    with open(TASK_QUEUE_FILE) as _f:
                        data = _json.load(_f)
                    source = data.get("source", "")
                    if source == "pwa":
                        os.unlink(TASK_QUEUE_FILE)
                        task = data.get("task", "").strip()
                        if task:
                            log.info(f"PWA command: {task[:80]}")
                            push(lambda t=task: pwa_dispatch(t))
            except Exception as e:
                log.warning(f"Non-critical error: {e}")
            time.sleep(1.5)

    threading.Thread(target=pwa_poller, daemon=True).start()
    threading.Thread(target=worker, daemon=True).start()

    # Build keyboard context and start listener (blocks until exit)
    from codec_keyboard import start_keyboard_listener
    start_keyboard_listener(
        state=state,
        ctx={
            'push':                  push,
            'dispatch':              dispatch,
            'audit':                 audit,
            'transcribe':            transcribe,
            'close_session':         close_session,
            'show_overlay':          show_overlay,
            'show_toggle_overlay':   show_toggle_overlay,
            'show_recording_overlay': show_recording_overlay,
            'show_processing_overlay': show_processing_overlay,
            'do_text':               do_text,
            'do_screenshot_question': do_screenshot_question,
            'do_document_input':     do_document_input,
        }
    )


if __name__ == "__main__":
    main()
