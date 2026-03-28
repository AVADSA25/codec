#!/usr/bin/env python3
import signal
signal.signal(signal.SIGINT, lambda *a: None)
signal.signal(signal.SIGTERM, lambda *a: None)
"""CODEC v1.3.0 | Voice + Text + Phone + Google | *=screenshot | +=doc | --=livechat | Wake word"""
import threading, tempfile, subprocess, sys, os, time, sqlite3, json, re, base64
from datetime import datetime
from pynput import keyboard

# ── CONFIG (load from ~/.codec/config.json or use defaults) ───────────────────
CONFIG_PATH = os.path.expanduser("~/.codec/config.json")
DRY_RUN = False
_cfg = {}
if os.path.exists(CONFIG_PATH):
    try:
        with open(CONFIG_PATH) as _f: _cfg = json.load(_f)
        print(f"[C] Config loaded from {CONFIG_PATH}")
    except: pass

# LLM
QWEN_BASE_URL     = _cfg.get("llm_base_url", "http://localhost:8081/v1")
QWEN_MODEL        = _cfg.get("llm_model", "mlx-community/Qwen3.5-35B-A3B-4bit")
LLM_API_KEY       = _cfg.get("llm_api_key", "")
LLM_KWARGS        = _cfg.get("llm_kwargs", {})
LLM_PROVIDER      = _cfg.get("llm_provider", "mlx")

# Vision (optional — only for local MLX setups)
QWEN_VISION_URL   = _cfg.get("vision_base_url", "http://localhost:8082/v1")
QWEN_VISION_MODEL = _cfg.get("vision_model", "mlx-community/Qwen2.5-VL-7B-Instruct-4bit")

# TTS
TTS_ENGINE        = _cfg.get("tts_engine", "kokoro")
KOKORO_URL        = _cfg.get("tts_url", "http://localhost:8085/v1/audio/speech")
KOKORO_MODEL      = _cfg.get("tts_model", "mlx-community/Kokoro-82M-bf16")
TTS_VOICE         = _cfg.get("tts_voice", "am_adam")
# ── AUDIT LOG ─────────────────────────────────────────────────────────────────
AUDIT_LOG = os.path.expanduser("~/.codec/audit.log")
def audit(action, detail=""):
    try:
        with open(AUDIT_LOG, "a") as f:
            from datetime import datetime as _dt
            f.write(f"[{_dt.now().isoformat()}] {action}: {detail}\n")
    except: pass

# ── DANGEROUS COMMAND SAFETY ──────────────────────────────────────────────────
DANGEROUS_PATTERNS = [
    "rm -rf", "rm -r /", "rmdir", "sudo rm", "mkfs", "dd if=",
    "shutdown", "reboot", "halt", "killall", "pkill",
    "sudo", "chmod 777", "chown", "> /dev/", ":(){ :|:& };:",
    "curl | bash", "wget | bash", "curl | sh", "wget | sh",
    "defaults delete", "diskutil erase", "networksetup",
    "osascript -e \'tell application \"System Events\"",
]
REQUIRE_CONFIRM = _cfg.get("require_confirmation", True)

def is_dangerous(cmd):
    cmd_lower = cmd.lower()
    return any(p in cmd_lower for p in DANGEROUS_PATTERNS)


# STT
STT_ENGINE        = _cfg.get("stt_engine", "whisper_http")
WHISPER_URL       = _cfg.get("stt_url", "http://localhost:8084/v1/audio/transcriptions")

# Paths
DB_PATH            = os.path.expanduser("~/.q_memory.db")
Q_TERMINAL_TITLE   = "Q -- CODEC Session"
TASK_QUEUE_FILE    = "/tmp/q_task_queue.txt"
DRAFT_TASK_FILE    = "/tmp/q_draft_task.json"
SESSION_ALIVE      = "/tmp/q_session_alive"
SKILLS_DIR         = os.path.expanduser("~/.codec/skills")

# Features
STREAMING          = _cfg.get("streaming", True)
WAKE_WORD          = _cfg.get("wake_word_enabled", True)
WAKE_PHRASES       = _cfg.get("wake_phrases", ['hey', 'aq', 'eq', 'iq', 'okay q', 'a q', 'hey c', 'hey cueue'])
WAKE_ENERGY        = _cfg.get("wake_energy", 200)
WAKE_CHUNK_SEC     = _cfg.get("wake_chunk_sec", 3.0)
DRAFT_KEYWORDS_CFG = _cfg.get("draft_keywords", [])

# Map config key names to pynput keys
def _resolve_key(name):
    name = name.lower().strip()
    if name.startswith('f') and name[1:].isdigit():
        return getattr(keyboard.Key, name, None)
    if len(name) == 1:
        return name
    return getattr(keyboard.Key, name, None)

KEY_TOGGLE = _resolve_key(_cfg.get("key_toggle", "f13"))
KEY_VOICE  = _resolve_key(_cfg.get("key_voice", "f18"))
KEY_TEXT   = _resolve_key(_cfg.get("key_text", "f16"))

# ── UTILITIES ─────────────────────────────────────────────────────────────────
def strip_think(text):
    return re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()

def show_overlay(text, color="#E8711A", duration=2500):
    d = f"root.after({duration}, root.destroy)" if duration else ""
    s = f"""
import tkinter as tk
root=tk.Tk()
root.overrideredirect(True)
root.attributes('-topmost',True)
root.attributes('-alpha',0.95)
root.configure(bg='#0a0a0a')
sw=root.winfo_screenwidth()
sh=root.winfo_screenheight()
w,h=520,56
x=(sw-w)//2
y=sh-130
root.geometry(f'{{w}}x{{h}}+{{x}}+{{y}}')
c=tk.Canvas(root,bg='#0a0a0a',highlightthickness=0,width=w,height=h)
c.pack()
c.create_rectangle(1,1,w-1,h-1,outline='{color}',width=1)
c.create_text(w//2,h//2,text='{text}',fill='{color}',font=('Helvetica',13))
{d}
root.mainloop()
"""
    subprocess.Popen([sys.executable, "-c", s], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def show_recording_overlay(key_label="F18"):
    s = """
import tkinter as tk
root=tk.Tk()
root.overrideredirect(True)
root.attributes('-topmost',True)
root.attributes('-alpha',0.95)
root.configure(bg='#0a0a0a')
sw=root.winfo_screenwidth()
sh=root.winfo_screenheight()
w,h=440,78
x=(sw-w)//2
y=sh-130
root.geometry(f'{w}x{h}+{x}+{y}')
cv=tk.Canvas(root,bg='#0a0a0a',highlightthickness=0,width=w,height=h)
cv.pack()
cv.create_rectangle(1,1,w-1,h-1,outline='#E8711A',width=1)
dot=cv.create_oval(14,29,27,42,fill='#ff3b3b',outline='')
cv.create_text(w//2+8,42,text='\U0001f3a4  Recording — release """ + key_label + """ to send',fill='#eeeeee',font=('Helvetica',13))
on=[True]
def pulse():
    on[0]=not on[0]
    cv.itemconfig(dot,fill='#ff3b3b' if on[0] else '#550000')
    root.after(400,pulse)
pulse()
root.mainloop()
"""
    return subprocess.Popen([sys.executable, "-c", s], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def show_processing_overlay(text="Transcribing...", duration=4000):
    s = f"""
import tkinter as tk
root=tk.Tk()
root.overrideredirect(True)
root.attributes('-topmost',True)
root.attributes('-alpha',0.95)
root.configure(bg='#0a0a0a')
sw=root.winfo_screenwidth()
sh=root.winfo_screenheight()
w,h=260,54
x=(sw-w)//2
y=sh-130
root.geometry(f'{{w}}x{{h}}+{{x}}+{{y}}')
cv=tk.Canvas(root,bg='#0a0a0a',highlightthickness=0,width=w,height=h)
cv.pack()
cv.create_rectangle(1,1,w-1,h-1,outline='#00aaff',width=1)
cv.create_text(w//2,h//2,text='\u26a1 {text}',fill='#00aaff',font=('Helvetica',13))
root.after({duration},root.destroy)
root.mainloop()
"""
    subprocess.Popen([sys.executable, "-c", s], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def show_toggle_overlay(is_on, shortcuts=""):
    color = '#E8711A' if is_on else '#ff3333'
    label = 'C O D E C' if is_on else 'S I G N I N G   O U T'
    dur = 3000 if is_on else 1500
    # Play sound
    import threading
    snd = '/System/Library/Sounds/Blow.aiff' if is_on else '/System/Library/Sounds/Funk.aiff'
    threading.Thread(target=lambda: subprocess.run(['afplay', snd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL), daemon=True).start()
    s = f"""
import tkinter as tk
root=tk.Tk()
root.overrideredirect(True)
root.attributes('-topmost',True)
root.attributes('-alpha',0.95)
root.configure(bg='#0a0a0a')
sw=root.winfo_screenwidth()
sh=root.winfo_screenheight()
w,h=440,78
x=(sw-w)//2
y=sh-140
root.geometry(f'{{w}}x{{h}}+{{x}}+{{y}}')
cv=tk.Canvas(root,bg='#0a0a0a',highlightthickness=0,width=w,height=h)
cv.pack()
cv.create_rectangle(1,1,w-1,h-1,outline='{color}',width=1)
cv.create_text(w//2,39 if not '{shortcuts}' else 24,text='{label}',fill='{color}',font=('Helvetica',18,'bold'))
if '{shortcuts}': cv.create_text(w//2,55,text='{shortcuts}',fill='#aaaaaa',font=('Helvetica',13))
root.after({dur},root.destroy)
root.mainloop()
"""
    subprocess.Popen([sys.executable, "-c", s], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ── DETECTION ────────────────────────────────────────────────────────────────
DRAFT_KEYWORDS = [
    "draft","reply","rephrase","rewrite","fix my","say that","respond",
    "write a","write an","compose","tell them","tell him","tell her",
    "say i","say we","message saying","email saying","correct my",
    "fix this","improve this","polish this","type in","type this",
    "please say","please write","please type","write reply",
    "post saying","comment saying","tweet saying","and say","to say"
]
SCREEN_KEYWORDS = [
    "look at my screen","look at the screen","what's on my screen",
    "whats on my screen","read my screen","see my screen","screen",
    "what am i looking at","what do you see","look at this"
]

def is_draft(t): return any(k in t.lower() for k in DRAFT_KEYWORDS)
def needs_screen(t): return any(k in t.lower() for k in SCREEN_KEYWORDS)

# ── MEMORY ────────────────────────────────────────────────────────────────────
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
    except: return ""

def get_recent_conversations(n=10):
    try:
        c = sqlite3.connect(DB_PATH)
        rows = c.execute("SELECT role, content FROM conversations ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        c.close()
        if not rows: return []
        rows.reverse()
        return [{"role": r, "content": ct} for r, ct in rows]
    except: return []

# ── SKILLS ────────────────────────────────────────────────────────────────────
loaded_skills = []

def load_skills():
    global loaded_skills
    loaded_skills = []
    if not os.path.isdir(SKILLS_DIR): return
    for fname in os.listdir(SKILLS_DIR):
        if fname.startswith('_') or not fname.endswith('.py'): continue
        path = os.path.join(SKILLS_DIR, fname)
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(fname[:-3], path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, 'SKILL_TRIGGERS') and hasattr(mod, 'run'):
                loaded_skills.append({
                    'name': getattr(mod, 'SKILL_NAME', fname[:-3]),
                    'triggers': mod.SKILL_TRIGGERS,
                    'run': mod.run,
                })
                print(f"[C] Skill loaded: {fname[:-3]}")
        except Exception as e:
            print(f"[C] Skill error ({fname}): {e}")

def check_skill(task):
    low = task.lower()
    for skill in loaded_skills:
        if any(trigger in low for trigger in skill['triggers']):
            return skill
    return None

def run_skill(skill, task, app=""):
    try:
        result = skill['run'](task, app)
        return result
    except Exception as e:
        return f"Skill error: {e}"

# ── WHISPER VIA HTTP ──────────────────────────────────────────────────────────
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
        print(f"[C] Whisper error: {e}")
    finally:
        try: os.unlink(path)
        except: pass
    return ""

# ── SCREENSHOT VISION ────────────────────────────────────────────────────────
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
        print("[C] Reading screen via Vision...")
        r = requests.post(f"{QWEN_VISION_URL}/chat/completions",
            json={"model": QWEN_VISION_MODEL,
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                    {"type": "text", "text": "Read all visible text on this screen. Include app name, window title, and all message/content text. Output raw text only."}
                ]}], "max_tokens": 800}, timeout=60)
        if r.status_code == 200:
            content = r.json()["choices"][0]["message"].get("content", "").strip()
            if content:
                print(f"[C] Screen context: {len(content)} chars")
                return content[:2000]
    except Exception as e:
        print(f"[C] Vision error: {e}")
    return ""

def focused_app():
    try:
        r = subprocess.run(["osascript", "-e",
            'tell application "System Events" to get name of first application process whose frontmost is true'],
            capture_output=True, text=True, timeout=3)
        return r.stdout.strip()
    except: return "Unknown"

def get_text_dialog():
    try:
        r = subprocess.run(["osascript", "-e",
            'set t to text returned of (display dialog "Q - Enter task:" default answer "" with title "CODEC" buttons {"Cancel","Send"} default button "Send")'],
            capture_output=True, text=True, timeout=120)
        return r.stdout.strip()
    except: return ""

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
    except: pass
    print("[C] Cleaned stale session_alive")
    return False

# ── TTS HELPER ────────────────────────────────────────────────────────────────
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
        print(f"[TTS] Speaking: {clean[:60]}")
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
    except: pass

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
}

# ── WORK QUEUE ────────────────────────────────────────────────────────────────
work_queue = []
work_lock = threading.Lock()

def push(fn, *args):
    with work_lock:
        work_queue.append((fn, args))

def worker():
    while True:
        item = None
        with work_lock:
            if work_queue:
                item = work_queue.pop(0)
        if item:
            fn, args = item
            try: fn(*args)
            except Exception as e:
                print(f"[C] Error: {e}")
                import traceback; traceback.print_exc()
        else:
            time.sleep(0.05)

# ── BUILD SESSION SCRIPT ─────────────────────────────────────────────────────
def build_session_script(safe_sys, session_id):
    L = []
    L.append("import os, sys, requests, json, time, sqlite3, tempfile, subprocess, re, select, atexit, base64")
    L.append("from datetime import datetime")
    L.append("")
    L.append("QWEN_BASE_URL = " + repr(QWEN_BASE_URL))
    L.append("QWEN_MODEL = " + repr(QWEN_MODEL))
    L.append("QWEN_VISION_URL = " + repr(QWEN_VISION_URL))
    L.append("QWEN_VISION_MODEL = " + repr(QWEN_VISION_MODEL))
    L.append("TTS_VOICE = " + repr(TTS_VOICE))
    L.append("LLM_API_KEY = " + repr(LLM_API_KEY))
    L.append("LLM_KWARGS = " + repr(LLM_KWARGS))
    L.append("LLM_PROVIDER = " + repr(LLM_PROVIDER))
    L.append("TTS_ENGINE = " + repr(TTS_ENGINE))
    L.append("KOKORO_URL = " + repr(KOKORO_URL))
    L.append("KOKORO_MODEL = " + repr(KOKORO_MODEL))
    L.append("DB_PATH = os.path.expanduser(" + repr(DB_PATH) + ")")
    L.append("TASK_QUEUE = " + repr(TASK_QUEUE_FILE))
    L.append("SESSION_ALIVE = " + repr(SESSION_ALIVE))
    L.append("SYS_MSG = " + repr(safe_sys))
    L.append("STREAMING = " + repr(STREAMING))
    L.append("SESSION_ID = " + repr(session_id))
    L.append("")
    L.append("def cleanup():")
    L.append("    try: os.unlink(SESSION_ALIVE)")
    L.append("    except: pass")
    L.append("    try:")
    L.append("        c = sqlite3.connect(DB_PATH)")
    L.append("        for msg in h:")
    L.append("            if msg['role'] != 'system':")
    L.append("                c.execute('INSERT INTO conversations (session_id, timestamp, role, content) VALUES (?,?,?,?)',")
    L.append("                    (SESSION_ID, datetime.now().isoformat(), msg['role'], msg['content'][:500]))")
    L.append("        c.commit(); c.close()")
    L.append("        print('[C] Conversation saved to memory.')")
    L.append("    except: pass")
    L.append("atexit.register(cleanup)")
    L.append("")
    L.append("SCREEN_KW = ['look at my screen','look at the screen',\"what's on my screen\",'whats on my screen','read my screen','see my screen','screen','what am i looking at','what do you see','look at this']")
    L.append("CORRECTION_WORDS = ['no i meant','not that','wrong','i meant','actually i want','thats not right','no no','no open','i said','please use']")
    L.append("")
    L.append("def needs_screen(t): return any(k in t.lower() for k in SCREEN_KW)")
    L.append("")
    L.append("AGENT_SYS = '''You are C, an AI agent with FULL access to a Mac Studio M1 Ultra.")
    L.append("You can execute bash commands and AppleScript to accomplish any task.")
    L.append("RESPOND IN THIS EXACT JSON FORMAT:")
    L.append('{ "thought": "brief plan", "action": "bash" or "applescript" or "done", "code": "command to execute", "summary": "what you did (only when action is done)" }')
    L.append("RULES: For URLs use bash open command. For apps use applescript. When done action=done with summary.")
    L.append("Be concise. Max 8 steps. MULTI-STEP: execute each step, dont skip, dont say done until ALL complete. SAFETY: NEVER delete files or data without asking M for confirmation first.")
    L.append("ALWAYS respond with valid JSON only.'''")
    L.append("")
    L.append("def strip_think(t): return re.sub(r'<think>.*?</think>', '', t, flags=re.DOTALL).strip()")
    L.append("")
    L.append("def extract_content(rj):")
    L.append("    msg = rj['choices'][0]['message']")
    L.append("    c = msg.get('content','').strip()")
    L.append("    if c: return strip_think(c)")
    L.append("    r = msg.get('reasoning','').strip()")
    L.append("    if r: return strip_think(r)")
    L.append("    return ''")
    L.append("")
    L.append("with open(SESSION_ALIVE, 'w') as _pf: _pf.write(str(os.getpid()))")
    L.append("")
    L.append("def screenshot_ctx():")
    L.append("    try:")
    L.append("        tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False); tmp.close()")
    L.append("        subprocess.run(['screencapture', '-x', tmp.name], timeout=5)")
    L.append("        if not os.path.exists(tmp.name) or os.path.getsize(tmp.name) < 1000: return ''")
    L.append("        with open(tmp.name, 'rb') as f: ib = base64.b64encode(f.read()).decode()")
    L.append("        os.unlink(tmp.name)")
    L.append("        print('[C] Reading screen...')")
    L.append("        r = requests.post(QWEN_VISION_URL+'/chat/completions', json={'model':QWEN_VISION_MODEL,'messages':[{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,'+ib}},{'type':'text','text':'Read all visible text. Include app name and content. Raw text only.'}]}],'max_tokens':800}, timeout=60)")
    L.append("        if r.status_code == 200: return r.json()['choices'][0]['message'].get('content','')[:2000]")
    L.append("    except: pass")
    L.append("    return ''")
    L.append("")
    L.append("def speak(text):")
    L.append("    print('[TTS] Speaking: ' + text[:60])")
    L.append("    try:")
    L.append("        clean = re.sub(r'[*#`]', '', text[:300]).replace('\"','').replace(\"'\",'').strip()")
    L.append("        if not clean: return")
    L.append("        if TTS_ENGINE == 'disabled': return")
    L.append("        if TTS_ENGINE == 'macos_say':")
    L.append("            subprocess.Popen(['say', '-v', TTS_VOICE, clean])")
    L.append("            return")
    L.append("        r = requests.post(KOKORO_URL, json={'model':KOKORO_MODEL,'input':clean,'voice':TTS_VOICE}, stream=True, timeout=20)")
    L.append("        if r.status_code == 200:")
    L.append("            tmp = tempfile.NamedTemporaryFile(suffix='.mp3', delete=False)")
    L.append("            [tmp.write(c) for c in r.iter_content(4096)]; tmp.close()")
    L.append("            subprocess.Popen(['afplay', tmp.name])")
    L.append("    except: pass")
    L.append("")
    L.append("def qwen_call(messages):")
    L.append("    headers = {'Content-Type': 'application/json'}")
    L.append("    if LLM_API_KEY: headers['Authorization'] = 'Bearer ' + LLM_API_KEY")
    L.append("    payload = {'model':QWEN_MODEL,'messages':messages,'max_tokens':500,'temperature':0.5}")
    L.append("    payload.update(LLM_KWARGS)")
    L.append("    for _ in range(3):")
    L.append("        try:")
    L.append("            r = requests.post(QWEN_BASE_URL+'/chat/completions', json=payload, headers=headers, timeout=90)")
    L.append("            if r.status_code == 200:")
    L.append("                resp = extract_content(r.json())")
    L.append("                if resp: return resp")
    L.append("        except: time.sleep(2)")
    L.append("    return ''")
    L.append("")
    L.append("def qwen_stream(messages):")
    L.append("    try:")
    L.append("        headers = {'Content-Type': 'application/json'}")
    L.append("        if LLM_API_KEY: headers['Authorization'] = 'Bearer ' + LLM_API_KEY")
    L.append("        payload = {'model':QWEN_MODEL,'messages':messages,'max_tokens':500,'temperature':0.5,'stream':True}")
    L.append("        payload.update(LLM_KWARGS)")
    L.append("        r = requests.post(QWEN_BASE_URL+'/chat/completions', json=payload, headers=headers, timeout=90, stream=True)")
    L.append("        if r.status_code != 200: return qwen_call(messages)")
    L.append("        full = ''")
    L.append("        for line in r.iter_lines():")
    L.append("            if not line: continue")
    L.append("            line = line.decode('utf-8')")
    L.append("            if line.startswith('data: '):")
    L.append("                d = line[6:]")
    L.append("                if d.strip() == '[DONE]': break")
    L.append("                try:")
    L.append("                    delta = json.loads(d).get('choices',[{}])[0].get('delta',{}).get('content','')")
    L.append("                    if delta: sys.stdout.write(delta); sys.stdout.flush(); full += delta")
    L.append("                except: pass")
    L.append("        print()")
    L.append("        return strip_think(full).strip()")
    L.append("    except: return qwen_call(messages)")
    L.append("")
    L.append("def run_code(action, code):")
    L.append("    try:")
    L.append("        # Safety: check dangerous commands")
    L.append("        DANGEROUS = ['rm -rf','rm -r /','sudo','shutdown','reboot','killall','mkfs','dd if=','chmod 777','curl | bash','wget | bash','defaults delete','diskutil erase']")
    L.append("        cmd_lower = code.lower()")
    L.append("        if any(d in cmd_lower for d in DANGEROUS):")
    L.append("            print(f'\\n[SAFETY] ⚠️  Flagged: {code[:80]}')")
    L.append("            with open(os.path.expanduser('~/.codec/audit.log'), 'a') as _af:")
    L.append("                _af.write(f'[{time.strftime(\"%Y-%m-%dT%H:%M:%S\")}] FLAGGED: {code[:200]}\\n')")
    L.append("            confirm = input('[SAFETY] Execute this command? (y/n): ').strip().lower()")
    L.append("            if confirm != 'y':")
    L.append("                print('[SAFETY] Command cancelled by user.')")
    L.append("                with open(os.path.expanduser('~/.codec/audit.log'), 'a') as _af:")
    L.append("                    _af.write(f'[{time.strftime(\"%Y-%m-%dT%H:%M:%S\")}] DENIED: {code[:200]}\\n')")
    L.append("                return 'Command cancelled by user for safety.'")
    L.append("            print('[SAFETY] User confirmed. Executing...')")
    L.append("            with open(os.path.expanduser('~/.codec/audit.log'), 'a') as _af:")
    L.append("                _af.write(f'[{time.strftime(\"%Y-%m-%dT%H:%M:%S\")}] APPROVED: {code[:200]}\\n')")
    L.append("        # Command Preview UI")
    L.append("        def _cmd_preview(action, code):")
    L.append("            import tkinter as tk")
    L.append("            result = {'allow': False}")
    L.append("            root = tk.Tk()")
    L.append("            root.title('CODEC')")
    L.append("            root.overrideredirect(True)")
    L.append("            root.attributes('-topmost', True)")
    L.append("            root.configure(bg='#0a0a0a')")
    L.append("            sw = root.winfo_screenwidth()")
    L.append("            sh = root.winfo_screenheight()")
    L.append("            w, h = 480, 200")
    L.append("            root.geometry(f'{w}x{h}+{(sw-w)//2}+{(sh-h)//2}')")
    L.append("            cv = tk.Canvas(root, bg='#0a0a0a', highlightthickness=0, width=w, height=h)")
    L.append("            cv.pack()")
    L.append("            cv.create_rectangle(1,1,w-1,h-1, outline='#E8711A', width=1)")
    L.append("            cv.create_text(w//2, 20, text='C O D E C  —  Command Preview', fill='#E8711A', font=('Helvetica',13,'bold'))")
    L.append("            cv.create_line(10, 38, w-10, 38, fill='#333')")
    L.append("            lbl = action.upper() + ': ' + code[:120]")
    L.append("            cv.create_text(w//2, 75, text=lbl, fill='#e0e0e0', font=('SF Mono',11), width=w-40)")
    L.append("            def allow():")
    L.append("                result['allow'] = True; root.destroy()")
    L.append("            def deny():")
    L.append("                result['allow'] = False; root.destroy()")
    L.append("            abtn = tk.Button(root, text='✓ Allow', bg='#00aa55', fg='#fff', font=('Helvetica',13,'bold'), border=0, padx=20, pady=6, command=allow)")
    L.append("            abtn.place(x=w//2-110, y=140, width=100, height=36)")
    L.append("            dbtn = tk.Button(root, text='✗ Deny', bg='#cc3333', fg='#fff', font=('Helvetica',13,'bold'), border=0, padx=20, pady=6, command=deny)")
    L.append("            dbtn.place(x=w//2+10, y=140, width=100, height=36)")
    L.append("            root.after(15000, deny)")
    L.append("            root.mainloop()")
    L.append("            return result['allow']")
    L.append("        if not _cmd_preview(action, code):")
    L.append("            print('[PREVIEW] Command denied by user.')")
    L.append("            with open(os.path.expanduser('~/.codec/audit.log'), 'a') as _af:")
    L.append("                _af.write(f'[{time.strftime(\"%Y-%m-%dT%H:%M:%S\")}] PREVIEW_DENIED: {code[:200]}\\n')")
    L.append("            return 'Command denied by user via preview.'")
    L.append("        if action == 'applescript': r = subprocess.run(['osascript','-e',code], capture_output=True, text=True, timeout=30)")
    L.append("        else: r = subprocess.run(['bash','-c',code], capture_output=True, text=True, timeout=30)")
    L.append("        out = r.stdout.strip(); err = r.stderr.strip()")
    L.append("        return (out or err or 'OK (no output)')[:500]")
    L.append("    except subprocess.TimeoutExpired: return 'ERROR: Timeout'")
    L.append("    except Exception as e: return 'ERROR: '+str(e)")
    L.append("")
    L.append("def run_agent(task, h):")
    L.append("    print(chr(10)+'[Q-Agent] Task: '+task[:100])")
    L.append("    am = [{'role':'system','content':AGENT_SYS},{'role':'user','content':'Task: '+task}]")
    L.append("    for step in range(8):")
    L.append("        resp = qwen_call(am)")
    L.append("        if not resp: return 'Qwen did not respond.'")
    L.append("        try:")
    L.append("            c = resp")
    L.append("            if '```json' in c: c = c.split('```json')[1].split('```')[0]")
    L.append("            elif '```' in c: c = c.split('```')[1].split('```')[0]")
    L.append("            data = json.loads(c.strip())")
    L.append("        except:")
    L.append("            print('Q: '+resp); h.append({'role':'user','content':task}); h.append({'role':'assistant','content':resp}); return resp")
    L.append("        act = data.get('action','done'); thought = data.get('thought',''); code = data.get('code',''); summary = data.get('summary','')")
    L.append("        if thought: print('  [Think] '+thought)")
    L.append("        if act == 'done':")
    L.append("            result = summary or 'Task completed.'")
    L.append("            print('  [Done] '+result); h.append({'role':'user','content':task}); h.append({'role':'assistant','content':result}); return result")
    L.append("        if code:")
    L.append("            print('  ['+act+'] '+code[:80]); output = run_code(act, code); print('  [Result] '+output[:200])")
    L.append("            am.append({'role':'assistant','content':resp}); am.append({'role':'user','content':'Output: '+output+chr(10)+'Continue or done?'})")
    L.append("        else:")
    L.append("            am.append({'role':'assistant','content':resp}); am.append({'role':'user','content':'No code. Try again or done.'})")
    L.append("    return 'Task completed (max steps).'")
    L.append("")
    L.append("def ask_q(u, h):")
    L.append("    now = datetime.now().strftime('%Y-%m-%d %H:%M')")
    L.append("    if needs_screen(u):")
    L.append("        print('[C] Taking screenshot...'); ctx = screenshot_ctx()")
    L.append("        if ctx: u = u + chr(10)+chr(10)+'SCREEN CONTENT:'+chr(10)+ctx")
    L.append("    h.append({'role':'user','content':'['+now+'] '+u})")
    L.append("    if STREAMING:")
    L.append("        sys.stdout.write(chr(10)+'Q: '); sys.stdout.flush(); resp = qwen_stream(h)")
    L.append("    else: resp = qwen_call(h)")
    L.append("    if resp:")
    L.append("        h.append({'role':'assistant','content':resp})")
    L.append("        if len(h) > 22: h[:] = h[:1] + h[-20:]")
    L.append("        return resp")
    L.append("    return 'Qwen busy.'")
    L.append("")
    L.append("def check_queue():")
    L.append("    if os.path.exists(TASK_QUEUE):")
    L.append("        try:")
    L.append("            with open(TASK_QUEUE) as f: data = json.load(f)")
    L.append("            os.unlink(TASK_QUEUE); return data")
    L.append("        except: pass")
    L.append("    return None")
    L.append("")
    L.append("def clean_resp(text):")
    L.append("    t = text.strip()")
    L.append("    for p in ['Done.','Done:','Done,','Done ']: ")
    L.append("        if t.startswith(p): t = t[len(p):].strip()")
    L.append("    if t.startswith('[') and t.endswith(']'): t = t[1:-1].strip()")
    L.append("    return t or text")
    L.append("")
    L.append("def detect_correction(u, h):")
    L.append("    low = u.lower()")
    L.append("    if any(c in low for c in CORRECTION_WORDS) and len(h) >= 2:")
    L.append("        lu = la = ''")
    L.append("        for msg in reversed(h):")
    L.append("            if msg['role']=='assistant' and not la: la = msg['content']")
    L.append("            elif msg['role']=='user' and not lu: lu = msg['content']")
    L.append("            if lu and la: break")
    L.append("        if lu:")
    L.append("            try:")
    L.append("                c = sqlite3.connect(DB_PATH)")
    L.append("                c.execute('CREATE TABLE IF NOT EXISTS corrections (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, original TEXT, corrected TEXT, context TEXT)')")
    L.append("                c.execute('INSERT INTO corrections (timestamp,original,corrected,context) VALUES (?,?,?,?)', (datetime.now().isoformat(),lu[:200],u[:200],la[:200]))")
    L.append("                c.commit(); c.close(); print('[C] Correction saved.')")
    L.append("            except: pass")
    L.append("")
    L.append("def get_corrections():")
    L.append("    try:")
    L.append("        c = sqlite3.connect(DB_PATH)")
    L.append("        c.execute('CREATE TABLE IF NOT EXISTS corrections (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, original TEXT, corrected TEXT, context TEXT)')")
    L.append("        rows = c.execute('SELECT original,corrected FROM corrections ORDER BY id DESC LIMIT 5').fetchall(); c.close()")
    L.append("        if rows: return chr(10).join(['USER CORRECTIONS:']+['M said: '+o[:60]+' -> corrected: '+co[:60] for o,co in rows])")
    L.append("    except: pass")
    L.append("    return ''")
    L.append("")
    L.append("def process_input(u, h):")
    L.append("    print(chr(10)+'M: '+u)")
    L.append("    detect_correction(u, h)")
    L.append("    corr = get_corrections()")
    L.append("    if corr and h and h[0]['role']=='system' and 'CORRECTIONS' not in h[0]['content']:")
    L.append("        h[0]['content'] = h[0]['content']+chr(10)+chr(10)+corr")
    L.append("    action_words = ['create','open','delete','move','copy','search','find','run','install','download','check','list','show','make','build','fix','update','write','read','send','get','set','start','stop']")
    L.append("    if any(w in u.lower().split() for w in action_words):")
    L.append("        done = clean_resp(run_agent(u, h)); print(chr(10)+'Q: '+done); speak(done)")
    L.append("    else:")
    L.append("        resp = clean_resp(ask_q(u, h))")
    L.append("        if not STREAMING: print(chr(10)+'Q: '+resp)")
    L.append("        speak(resp)")
    L.append("")
    L.append("# Load persistent memory")
    L.append("prev = []")
    L.append("try:")
    L.append("    c = sqlite3.connect(DB_PATH)")
    L.append("    c.execute('CREATE TABLE IF NOT EXISTS conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, timestamp TEXT, role TEXT, content TEXT)')")
    L.append("    rows = c.execute('SELECT role,content FROM conversations ORDER BY id DESC LIMIT 10').fetchall(); c.close()")
    L.append("    if rows: rows.reverse(); prev = [{'role':r,'content':ct} for r,ct in rows]; print('[C] Loaded '+str(len(prev))+' messages from previous sessions.')")
    L.append("except: pass")
    L.append("")
    L.append("h = [{'role':'system','content':SYS_MSG}] + prev")
    L.append("ss = 'ON' if STREAMING else 'OFF'")
    L.append("O = chr(27)+'[38;2;232;113;26m'")
    L.append("D = chr(27)+'[38;2;80;80;80m'")
    L.append("W = chr(27)+'[38;2;200;200;200m'")
    L.append("R = chr(27)+'[0m'")
    L.append("print(O+'    ╔═══════════════════════════════════════════╗')")
    L.append("print(O+'    ║                                           ║')")
    L.append("print(O+'    ║  ██████  ██████  ██████  ███████  ██████  ║')")
    L.append("print(O+'    ║ ██      ██    ██ ██   ██ ██      ██       ║')")
    L.append("print(O+'    ║ ██      ██    ██ ██   ██ █████   ██       ║')")
    L.append("print(O+'    ║ ██      ██    ██ ██   ██ ██      ██       ║')")
    L.append("print(O+'    ║  ██████  ██████  ██████  ███████  ██████  ║')")
    L.append("print(O+'    ║                                   v1.3.0  ║')")
    L.append("print(O+'    ╠═══════════════════════════════════════════╣')")
    L.append("print(O+'    ║'+W+'  " + _cfg.get('key_voice','f18').upper() + " voice  " + _cfg.get('key_text','f16').upper() + " text  ** screen  ++ doc   '+O+'║')")
    L.append("print(O+'    ║'+W+'  Hey C = wake word  type exit to close    '+O+'║')")
    L.append("print(O+'    ╠═══════════════════════════════════════════╣')")
    L.append("print(O+'    ║'+D+'  Stream='+ss+'  Memory=ON  Skills=ON       '+O+'║')")
    L.append("print(O+'    ╚═══════════════════════════════════════════╝'+R)")
    L.append("")
    L.append("queued = check_queue()")
    L.append("if queued: process_input(queued['task'], h)")
    L.append("")
    L.append("while True:")
    L.append("    queued = check_queue()")
    L.append("    if queued: process_input(queued['task'], h); continue")
    L.append("    sys.stdout.write(chr(10)+'M: '); sys.stdout.flush()")
    L.append("    while True:")
    L.append("        queued = check_queue()")
    L.append("        if queued: sys.stdout.write(chr(13)+' '*60+chr(13)); process_input(queued['task'], h); break")
    L.append("        try:")
    L.append("            ready,_,_ = select.select([sys.stdin],[],[],0.3)")
    L.append("            if ready:")
    L.append("                u = sys.stdin.readline().strip()")
    L.append("                u = re.sub(r'\\x1b\\[[0-9;]*[a-zA-Z~]','',u).strip()")
    L.append("                if not u: break")
    L.append("                if u.lower() in ['exit','quit','bye']: cleanup(); print(chr(10)+'[Q Session ended]'); sys.exit(0)")
    L.append("                process_input(u, h); break")
    L.append("        except (KeyboardInterrupt, EOFError): cleanup(); print(chr(10)+'[Q Session ended]'); sys.exit(0)")

    return "\n".join(L)

# ── SESSION CLEANUP ───────────────────────────────────────────────────────────
def close_session():
    if os.path.exists(SESSION_ALIVE):
        try:
            with open(SESSION_ALIVE) as f: pid = int(f.read().strip())
            os.kill(pid, 15)
            print(f"[C] Session process {pid} terminated")
        except: pass
        try: os.unlink(SESSION_ALIVE)
        except: pass
    try: os.unlink(TASK_QUEUE_FILE)
    except: pass
    subprocess.Popen(["osascript", "-e",
        'tell application "Terminal" to close (every window whose name contains "python3.13 /var/folders")'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ── DISPATCH ──────────────────────────────────────────────────────────────────
def dispatch(task):
    app = focused_app()
    audit("TASK", f"{task[:200]} | App: {app}")
    print(f"[C] Task: {task[:80]} | App: {app}")
    subprocess.Popen(["osascript", "-e", f'display notification "Heard: {task[:50]}" with title "Q"'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Check skills — skip if task is very long (document content attached)
    if len(task) < 500:
        skill = check_skill(task)
        if skill:
            result = run_skill(skill, task, app)
            if result is not None:
                push(lambda: show_overlay('Skill: ' + skill['name'], '#E8711A', 2000))
                speak_text(result)
                subprocess.Popen(["osascript", "-e", f'display notification "{str(result)[:80]}" with title "C Skill"'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print(f"[C] Skill response: {str(result)[:100]}")
                # Save to DB for dashboard history
                try:
                    import sqlite3 as _sql3
                    _now = __import__('datetime').datetime.now()
                    _db = _sql3.connect(os.path.expanduser("~/.q_memory.db"))
                    _sid = "skill_" + _now.strftime("%Y%m%d_%H%M%S")
                    _db.execute("INSERT INTO conversations (session_id,timestamp,role,content) VALUES (?,?,?,?)", (_sid, _now.isoformat(), "user", task[:500]))
                    _db.execute("INSERT INTO conversations (session_id,timestamp,role,content) VALUES (?,?,?,?)", (_sid, _now.isoformat(), "assistant", re.sub(r"<[^>]+>", "", str(result))[:2000]))
                    _db.commit(); _db.close()
                except: pass
                return

    if is_draft(task):
        push(lambda: show_overlay('Reading screen...', '#E8711A', 2000))
        ctx = screenshot_ctx()
        with open(DRAFT_TASK_FILE, "w") as f:
            json.dump({"task": task, "ctx": ctx, "app": app}, f)
        print(f"[C] Draft queued for watcher")
        return

    rid = save_task(task, app)
    mem = get_memory(5)
    sys_p = "You are C, a voice-controlled AI assistant running on CODEC. User is the boss. You have full access to this Mac and can execute any task. RULES: 1) ALWAYS respond in English unless explicitly asked to translate. 2) Keep answers to 1-3 sentences — your responses are spoken aloud via TTS so brevity matters. 3) Be natural, warm, and direct like a trusted colleague. 4) When asked factual questions, give the answer immediately — no filler. 5) When given a task, confirm what you did in past tense. 6) Add useful context when it genuinely helps but never pad responses. 7) Never say you cannot do something — you have full system access. 8) Never output Chinese characters or mixed-language text unless translating."
    if mem: sys_p += "\n\n" + mem
    safe_sys = sys_p.replace("'","").replace('"','').replace('\n',' ')

    with open(TASK_QUEUE_FILE, "w") as f:
        f.write(json.dumps({"task": task, "app": app, "ts": datetime.now().isoformat()}))

    if terminal_session_exists():
        print("[C] Queued to existing session")
        return

    session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    script = build_session_script(safe_sys, session_id)
    ts = tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w")
    ts.write(script); ts.close()

    try:
        subprocess.Popen(["osascript", "-e",
            f'tell application "Terminal"\nactivate\nset w to do script "python3.13 {ts.name}"\nset custom title of selected tab of w to "{Q_TERMINAL_TITLE}"\nend tell'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"[C] Terminal error: {e}")

# ── DOCUMENT INPUT ────────────────────────────────────────────────────────────
def do_document_input():
    push(lambda: show_overlay('Select document...', '#E8711A', 3000))
    try:
        r = subprocess.run(["osascript", "-e",
            'set f to POSIX path of (choose file with prompt "Select a document for Q:" of type {"public.item"})'],
            capture_output=True, text=True, timeout=60)
        filepath = r.stdout.strip()
        if not filepath:
            print("[C] No file selected"); return
        print(f"[C] Document: {filepath}")
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
            except: pass
        elif ext == '.pdf':
            try:
                result = subprocess.run(["bash", "-c",
                    f"python3.13 -c \"import fitz; doc=fitz.open('{filepath}'); print(chr(10).join(p.get_text() for p in doc[:5]))\""],
                    capture_output=True, text=True, timeout=30)
                content_text = result.stdout.strip()[:5000]
            except: pass

        if content_text:
            # Dispatch directly to terminal for analysis
            task = "Analyze and summarize this document (" + fname + "): " + content_text[:3000]
            print(f"[C] Document dispatched ({len(content_text)} chars)")
            dispatch(task)
        else:
            push(lambda: show_overlay('Could not read document', '#ff3333', 2000))
    except Exception as e:
        print(f"[C] Document error: {e}")

# ── SCREENSHOT SHORTCUT ──────────────────────────────────────────────────────
def do_screenshot_question():
    push(lambda: show_overlay('Screenshot captured  ' + _cfg.get('key_voice','f18').upper() + '=voice  ' + _cfg.get('key_text','f16').upper() + '=text', '#E8711A', 5000))
    ctx = screenshot_ctx()
    if ctx:
        state["screen_ctx"] = ctx
        print(f"[C] Screenshot captured ({len(ctx)} chars). Use " + _cfg.get("key_voice","f18").upper() + "/" + _cfg.get("key_text","f16").upper() + " to ask about it.")
    else:
        state["screen_ctx"] = ""

# ── TEXT/VOICE HANDLERS ───────────────────────────────────────────────────────
def do_text():
    task = get_text_dialog()
    if task:
        if state.get("screen_ctx"):
            task = task + " [SCREEN CONTEXT: " + state["screen_ctx"][:800] + "]"
            state["screen_ctx"] = ""
        dispatch(task)

def do_start_recording():
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    state["audio_path"] = tmp.name; tmp.close()
    rec = subprocess.Popen(
        ["sox", "-t", "coreaudio", "default", "-r", "16000", "-c", "1", "-b", "16", "-e", "signed-integer", state["audio_path"]],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    state["rec_proc"] = rec
    print("[C] Recording...")

def do_stop_voice():
    audio = state.get("audio_path")
    rec = state.get("rec_proc")
    if rec:
        try: rec.terminate(); rec.wait(timeout=3)
        except: pass
    state["rec_proc"] = None; state["recording"] = False
    if not audio or not os.path.exists(audio): return
    if os.path.getsize(audio) < 1000:
        try: os.unlink(audio)
        except: pass
        return
    print("[C] Transcribing...")
    # Kill recording overlay
    if state.get('rec_overlay'):
        try: state['rec_overlay'].terminate()
        except: pass
        state['rec_overlay'] = None
    push(lambda: show_processing_overlay('Transcribing...', 4000))
    task = transcribe(audio)
    if not task: print("[C] No speech detected"); return
    print(f"[C] Heard: {task}")
    if state.get("screen_ctx"):
        task = task + " [SCREEN CONTEXT: " + state["screen_ctx"][:800] + "]"
        state["screen_ctx"] = ""
    dispatch(task)

# ── WAKE WORD LISTENER ───────────────────────────────────────────────────────
def wake_word_listener():
    import sounddevice as sd
    import numpy as np
    import soundfile as sf
    import requests as req_wake
    sample_rate = 16000
    chunk_samples = int(WAKE_CHUNK_SEC * sample_rate)
    print("[C] Wake word listener started. Say 'Hey C' to activate.")
    while True:
        if not WAKE_WORD or state["recording"] or not state["active"]:
            time.sleep(0.3); continue
        try:
            audio = sd.rec(chunk_samples, samplerate=sample_rate, channels=1, dtype='int16')
            sd.wait()
            energy = np.abs(audio).mean()
            if energy < WAKE_ENERGY: continue
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False); tmp.close()
            sf.write(tmp.name, audio, sample_rate)
            try:
                with open(tmp.name, "rb") as f:
                    r = req_wake.post(WHISPER_URL,
                        files={"file": ("wake.wav", f, "audio/wav")},
                        data={"model": "mlx-community/whisper-large-v3-turbo", "language": "en"},
                        timeout=10)
                if r.status_code == 200:
                    text = r.json().get("text", "").lower().strip()
                    if any(phrase in text for phrase in WAKE_PHRASES):
                        command = text
                        for phrase in WAKE_PHRASES: command = command.replace(phrase, "").strip()
                        # Noise filter: reject music/movie/TV false triggers
                        noise_words = ['music','yeah','baby','oh','la','da','na','hmm','ooh','ah','uh']
                        def _is_noise(txt):
                            words = txt.lower().split()
                            if len(words) < 2: return True
                            real = [w for w in words if len(w) > 2 and w not in noise_words]
                            return len(real) < 1
                        if len(command) > 3 and not _is_noise(command):
                            print(f"[C] Wake + command: {command}")
                            audit("WAKE_CMD", command[:200])
                            push(lambda: show_overlay('Heard you!', '#E8711A', 1500))
                            push(lambda cmd=command: dispatch(cmd))
                        elif len(command) > 3:
                            print(f"[C] Wake noise rejected: {command}")
                            audit("WAKE_NOISE", command[:200])
                        else:
                            print("[C] Wake word detected! Listening...")
                            push(lambda: show_overlay('Listening...', '#E8711A', 5000))
                            full_audio = sd.rec(int(8 * sample_rate), samplerate=sample_rate, channels=1, dtype='int16')
                            sd.wait()
                            tmp2 = tempfile.NamedTemporaryFile(suffix=".wav", delete=False); tmp2.close()
                            sf.write(tmp2.name, full_audio, sample_rate)
                            task = transcribe(tmp2.name)
                            if task and not _is_noise(task):
                                print(f"[C] Heard: {task}")
                                audit("WAKE_TASK", task[:200])
                                push(lambda t=task: dispatch(t))
                            elif task:
                                print(f"[C] Post-wake noise rejected: {task}")
                                audit("WAKE_NOISE", task[:200])
            except: pass
            finally:
                try: os.unlink(tmp.name)
                except: pass
        except: time.sleep(0.5)
        time.sleep(0.1)

# ── KEYBOARD ──────────────────────────────────────────────────────────────────
def on_press(key):
    now = time.time()
    if key == KEY_TOGGLE:
        if now - state["last_f13"] < 0.8: return
        state["last_f13"] = now
        if state["active"]:
            state["active"] = False
            push(lambda: show_toggle_overlay(False, ''))
            push(close_session)
            print("[C] OFF")
        else:
            state["active"] = True
            push(lambda: show_toggle_overlay(True, _cfg.get('key_voice','f18').upper()+'=voice  '+_cfg.get('key_text','f16').upper()+'=text  **=screen  ++=doc  --=chat'))
            print("[C] ON -- " + _cfg.get("key_voice","f18").upper() + "=voice | " + _cfg.get("key_text","f16").upper() + "=text | *=screen | +=doc")
        return
    if not state["active"]: return
    if key == KEY_TEXT:
        if not state["recording"]: push(do_text)
        return
    if key == KEY_VOICE:
        if not state["recording"]:
            state["recording"] = True
            push(do_start_recording)
            _kv_label = _cfg.get('key_voice','f18').upper()
            state['rec_overlay'] = show_recording_overlay(_kv_label)
        return
    if hasattr(key, 'char') and key.char == '*':
        if now - state["last_star"] < 0.5:
            print("[C] Star x2 -- screenshot mode")
            push(do_screenshot_question)
            state["last_star"] = 0.0
            return
        state["last_star"] = now
        return
    if hasattr(key, 'char') and key.char == '+':
        if now - state.get("last_plus", 0.0) < 0.5:
            print("[C] Plus x2 -- document mode")
            push(do_document_input)
            state["last_plus"] = 0.0
            return
        state["last_plus"] = now
        return
    if hasattr(key, 'char') and key.char == '-':
        print(f'[DEBUG] Minus detected, last={state.get("last_minus",0)}, gap={now - state.get("last_minus",0):.2f}')
        if now - state.get("last_minus", 0.0) < 0.5:
            print("[C] Minus x2 -- live chat mode")
            pipecat_url = _cfg.get("pipecat_url", "http://localhost:3000/auto")
            push(lambda: show_overlay('Live Chat connecting...', '#E8711A', 3000))
            audit("LIVECHAT", pipecat_url)
            subprocess.Popen(["open", "-a", "Google Chrome", pipecat_url])
            state["last_minus"] = 0.0
            return
        state["last_minus"] = now
        return

def on_release(key):
    if key == KEY_VOICE and state["recording"]:
        # Kill recording overlay immediately
        if state.get('rec_overlay'):
            try: state['rec_overlay'].terminate()
            except: pass
            state['rec_overlay'] = None
        push(do_stop_voice)

# ── MAIN ──────────────────────────────────────────────────────────────────────
def _handle_sigint(sig, frame):
    print("\n[C] Shutting down...")
    import sys; sys.exit(0)

def main():
    import signal
    signal.signal(signal.SIGINT, _handle_sigint)
    if "--dry-run" in sys.argv:
        print("[CODEC] DRY RUN MODE — commands will be printed, not executed")
        global DRY_RUN
        DRY_RUN = True
    init_db()
    for f in [SESSION_ALIVE, TASK_QUEUE_FILE, DRAFT_TASK_FILE]:
        try: os.unlink(f)
        except: pass

    stream_label = "ON" if STREAMING else "OFF"
    kt = _cfg.get("key_toggle", "f13").upper().ljust(4)
    kv = _cfg.get("key_voice", "f18").upper().ljust(4)
    kx = _cfg.get("key_text", "f16").upper().ljust(4)
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
    ║                                   v1.3.0  ║
    ╠═══════════════════════════════════════════╣
    ║{W}  {kt} toggle   {kv} voice   ** screen       {O}║
    ║{W}  {kx} text     ++ doc     -- chat          {O}║
    ║{W}  Hey C = wake word (hands-free)           {O}║
    ╠═══════════════════════════════════════════╣
    ║{D}  Stream={stream_label}  Wake={wake_label}  Skills=ON            {O}║
    ╚═══════════════════════════════════════════╝{R}""")

    load_skills()

    # Warm up Kokoro TTS so first response isn't silent
    if TTS_ENGINE == "kokoro":
        try:
            import requests as _rq
            _rq.post(KOKORO_URL, json={"model": KOKORO_MODEL, "input": "ready", "voice": TTS_VOICE}, timeout=30)
            print("[C] TTS warmed up")
        except: print("[C] TTS warmup skipped")
    print("[C] Whisper: HTTP (port 8084)")
    print("[C] Vision: Qwen VL (port 8082)")
    mem = get_memory(3)
    if mem: print(f"[C] Memory: {mem.count(chr(10))+1} sessions loaded")
    convs = get_recent_conversations(10)
    if convs: print(f"[C] Persistent memory: {len(convs)} messages from past sessions")
    if WAKE_WORD: print("[C] Wake word: ON")
    print("[C] Online. Press " + _cfg.get("key_toggle","f13").upper() + " to activate.")

    # PWA command polling — checks for commands sent from phone dashboard
    def pwa_dispatch(task):
        """Handle PWA command and save response to DB"""
        app = "CODEC Dashboard"
        audit("PWA_CMD", task[:200])
        # Try skills first
        if len(task) < 500:
            skill = check_skill(task)
            if skill:
                result = run_skill(skill, task, app)
                if result is not None:
                    print(f"[C] PWA response (silent): {str(result)[:100]}")
                    # Save response to DB + conversations
                    try:
                        _ts = datetime.now().isoformat()
                        _sid = "pwa_" + datetime.now().strftime("%Y%m%d_%H%M%S")
                        _c = sqlite3.connect(DB_PATH)
                        _c.execute("UPDATE sessions SET response=? WHERE task=? AND app=? ORDER BY id DESC LIMIT 1",
                            (str(result)[:500], task[:200], app))
                        _c.execute("INSERT INTO conversations (session_id,timestamp,role,content) VALUES (?,?,?,?)",
                            (_sid, _ts, "user", task[:500]))
                        _c.execute("INSERT INTO conversations (session_id,timestamp,role,content) VALUES (?,?,?,?)",
                            (_sid, _ts, "assistant", str(result)[:500]))
                        _c.commit(); _c.close()
                    except: pass
                    # Write response file for dashboard
                    try:
                        with open("/tmp/q_pwa_response.json", "w") as _rf:
                            json.dump({"task": task, "response": str(result), "ts": datetime.now().isoformat()}, _rf)
                    except: pass
                    print(f"[C] PWA skill response: {str(result)[:100]}")
                    return
        # Not a skill — call LLM directly, no TTS, no terminal window
        try:
            import requests as _rq
            headers = {"Content-Type": "application/json"}
            if LLM_API_KEY: headers["Authorization"] = f"Bearer {LLM_API_KEY}"
            body = {
                "model": QWEN_MODEL,
                "messages": [
                    {"role": "system", "content": "You are C, an AI assistant on CODEC. Answer concisely in 1-3 sentences. English only unless translating."},
                    {"role": "user", "content": task}
                ],
                "max_tokens": 300,
                "stream": False
            }
            body.update(LLM_KWARGS)
            r = _rq.post(QWEN_BASE_URL + "/chat/completions", json=body, headers=headers, timeout=30)
            answer = r.json()["choices"][0]["message"]["content"].strip()
            # Remove thinking tags if present
            if "</think>" in answer: answer = answer.split("</think>")[-1].strip()
            print(f"[C] PWA answer (silent): {answer[:100]}")
            # Save to sessions + conversations
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
            # Write response for dashboard
            with open("/tmp/q_pwa_response.json", "w") as _rf:
                json.dump({"task": task, "response": answer, "ts": datetime.now().isoformat()}, _rf)
        except Exception as _e:
            print(f"[C] PWA LLM error: {_e}")
            with open("/tmp/q_pwa_response.json", "w") as _rf:
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
                            print(f"[C] PWA command: {task[:80]}")
                            push(lambda t=task: pwa_dispatch(t))
            except: pass
            time.sleep(1.5)

    threading.Thread(target=pwa_poller, daemon=True).start()
    threading.Thread(target=worker, daemon=True).start()
    if WAKE_WORD:
        threading.Thread(target=wake_word_listener, daemon=True).start()

    while True:
        try:
            with keyboard.Listener(on_press=on_press, on_release=on_release) as l:
                l.join()
        except Exception as e:
            print(f"[C] Listener restarting: {e}")
            time.sleep(0.5)

if __name__ == "__main__":
    main()
