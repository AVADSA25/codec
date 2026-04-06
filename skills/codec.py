#!/usr/bin/env python3
import signal
signal.signal(signal.SIGINT, lambda *a: None)
signal.signal(signal.SIGTERM, lambda *a: None)
"""CODEC v1.0 | F13=on/off | F18=voice | F16=text | *=screenshot | +=doc | Wake word"""
import threading, tempfile, subprocess, sys, os, time, sqlite3, json, re, base64
from datetime import datetime
from pynput import keyboard

# ── CONFIG (load from ~/.codec/config.json or use defaults) ───────────────────
CONFIG_PATH = os.path.expanduser("~/.codec/config.json")
_cfg = {}
if os.path.exists(CONFIG_PATH):
    try:
        with open(CONFIG_PATH) as _f: _cfg = json.load(_f)
        print(f"[CODEC] Config loaded from {CONFIG_PATH}")
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

# STT
STT_ENGINE        = _cfg.get("stt_engine", "whisper_http")
WHISPER_URL       = _cfg.get("stt_url", "http://localhost:8084/v1/audio/transcriptions")

# Paths
DB_PATH            = os.path.expanduser("~/.q_memory.db")
Q_TERMINAL_TITLE   = "CODEC Session"
TASK_QUEUE_FILE    = "/tmp/q_task_queue.txt"
DRAFT_TASK_FILE    = "/tmp/q_draft_task.json"
SESSION_ALIVE      = "/tmp/q_session_alive"
SKILLS_DIR         = os.path.expanduser("~/.codec/skills")

# Features
STREAMING          = _cfg.get("streaming", True)
WAKE_WORD          = _cfg.get("wake_word_enabled", True)
WAKE_PHRASES       = _cfg.get("wake_phrases", ['hey codec', 'hey', 'okay codec', 'hey codex', 'hey coda', 'hey queue'])
WAKE_ENERGY        = _cfg.get("wake_energy", 200)
WAKE_CHUNK_SEC     = _cfg.get("wake_chunk_sec", 3.0)
DRAFT_KEYWORDS_CFG = _cfg.get("draft_keywords", [])


# ── SHARED (from codec_core.py — single source of truth) ────────────────��────
from codec_core import (
    strip_think, is_draft, needs_screen, DRAFT_KEYWORDS, SCREEN_KEYWORDS,
    init_db, save_task, get_memory, get_recent_conversations,
    loaded_skills, load_skills, run_skill,
    transcribe, speak_text, focused_app, get_text_dialog,
    terminal_session_exists, close_session,
)
from codec_agent import run_session_in_terminal

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
def check_skill(task):
    low = task.lower()
    best_skill = None
    best_len = 0
    for skill in loaded_skills:
        for trigger in skill['triggers']:
            if trigger in low and len(trigger) > best_len:
                best_skill = skill
                best_len = len(trigger)
    return best_skill

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
        print("[CODEC] Reading screen via Vision...")
        r = requests.post(f"{QWEN_VISION_URL}/chat/completions",
            json={"model": QWEN_VISION_MODEL,
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                    {"type": "text", "text": "Read all visible text on this screen. Include app name, window title, and all message/content text. Output raw text only."}
                ]}], "max_tokens": 800}, timeout=60)
        if r.status_code == 200:
            content = r.json()["choices"][0]["message"].get("content", "").strip()
            if content:
                print(f"[CODEC] Screen context: {len(content)} chars")
                return content[:2000]
    except Exception as e:
        print(f"[CODEC] Vision error: {e}")
    return ""

# ── STATE ─────────────────────────────────────────────────────────────────────
state = {
    "active": False,
    "recording": False,
    "rec_proc": None,
    "audio_path": None,
    "last_f13": 0.0,
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
                print(f"[CODEC] Error: {e}")
                import traceback; traceback.print_exc()
        else:
            time.sleep(0.05)

# ── DISPATCH ──────────────────────────────────────────────────────────────────
def dispatch(task):
    app = focused_app()
    print(f"[CODEC] Task: {task[:80]} | App: {app}")
    subprocess.Popen(["osascript", "-e", f'display notification "Heard: {task[:50]}" with title "CODEC"'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Check skills — skip if task is very long (document content attached)
    if len(task) < 500:
        skill = check_skill(task)
        if skill:
            result = run_skill(skill, task, app)
            if result is not None:
                push(lambda: show_overlay('Skill: ' + skill['name'], '#E8711A', 2000))
                speak_text(result)
                subprocess.Popen(["osascript", "-e", f'display notification "{str(result)[:80]}" with title "CODEC Skill"'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print(f"[CODEC] Skill response: {str(result)[:100]}")
                return

    if is_draft(task):
        push(lambda: show_overlay('Reading screen...', '#E8711A', 2000))
        ctx = screenshot_ctx()
        with open(DRAFT_TASK_FILE, "w") as f:
            json.dump({"task": task, "ctx": ctx, "app": app}, f)
        print(f"[CODEC] Draft queued for watcher")
        return

    rid = save_task(task, app)
    mem = get_memory(5)
    sys_p = "You are CODEC, a JARVIS-class AI assistant on Mac Studio M1 Ultra. ALWAYS respond in English only. Never respond in Chinese or any other language unless explicitly asked to translate. Answer in 1-3 sentences. Be natural and conversational like a smart friend. Add useful details when relevant. Full computer access. Never say cannot."
    if mem: sys_p += "\n\n" + mem
    safe_sys = sys_p.replace("'","").replace('"','').replace('\n',' ')

    with open(TASK_QUEUE_FILE, "w") as f:
        f.write(json.dumps({"task": task, "app": app, "ts": datetime.now().isoformat()}))

    if terminal_session_exists():
        print("[CODEC] Queued to existing session")
        return

    session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_session_in_terminal(safe_sys, session_id, task)

# ── DOCUMENT INPUT ────────────────────────────────────────────────────────────
def do_document_input():
    push(lambda: show_overlay('Select document...', '#E8711A', 3000))
    try:
        r = subprocess.run(["osascript", "-e",
            'set f to POSIX path of (choose file with prompt "Select a document for Q:" of type {"public.item"})'],
            capture_output=True, text=True, timeout=60)
        filepath = r.stdout.strip()
        if not filepath:
            print("[CODEC] No file selected"); return
        print(f"[CODEC] Document: {filepath}")
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
            print(f"[CODEC] Document dispatched ({len(content_text)} chars)")
            dispatch(task)
        else:
            push(lambda: show_overlay('Could not read document', '#ff3333', 2000))
    except Exception as e:
        print(f"[CODEC] Document error: {e}")

# ── SCREENSHOT SHORTCUT ──────────────────────────────────────────────────────
def do_screenshot_question():
    push(lambda: show_overlay('Screenshot captured  F18=voice  F16=text', '#E8711A', 5000))
    ctx = screenshot_ctx()
    if ctx:
        state["screen_ctx"] = ctx
        print(f"[CODEC] Screenshot captured ({len(ctx)} chars). Use F18/F16 to ask about it.")
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
    print("[CODEC] Recording...")

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
    print("[CODEC] Transcribing...")
    push(lambda: show_overlay('Transcribing...', '#E8711A', 2000))
    task = transcribe(audio)
    if not task: print("[CODEC] No speech detected"); return
    print(f"[CODEC] Heard: {task}")
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
    print("[CODEC] Wake word listener started. Say 'Hey CODEC' to activate.")
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
                        if len(command) > 3:
                            print(f"[CODEC] Wake + command: {command}")
                            push(lambda: show_overlay('Heard you!', '#E8711A', 1500))
                            push(lambda cmd=command: dispatch(cmd))
                        else:
                            print("[CODEC] Wake word detected! Listening...")
                            push(lambda: show_overlay('Listening...', '#E8711A', 5000))
                            full_audio = sd.rec(int(8 * sample_rate), samplerate=sample_rate, channels=1, dtype='int16')
                            sd.wait()
                            tmp2 = tempfile.NamedTemporaryFile(suffix=".wav", delete=False); tmp2.close()
                            sf.write(tmp2.name, full_audio, sample_rate)
                            task = transcribe(tmp2.name)
                            if task:
                                print(f"[CODEC] Heard: {task}")
                                push(lambda t=task: dispatch(t))
            except: pass
            finally:
                try: os.unlink(tmp.name)
                except: pass
        except: time.sleep(0.5)
        time.sleep(0.1)

# ── KEYBOARD ──────────────────────────────────────────────────────────────────
def on_press(key):
    now = time.time()
    if key == keyboard.Key.f13:
        if now - state["last_f13"] < 0.8: return
        state["last_f13"] = now
        if state["active"]:
            state["active"] = False
            push(lambda: show_overlay('CODEC OFF', '#ff3333', 1500))
            push(close_session)
            print("[CODEC] OFF")
        else:
            state["active"] = True
            push(lambda: show_overlay('CODEC ON  F18=voice  F16=text  **=screen  ++=doc', '#E8711A', 3000))
            print("[CODEC] ON -- F18=voice | F16=text | *=screen | +=doc")
        return
    if not state["active"]: return
    if key == keyboard.Key.f16:
        if not state["recording"]: push(do_text)
        return
    if key == keyboard.Key.f18:
        if not state["recording"]:
            state["recording"] = True
            push(do_start_recording)
            push(lambda: show_overlay('REC  release F18 to send', '#E8711A', 30000))
        return
    if hasattr(key, 'char') and key.char == '*':
        if now - state["last_star"] < 0.5:
            print("[CODEC] Star x2 -- screenshot mode")
            push(do_screenshot_question)
            state["last_star"] = 0.0
            return
        state["last_star"] = now
        return
    if hasattr(key, 'char') and key.char == '+':
        if now - state.get("last_plus", 0.0) < 0.5:
            print("[CODEC] Plus x2 -- document mode")
            push(do_document_input)
            state["last_plus"] = 0.0
            return
        state["last_plus"] = now
        return

def on_release(key):
    if key == keyboard.Key.f18 and state["recording"]:
        push(do_stop_voice)

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    init_db()
    for f in [SESSION_ALIVE, TASK_QUEUE_FILE, DRAFT_TASK_FILE]:
        try: os.unlink(f)
        except: pass

    stream_label = "ON" if STREAMING else "OFF"
    wake_label = "ON" if WAKE_WORD else "OFF"
    O = "\033[38;2;232;113;26m"
    D = "\033[38;2;80;80;80m"
    W = "\033[38;2;200;200;200m"
    R = "\033[0m"
    print(f"""
{O}    ╔══════════════════════════════════════════════════╗
    ║                                                  ║
    ║    ██████  ██████  ██████  ███████  ██████        ║
    ║   ██      ██    ██ ██   ██ ██      ██            ║
    ║   ██      ██    ██ ██   ██ █████   ██            ║
    ║   ██      ██    ██ ██   ██ ██      ██            ║
    ║    ██████  ██████  ██████  ███████  ██████        ║
    ║                                          v1.0    ║
    ╠══════════════════════════════════════════════════╣
    ║{W}  F13  toggle ON/OFF    **  screenshot + ask    {O}║
    ║{W}  F18  voice command    ++  document analysis   {O}║
    ║{W}  F16  text input       Hey CODEC  wake word        {O}║
    ╠══════════════════════════════════════════════════╣
    ║{D}  Stream={stream_label}  Wake={wake_label}  Memory=ON  Skills=ON       {O}║
    ╚══════════════════════════════════════════════════╝{R}""")

    load_skills()
    print("[CODEC] Whisper: HTTP (port 8084)")
    print("[CODEC] Vision: Qwen VL (port 8082)")
    mem = get_memory(3)
    if mem: print(f"[CODEC] Memory: {mem.count(chr(10))+1} sessions loaded")
    convs = get_recent_conversations(10)
    if convs: print(f"[CODEC] Persistent memory: {len(convs)} messages from past sessions")
    if WAKE_WORD: print("[CODEC] Wake word: ON")
    print("[CODEC] Online. Press F13 to activate.")

    threading.Thread(target=worker, daemon=True).start()
    if WAKE_WORD:
        threading.Thread(target=wake_word_listener, daemon=True).start()

    while True:
        try:
            with keyboard.Listener(on_press=on_press, on_release=on_release) as l:
                l.join()
        except Exception as e:
            print(f"[CODEC] Listener restarting: {e}")
            time.sleep(0.5)

if __name__ == "__main__":
    main()
