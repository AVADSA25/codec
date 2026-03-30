"""CODEC Agent — session script builder and subprocess runner"""
import os
import sys
import json
import tempfile
import subprocess
import logging

from codec_config import (
    QWEN_BASE_URL, QWEN_MODEL, QWEN_VISION_URL, QWEN_VISION_MODEL,
    TTS_VOICE, LLM_API_KEY, LLM_KWARGS, LLM_PROVIDER,
    TTS_ENGINE, KOKORO_URL, KOKORO_MODEL,
    DB_PATH, TASK_QUEUE_FILE, SESSION_ALIVE,
    STREAMING, cfg,
)

log = logging.getLogger('codec')


def build_session_script(safe_sys, session_id):
    """Build the agent session Python script as a string"""
    L = []
    # Resource limits — cap memory and CPU before anything else
    L.append("import resource")
    L.append("try:")
    L.append("    if hasattr(resource, 'RLIMIT_AS'):")
    L.append("        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))")
    L.append("    resource.setrlimit(resource.RLIMIT_CPU, (120, 120))")
    L.append("except Exception: pass")
    L.append("")
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
    L.append("AGENT_SYS = '''You are ' + AGENT_NAME + ', an AI agent with FULL access to a Mac Studio M1 Ultra.")
    L.append("You can execute bash commands and AppleScript to accomplish any task.")
    L.append("RESPOND IN THIS EXACT JSON FORMAT:")
    L.append('{ "thought": "brief plan", "action": "bash" or "applescript" or "done", "code": "command to execute", "summary": "what you did (only when action is done)" }')
    L.append("RULES:")
    L.append("1. For URLs: bash open command. For apps: applescript.")
    L.append("2. Max 8 steps. Execute each fully. Dont say done until ALL complete.")
    L.append("3. NEVER delete files or data without confirmation.")
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
    L.append("    for _attempt in range(3):")
    L.append("        try:")
    L.append("            r = requests.post(QWEN_BASE_URL+'/chat/completions', json=payload, headers=headers, timeout=90)")
    L.append("            if r.status_code == 200:")
    L.append("                resp = extract_content(r.json())")
    L.append("                if resp: return resp")
    L.append("        except: time.sleep(2 ** _attempt)")
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
    L.append("# Import DANGEROUS_PATTERNS from codec_config (single source of truth)")
    L.append("try:")
    L.append("    import sys as _sys; _sys.path.insert(0, os.path.expanduser('~/codec-repo'))")
    L.append("    from codec_config import DANGEROUS_PATTERNS as DANGEROUS")
    L.append("except ImportError:")
    L.append("    DANGEROUS = ['rm -rf','rm -r /','sudo','shutdown','reboot','halt','killall','mkfs','dd if=','chmod 777','| bash','| sh','defaults delete','diskutil erase','launchctl unload','csrutil disable','nvram',':(){ :|:& };:']")
    L.append("")
    L.append("def run_code(action, code):")
    L.append("    try:")
    L.append("        # Safety: check dangerous commands")
    L.append("        cmd_lower = code.lower()")
    L.append("        if any(d.lower() in cmd_lower for d in DANGEROUS):")
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
    L.append("                result['allow'] = True; root.after(1, root.destroy)")
    L.append("            def deny():")
    L.append("                result['allow'] = False; root.after(1, root.destroy)")
    L.append("            abtn = tk.Button(root, text='✓ Allow', bg='#00cc55', fg='#000', font=('Helvetica',13,'bold'), border=0, padx=20, pady=6, command=allow)")
    L.append("            abtn.place(x=w//2-110, y=140, width=100, height=36)")
    L.append("            dbtn = tk.Button(root, text='✗ Deny', bg='#ff4444', fg='#000', font=('Helvetica',13,'bold'), border=0, padx=20, pady=6, command=deny)")
    L.append("            dbtn.place(x=w//2+10, y=140, width=100, height=36)")
    L.append("            root.after(120000, deny)")
    L.append("            root.mainloop()")
    L.append("            return result['allow']")
    L.append("        # Safe commands skip preview")
    L.append("        safe_cmds = ['sqlite3','echo ','cat ','ls ','pwd','date','uptime','whoami','sw_vers','which ','head ','tail ','wc ','grep ','screencapture','defaults read','open -a','open http','tell application']")
    L.append("        is_safe = any(code.strip().lower().startswith(s) for s in safe_cmds) or action == 'applescript'")
    L.append("        if not is_safe and not _cmd_preview(action, code):")
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
    L.append("print(O+'    ║                                   v1.5.0  ║')")
    L.append("print(O+'    ╠═══════════════════════════════════════════╣')")
    L.append("print(O+'    ║'+W+'  " + cfg.get('key_voice', 'f18').upper() + " voice  " + cfg.get('key_text', 'f16').upper() + " text  ** screen  ++ doc   '+O+'║')")
    L.append("print(O+'    ║'+W+'  Hey C = wake word  type exit to close    '+O+'║')")
    L.append("print(O+'    ╠═══════════════════════════════════════════╣')")
    L.append("print(O+'    ║'+D+'  Stream='+ss+'  Memory=ON  Skills=ON          '+O+'║')")
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


def run_session(script_content, task, timeout=120):
    """Run agent session as isolated subprocess — no terminal window (for PWA/background tasks)"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(script_content)
        tmp_path = f.name
    try:
        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, 'CODEC_TASK': task},
        )
        return result.stdout
    except subprocess.TimeoutExpired:
        log.warning(f"Session timed out after {timeout}s for task: {task[:60]}")
        return "Session timed out"
    except Exception as e:
        log.error(f"Session error: {e}")
        return f"Session error: {e}"
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def build_session_params(safe_sys, session_id):
    """Build parameter dict for codec_session.Session — new module-based approach."""
    return {
        "sys_msg": safe_sys,
        "session_id": session_id,
        "qwen_base_url": QWEN_BASE_URL,
        "qwen_model": QWEN_MODEL,
        "qwen_vision_url": QWEN_VISION_URL,
        "qwen_vision_model": QWEN_VISION_MODEL,
        "tts_voice": TTS_VOICE,
        "llm_api_key": LLM_API_KEY,
        "llm_kwargs": LLM_KWARGS,
        "llm_provider": LLM_PROVIDER,
        "tts_engine": TTS_ENGINE,
        "kokoro_url": KOKORO_URL,
        "kokoro_model": KOKORO_MODEL,
        "db_path": DB_PATH,
        "task_queue": TASK_QUEUE_FILE,
        "session_alive": SESSION_ALIVE,
        "streaming": STREAMING,
        "agent_name": cfg.get("agent_name", "C"),
        "key_voice": cfg.get("key_voice", "f18"),
        "key_text": cfg.get("key_text", "f16"),
    }


def run_session_module(safe_sys, session_id, task, timeout=120):
    """Run session using the new codec_session module in a subprocess."""
    params = build_session_params(safe_sys, session_id)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(params, f)
        params_path = f.name
    try:
        repo = os.path.expanduser("~/codec-repo")
        result = subprocess.run(
            [sys.executable, "-c", f"""
import sys, json
sys.path.insert(0, {repr(repo)})
from codec_session import Session
params = json.load(open({repr(params_path)}))
s = Session(**params)
s.run()
"""],
            text=True,
            timeout=timeout,
            env={**os.environ, "CODEC_TASK": task},
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        log.warning(f"Session module timed out after {timeout}s for task: {task[:60]}")
        return False
    except Exception as e:
        log.error(f"Session module error: {e}")
        return False
    finally:
        try:
            os.unlink(params_path)
        except Exception:
            pass
