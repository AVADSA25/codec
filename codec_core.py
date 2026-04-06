"""CODEC Core — shared functions used by both codec.py and skills/codec.py.

Single source of truth. Edit HERE, not in the consumer files.
"""
import logging, os, sys, json, re, sqlite3, subprocess, tempfile, base64
from datetime import datetime

log = logging.getLogger(__name__)

# ── CONFIG (single source of truth: codec_config.py) ─────────────────────────
from codec_config import (
    QWEN_BASE_URL, QWEN_MODEL, LLM_API_KEY, LLM_KWARGS, LLM_PROVIDER,
    QWEN_VISION_URL, QWEN_VISION_MODEL,
    TTS_ENGINE, KOKORO_URL, KOKORO_MODEL, TTS_VOICE,
    WHISPER_URL,
    DB_PATH, TASK_QUEUE_FILE, SESSION_ALIVE, SKILLS_DIR,
    STREAMING,
)

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

def strip_think(text):
    return re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()

def is_draft(t): return any(k in t.lower() for k in DRAFT_KEYWORDS)
def needs_screen(t): return any(k in t.lower() for k in SCREEN_KEYWORDS)

# ── MEMORY ────────────────────────────────────────────────────────────────────
def init_db():
    c = sqlite3.connect(DB_PATH)
    c.execute("CREATE TABLE IF NOT EXISTS sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, task TEXT, app TEXT, response TEXT, user_id TEXT DEFAULT 'default')")
    c.execute("CREATE TABLE IF NOT EXISTS conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, timestamp TEXT, role TEXT, content TEXT, user_id TEXT DEFAULT 'default')")
    c.execute("CREATE TABLE IF NOT EXISTS corrections (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, original TEXT, corrected TEXT, context TEXT, user_id TEXT DEFAULT 'default')")
    # Migrate existing tables: add user_id column if missing
    for table in ("sessions", "conversations", "corrections"):
        try:
            c.execute(f"ALTER TABLE {table} ADD COLUMN user_id TEXT DEFAULT 'default'")
        except sqlite3.OperationalError:
            pass  # column already exists
    c.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_corrections_user ON corrections(user_id)")
    c.commit(); c.close()

def save_task(task, app, user_id="default"):
    c = sqlite3.connect(DB_PATH)
    cur = c.execute("INSERT INTO sessions (timestamp,task,app,response,user_id) VALUES (?,?,?,?,?)", (datetime.now().isoformat(), task[:200], app, "", user_id))
    rid = cur.lastrowid; c.commit(); c.close(); return rid

def get_memory(n=5, user_id=None):
    try:
        c = sqlite3.connect(DB_PATH)
        if user_id is not None:
            rows = c.execute("SELECT timestamp,task,app,response FROM sessions WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, n)).fetchall()
        else:
            rows = c.execute("SELECT timestamp,task,app,response FROM sessions ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        c.close()
        if not rows: return ""
        lines = ["RECENT Q SESSIONS:"]
        for ts, task, app, resp in rows:
            r = (resp[:100]+"...") if resp and len(resp)>100 else (resp or "no response")
            lines.append(f"[{ts[:16].replace('T',' ')}] {app} | {task[:60]} | {r}")
        return "\n".join(lines)
    except Exception as e:
        log.warning("Recent sessions query failed: %s", e)
        return ""

def get_recent_conversations(n=10, user_id=None):
    try:
        c = sqlite3.connect(DB_PATH)
        if user_id is not None:
            rows = c.execute("SELECT role, content FROM conversations WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, n)).fetchall()
        else:
            rows = c.execute("SELECT role, content FROM conversations ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        c.close()
        if not rows: return []
        rows.reverse()
        return [{"role": r, "content": ct} for r, ct in rows]
    except Exception as e:
        log.warning("Recent conversations query failed: %s", e)
        return []

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
                print(f"[CODEC] Skill loaded: {fname[:-3]}")
        except Exception as e:
            print(f"[CODEC] Skill error ({fname}): {e}")

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
        print(f"[CODEC] Whisper error: {e}")
    finally:
        try: os.unlink(path)
        except Exception as e: log.debug("Audio file cleanup failed: %s", e)
    return ""

# ── UTILITIES ─────────────────────────────────────────────────────────────────
def focused_app():
    try:
        r = subprocess.run(["osascript", "-e",
            'tell application "System Events" to get name of first application process whose frontmost is true'],
            capture_output=True, text=True, timeout=3)
        return r.stdout.strip()
    except Exception as e:
        log.debug("Focused app detection failed: %s", e)
        return "Unknown"

def get_text_dialog():
    try:
        r = subprocess.run(["osascript", "-e",
            'set t to text returned of (display dialog "CODEC - Enter task:" default answer "" with title "CODEC" buttons {"Cancel","Send"} default button "Send")'],
            capture_output=True, text=True, timeout=120)
        return r.stdout.strip()
    except Exception as e:
        log.debug("Text dialog failed: %s", e)
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
    except Exception as e: log.debug("Stale session_alive cleanup failed: %s", e)
    print("[CODEC] Cleaned stale session_alive")
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
    except Exception as e: log.warning("TTS speak failed: %s", e)

# ── SESSION CLEANUP ───────────────────────────────────────────────────────────
def close_session():
    if os.path.exists(SESSION_ALIVE):
        try:
            with open(SESSION_ALIVE) as f: pid = int(f.read().strip())
            os.kill(pid, 15)
            print(f"[CODEC] Session process {pid} terminated")
        except Exception as e: log.debug("Session process termination failed: %s", e)
        try: os.unlink(SESSION_ALIVE)
        except Exception as e: log.debug("Session alive file cleanup failed: %s", e)
    try: os.unlink(TASK_QUEUE_FILE)
    except Exception as e: log.debug("Task queue file cleanup failed: %s", e)
    subprocess.Popen(["osascript", "-e",
        'tell application "Terminal" to close (every window whose name contains "python3.13 /var/folders")'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ── BUILD SESSION SCRIPT ─────────────────────────────────────────────────────
def build_session_script(safe_sys, session_id, wake_word_label="CODEC"):
    """Generate the standalone Terminal session script.

    .. deprecated::
        Use :func:`codec_agent.run_session_in_terminal` instead.
        This function writes API keys to temp files and will be removed in a future version.

    Args:
        safe_sys: System prompt string
        session_id: Session ID for conversation tracking
        wake_word_label: Display name for wake word banner (default "CODEC")
    """
    import warnings
    warnings.warn(
        "build_session_script() is deprecated, use codec_agent.run_session_in_terminal()",
        DeprecationWarning,
        stacklevel=2,
    )
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
    L.append("        print('[CODEC] Conversation saved to memory.')")
    L.append("    except: pass")
    L.append("atexit.register(cleanup)")
    L.append("")
    L.append("SCREEN_KW = ['look at my screen','look at the screen',\"what's on my screen\",'whats on my screen','read my screen','see my screen','screen','what am i looking at','what do you see','look at this']")
    L.append("CORRECTION_WORDS = ['no i meant','not that','wrong','i meant','actually i want','thats not right','no no','no open','i said','please use']")
    L.append("")
    L.append("def needs_screen(t): return any(k in t.lower() for k in SCREEN_KW)")
    L.append("")
    L.append("AGENT_SYS = '''You are CODEC, an AI agent with FULL access to a Mac Studio M1 Ultra.")
    L.append("You can execute bash commands and AppleScript to accomplish any task.")
    L.append("RESPOND IN THIS EXACT JSON FORMAT:")
    L.append('{ "thought": "brief plan", "action": "bash" or "applescript" or "done", "code": "command to execute", "summary": "what you did (only when action is done)" }')
    L.append("RULES: For URLs use bash open command. For apps use applescript. When done action=done with summary.")
    L.append("Be concise. Max 8 steps. MULTI-STEP: execute each step, dont skip, dont say done until ALL complete. SAFETY: NEVER delete files or data without asking the user for confirmation first.")
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
    L.append("        print('[CODEC] Reading screen...')")
    L.append("        r = requests.post(QWEN_VISION_URL+'/chat/completions', json={'model':QWEN_VISION_MODEL,'messages':[{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,'+ib}},{'type':'text','text':'Read all visible text. Include app name and content. Raw text only.'}]}],'max_tokens':800}, timeout=120)")
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
    # ── Safety: dangerous command patterns and confirmation dialog ──
    L.append("_DANGER_PATTERNS = [")
    L.append("    'rm ', 'rm\\t', 'rm\\n', 'rm -rf', 'rmdir', 'sudo rm', 'unlink ', 'shred ', 'trash ',")
    L.append("    'find -delete', '-exec rm', 'mkfs', 'dd if=', 'diskutil erase',")
    L.append("    'shutdown', 'reboot', 'halt', 'killall', 'pkill', 'sudo',")
    L.append("    'chmod 777', 'chown', '> /dev/', 'mv / /dev/null',")
    L.append("    ':(){ :|:& };:', ':(){:|:&};:', 'curl | bash', 'wget | bash', '| bash', '| sh',")
    L.append("    'defaults delete', 'defaults write', 'csrutil disable', 'nvram',")
    L.append("    'init 0', 'kill -9 1', 'format', 'fdisk',")
    L.append("]")
    L.append("")
    L.append("def _is_dangerous(cmd):")
    L.append("    cl = cmd.lower()")
    L.append("    for p in _DANGER_PATTERNS:")
    L.append("        pl = p.lower()")
    L.append("        if pl[0].isalnum() and pl[-1].isalnum():")
    L.append("            if re.search(r'\\b' + re.escape(pl) + r'\\b', cl): return True")
    L.append("        else:")
    L.append("            if pl in cl: return True")
    L.append("    return False")
    L.append("")
    L.append("def _danger_dialog(action, code):")
    L.append("    import tkinter as tk")
    L.append("    result = {'allow': False}")
    L.append("    root = tk.Tk()")
    L.append("    root.title('CODEC — DANGER')")
    L.append("    root.overrideredirect(True)")
    L.append("    root.attributes('-topmost', True)")
    L.append("    root.configure(bg='#0a0a0a')")
    L.append("    sw = root.winfo_screenwidth()")
    L.append("    sh = root.winfo_screenheight()")
    L.append("    w, h = 520, 230")
    L.append("    root.geometry(f'{w}x{h}+{(sw-w)//2}+{(sh-h)//2}')")
    L.append("    cv = tk.Canvas(root, bg='#0a0a0a', highlightthickness=0, width=w, height=150)")
    L.append("    cv.pack(side='top', fill='x')")
    L.append("    cv.create_rectangle(1,1,w-1,149, outline='#ff3333', width=2)")
    L.append("    cv.create_text(w//2, 22, text='\\u26a0  DANGEROUS COMMAND', fill='#ff3333', font=('Helvetica',14,'bold'))")
    L.append("    cv.create_line(10,42,w-10,42, fill='#553333')")
    L.append("    cv.create_text(w//2, 75, text=action.upper()+': '+code[:140], fill='#e0e0e0', font=('SF Mono',11), width=w-40)")
    L.append("    cv.create_text(w//2, 125, text='This can delete data. Are you sure?', fill='#ff9999', font=('Helvetica',11))")
    L.append("    def allow(): result['allow']=True; root.withdraw(); root.quit(); root.destroy()")
    L.append("    def deny(): result['allow']=False; root.withdraw(); root.quit(); root.destroy()")
    L.append("    bf = tk.Frame(root, bg='#0a0a0a')")
    L.append("    bf.pack(side='top', pady=15)")
    L.append("    tk.Button(bf, text='\\u2713 Allow', bg='#ff3333', fg='#fff', font=('Helvetica',13,'bold'), border=0, padx=20, pady=6, command=allow).pack(side='left', padx=10)")
    L.append("    tk.Button(bf, text='\\u2717 Deny', bg='#444', fg='#fff', font=('Helvetica',13,'bold'), border=0, padx=20, pady=6, command=deny).pack(side='left', padx=10)")
    L.append("    root.after(30000, deny)")
    L.append("    try: root.mainloop()")
    L.append("    except: pass")
    L.append("    try: root.destroy()")
    L.append("    except: pass")
    L.append("    return result['allow']")
    L.append("")
    L.append("def run_code(action, code):")
    L.append("    try:")
    L.append("        if _is_dangerous(code):")
    L.append("            print(f'\\n[SAFETY] \\u26a0\\ufe0f  FLAGGED: {code[:80]}')")
    L.append("            with open(os.path.expanduser('~/.codec/audit.log'),'a') as af: af.write(f'[{time.strftime(\"%Y-%m-%dT%H:%M:%S\")}] shell_flagged: {code[:200]}\\n')")
    L.append("            if _danger_dialog(action, code):")
    L.append("                print('[SAFETY] User APPROVED via dialog.')")
    L.append("                with open(os.path.expanduser('~/.codec/audit.log'),'a') as af: af.write(f'[{time.strftime(\"%Y-%m-%dT%H:%M:%S\")}] APPROVED: {code[:200]}\\n')")
    L.append("            else:")
    L.append("                print('[SAFETY] User DENIED via dialog.')")
    L.append("                with open(os.path.expanduser('~/.codec/audit.log'),'a') as af: af.write(f'[{time.strftime(\"%Y-%m-%dT%H:%M:%S\")}] DENIED: {code[:200]}\\n')")
    L.append("                return 'Command blocked by user. Dangerous command was denied.'")
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
    L.append("        print('[CODEC] Taking screenshot...'); ctx = screenshot_ctx()")
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
    L.append("                c.commit(); c.close(); print('[CODEC] Correction saved.')")
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
    L.append("    if rows: rows.reverse(); prev = [{'role':r,'content':ct} for r,ct in rows]; print('[CODEC] Loaded '+str(len(prev))+' messages from previous sessions.')")
    L.append("except: pass")
    L.append("")
    L.append("h = [{'role':'system','content':SYS_MSG}] + prev")
    L.append("ss = 'ON' if STREAMING else 'OFF'")
    L.append("O = chr(27)+'[38;2;232;113;26m'")
    L.append("D = chr(27)+'[38;2;80;80;80m'")
    L.append("W = chr(27)+'[38;2;200;200;200m'")
    L.append("R = chr(27)+'[0m'")
    L.append("print(O+'    ╔══════════════════════════════════════════════════╗')")
    L.append("print(O+'    ║                                                  ║')")
    L.append("print(O+'    ║    ██████  ██████  ██████  ███████  ██████        ║')")
    L.append("print(O+'    ║   ██      ██    ██ ██   ██ ██      ██            ║')")
    L.append("print(O+'    ║   ██      ██    ██ ██   ██ █████   ██            ║')")
    L.append("print(O+'    ║   ██      ██    ██ ██   ██ ██      ██            ║')")
    L.append("print(O+'    ║    ██████  ██████  ██████  ███████  ██████        ║')")
    L.append("print(O+'    ║                                          v1.0    ║')")
    L.append("print(O+'    ╠══════════════════════════════════════════════════╣')")
    L.append("print(O+'    ║'+W+'  F18 voice   F16 text   ** screen   ++ doc   '+O+'║')")
    L.append("print(O+'    ║'+W+'  Hey " + wake_word_label + " = wake word" + " " * max(0, 8 - len(wake_word_label)) + "  type exit to close    '+O+'║')")
    L.append("print(O+'    ╠══════════════════════════════════════════════════╣')")
    L.append("print(O+'    ║'+D+'  Stream='+ss+'  Memory=ON  Skills=ON             '+O+'║')")
    L.append("print(O+'    ╚══════════════════════════════════════════════════╝'+R)")
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
    L.append("                if u.lower() in ['exit','quit','bye']: cleanup(); print(chr(10)+'[CODEC Session ended]'); sys.exit(0)")
    L.append("                process_input(u, h); break")
    L.append("        except (KeyboardInterrupt, EOFError): cleanup(); print(chr(10)+'[CODEC Session ended]'); sys.exit(0)")

    return "\n".join(L)
