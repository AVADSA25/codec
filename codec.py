#!/usr/bin/env python3
import signal
signal.signal(signal.SIGINT, lambda *a: None)
signal.signal(signal.SIGTERM, lambda *a: None)
"""CODEC v1.0 | F13=on/off | F18=voice | F16=text | *=screenshot | +=doc | Wake word"""
import logging, threading, tempfile, subprocess, sys, os, time, sqlite3, json, re, base64, shutil
from datetime import datetime
from pynput import keyboard

log = logging.getLogger(__name__)

try:
    from codec_audit import log_event
except ImportError:
    def log_event(*a, **kw): pass

# Ensure homebrew tools are on PATH (PM2 may not inherit full shell PATH)
_BREW = "/opt/homebrew/bin"
if _BREW not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _BREW + ":" + os.environ.get("PATH", "")
SOX = shutil.which("sox") or "sox"

# ── CONFIG (single source of truth: codec_config.py) ─────────────────────────
from codec_config import (
    cfg as _cfg,
    QWEN_BASE_URL, QWEN_MODEL, LLM_API_KEY, LLM_KWARGS, LLM_PROVIDER,
    QWEN_VISION_URL, QWEN_VISION_MODEL,
    TTS_ENGINE, KOKORO_URL, KOKORO_MODEL, TTS_VOICE,
    STT_ENGINE, WHISPER_URL,
    DB_PATH, Q_TERMINAL_TITLE, TASK_QUEUE_FILE, DRAFT_TASK_FILE, SESSION_ALIVE, SKILLS_DIR,
    STREAMING, WAKE_WORD, WAKE_PHRASES, WAKE_ENERGY, WAKE_CHUNK_SEC,
)

# Vision — prefer Gemini Flash (fast cloud), fall back to local Qwen VL
# These are codec.py-specific (not in codec_config)
GEMINI_API_KEY    = _cfg.get("gemini_api_key", os.environ.get("GEMINI_API_KEY", ""))
VISION_PROVIDER   = _cfg.get("vision_provider", "gemini" if GEMINI_API_KEY else "local")
DRAFT_KEYWORDS_CFG = _cfg.get("draft_keywords", [])

# ─��� SHARED (from codec_core.py — single source of truth) ─────────────────────
import codec_core as _core
from codec_core import (
    strip_think, is_draft, needs_screen, DRAFT_KEYWORDS, SCREEN_KEYWORDS,
    init_db, save_task, get_memory, get_recent_conversations,
    loaded_skills, load_skills, run_skill,
    transcribe, speak_text, focused_app, get_text_dialog,
    terminal_session_exists, close_session,
)
from codec_agent import run_session_in_terminal

from codec_overlays import show_overlay, show_recording_overlay, show_processing_overlay, show_toggle_overlay
from codec_identity import CODEC_VOICE_PROMPT

# ── SKILLS (codec.py-specific: ranked matching) ──────────────────────────────

def check_skills_ranked(task):
    """Return all matching skills, sorted by trigger length (best match first)."""
    low = task.lower()
    matches = []
    seen = set()
    for skill in loaded_skills:
        for trigger in skill['triggers']:
            if trigger in low and skill['name'] not in seen:
                matches.append((len(trigger), skill))
                seen.add(skill['name'])
    matches.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in matches]

def check_skill(task):
    ranked = check_skills_ranked(task)
    return ranked[0] if ranked else None

# ── VISION (Gemini Flash or local Qwen VL) ──────────────────────────────────
def _gemini_vision(img_b64, prompt, max_tokens=800):
    """Call Gemini Flash vision API. Fast, reliable, free tier."""
    import requests
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [
            {"inlineData": {"mimeType": "image/png", "data": img_b64}},
            {"text": prompt}
        ]}],
        "generationConfig": {"maxOutputTokens": max_tokens}
    }
    r = requests.post(url, json=payload, timeout=30)
    if r.status_code == 200:
        candidates = r.json().get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            if parts:
                return parts[0].get("text", "").strip()
    else:
        print(f"[CODEC] Gemini error {r.status_code}: {r.text[:200]}")
    return ""

def _local_vision(img_b64, prompt, max_tokens=800):
    """Call local Qwen VL vision API (fallback)."""
    import requests
    r = requests.post(f"{QWEN_VISION_URL}/chat/completions",
        json={"model": QWEN_VISION_MODEL,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                {"type": "text", "text": prompt}
            ]}], "max_tokens": max_tokens}, timeout=60)
    if r.status_code == 200:
        return r.json()["choices"][0]["message"].get("content", "").strip()
    return ""

def vision_describe(img_b64, prompt="Read all visible text on this screen. Include app name, window title, and all message/content text. Output raw text only.", max_tokens=800):
    """Route vision to Gemini or local based on config."""
    if VISION_PROVIDER == "gemini" and GEMINI_API_KEY:
        result = _gemini_vision(img_b64, prompt, max_tokens)
        if result:
            return result
        print("[CODEC] Gemini failed, falling back to local vision...")
    return _local_vision(img_b64, prompt, max_tokens)

def screenshot_ctx():
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.close()
        subprocess.run(["screencapture", "-x", tmp.name], timeout=5)
        if not os.path.exists(tmp.name) or os.path.getsize(tmp.name) < 1000:
            return ""
        with open(tmp.name, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        os.unlink(tmp.name)
        provider = "Gemini Flash" if (VISION_PROVIDER == "gemini" and GEMINI_API_KEY) else "Qwen VL"
        print(f"[CODEC] Reading screen via {provider}...")
        content = vision_describe(img_b64)
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
    "last_minus": 0.0,
    "doc_ctx": "",
}

# ── DISPATCH LOCK — only one dispatch at a time, prevents feedback loops ──
_dispatch_lock = threading.Lock()
_dispatch_cooldown = 0.0  # timestamp: ignore wake words until this time

# ── VOICE CONVERSATION SESSION (persistent across F18 presses) ──────────────
voice_session = {
    "messages": [],      # [{role, content}, ...] — full conversation history
    "started": None,     # ISO timestamp of session start
    "turn_count": 0,     # number of exchanges
}

def reset_voice_session():
    """Clear voice conversation history (called on F13 toggle)."""
    voice_session["messages"] = []
    voice_session["started"] = None
    voice_session["turn_count"] = 0
    print("[CODEC] Voice session reset")

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
    global _dispatch_cooldown
    if not _dispatch_lock.acquire(blocking=False):
        print(f"[CODEC] Dispatch BLOCKED (already processing): {task[:60]}")
        return
    try:
        _dispatch_inner(task)
    finally:
        # Post-dispatch cooldown — TTS is now blocking so audio is already done
        # This extra 5s is just a safety buffer for echo/reverb decay
        _dispatch_cooldown = time.time() + 5.0
        _dispatch_lock.release()

def _dispatch_inner(task):
    app = focused_app()
    log_event("command", "open-codec", f"Voice dispatch: {task[:80]}")
    print(f"[CODEC] Task: {task[:80]} | App: {app}")
    _safe_task = task[:50].replace('\\', '\\\\').replace('"', '\\"')
    subprocess.Popen(["osascript", "-e", f'display notification "Heard: {_safe_task}" with title "CODEC"'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Check skills — try ranked matches, fall through if skill returns None
    skill_fired = False
    skill_result = None
    if len(task) < 500:
        for skill in check_skills_ranked(task):
            result = run_skill(skill, task, app)
            if result is not None:
                push(lambda: show_overlay('Skill: ' + skill['name'], '#E8711A', 2000))
                speak_text(result)
                subprocess.Popen(["osascript", "-e", f'display notification "{str(result)[:80]}" with title "CODEC Skill"'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print(f"[CODEC] Skill response: {str(result)[:100]}")
                skill_fired = True
                skill_result = str(result)
                # Add skill exchange to voice session for continuity
                voice_session["messages"].append({"role": "user", "content": task})
                voice_session["messages"].append({"role": "assistant", "content": f"[Skill: {skill['name']}] {skill_result}"})
                voice_session["turn_count"] += 1
                # Save to shared memory
                try:
                    from codec_memory import CodecMemory
                    cm = CodecMemory()
                    cm.add("user", task, source="voice")
                    cm.add("assistant", skill_result[:500], source="voice")
                except Exception as e:
                    log.warning(f"[CODEC] Memory save failed after skill: {e}")
                # After skill fires, grab screen context (skill may have opened browser etc)
                try:
                    time.sleep(2)  # give browser/app time to load
                    screen = screenshot_ctx()
                    if screen and len(screen) > 50:
                        voice_session["messages"].append({
                            "role": "system",
                            "content": f"[SCREEN AFTER SKILL: The user's screen now shows: {screen[:1000]}]"
                        })
                        print(f"[CODEC] Post-skill screen captured: {len(screen)} chars")
                except Exception as e:
                    print(f"[CODEC] Post-skill screenshot failed: {e}")
                return
            print(f"[CODEC] Skill {skill['name']} returned None, trying next...")

    if is_draft(task):
        push(lambda: show_overlay('Reading screen...', '#E8711A', 2000))
        ctx = screenshot_ctx()
        push(lambda: show_processing_overlay('Drafting your message...', 15000))
        with open(DRAFT_TASK_FILE, "w") as f:
            json.dump({"task": task, "ctx": ctx, "app": app}, f)
        print(f"[CODEC] Draft queued for watcher")
        return

    rid = save_task(task, app)

    # ── Build system prompt with memory ─────────────────────────────────
    mem = get_memory(5)
    mem_ctx = ""
    try:
        from codec_memory import CodecMemory
        cm = CodecMemory()
        targeted = cm.get_context(task, n=5)
        if targeted:
            mem_ctx += f"\n\n[MEMORY — RELEVANT PAST CONVERSATIONS]\n{targeted}\n[END MEMORY]"
        recent = cm.search_recent(days=3, limit=5)
        if recent:
            lines = ["[RECENT MEMORY — LAST 3 DAYS]"]
            for r in recent:
                ts = r["timestamp"][:16].replace("T", " ")
                snippet = r["content"][:200].replace("\n", " ")
                lines.append(f"  [{ts}] {r['role'].upper()}: {snippet}")
            lines.append("[END RECENT MEMORY]")
            mem_ctx += "\n\n" + "\n".join(lines)
    except Exception as e:
        log.warning("Memory context retrieval failed: %s", e)
    sys_p = CODEC_VOICE_PROMPT
    if mem: sys_p += "\n\n" + mem
    if mem_ctx: sys_p += mem_ctx
    safe_sys = sys_p.replace('\n', ' ')

    # ── Open terminal session (the real CODEC session window) ───────────
    with open(TASK_QUEUE_FILE, "w") as f:
        json.dump({"task": task, "app": app, "ts": datetime.now().isoformat()}, f)

    if terminal_session_exists():
        print("[CODEC] Queued to existing session")
    else:
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_session_in_terminal(safe_sys, session_id, task)

    # ── Quick voice reply (immediate feedback while terminal loads) ─────
    if not voice_session["started"]:
        voice_session["started"] = datetime.now().isoformat()

    # Add current user message to session
    voice_session["messages"].append({"role": "user", "content": task})

    # Build LLM messages: system + conversation history (keep last 20 turns)
    llm_messages = [{"role": "system", "content": sys_p}]
    # Trim to last 10 messages to keep prompt fast on 35B model
    history = voice_session["messages"][-10:]
    llm_messages.extend(history)

    push(lambda: show_processing_overlay('Thinking...', 15000))
    try:
        import requests as _llm_req
        headers = {}
        if LLM_API_KEY:
            headers["Authorization"] = f"Bearer {LLM_API_KEY}"
        payload = {
            "model": QWEN_MODEL,
            "messages": llm_messages,
            "max_tokens": 400,
            "temperature": 0.7,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        payload.update(LLM_KWARGS)
        r = _llm_req.post(f"{QWEN_BASE_URL}/chat/completions", json=payload, headers=headers, timeout=120)
        if r.status_code == 200:
            data = r.json()
            answer = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            answer = strip_think(answer).strip()
            if answer:
                print(f"[CODEC] Voice reply (turn {voice_session['turn_count']+1}): {answer[:120]}")
                log_event("tts", "open-codec", f"TTS: {answer[:60]}", {"text_len": len(answer)})
                # Add assistant response to session history
                voice_session["messages"].append({"role": "assistant", "content": answer})
                voice_session["turn_count"] += 1
                # Save response to DB
                try:
                    c = sqlite3.connect(DB_PATH)
                    c.execute("UPDATE sessions SET response=? WHERE id=?", (answer[:500], rid))
                    c.commit(); c.close()
                except Exception as e:
                    log.warning(f"[CODEC] DB save failed: {e}")
                # Save to shared memory (same store as Chat)
                try:
                    cm = CodecMemory()
                    cm.add("user", task, source="voice")
                    cm.add("assistant", answer, source="voice")
                except Exception as e:
                    log.warning(f"[CODEC] Memory save failed after LLM: {e}")
                speak_text(answer)
                _safe_ans = answer[:80].replace('\\', '\\\\').replace('"', '\\"')
                subprocess.Popen(["osascript", "-e",
                    f'display notification "{_safe_ans}" with title "CODEC"'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                print("[CODEC] Voice LLM returned empty response")
                speak_text("Sorry, I didn't get a response.")
        else:
            print(f"[CODEC] Voice LLM error: {r.status_code} {r.text[:200]}")
            speak_text("Sorry, the language model is not responding.")
    except Exception as e:
        log.error("Voice LLM call failed: %s", e)
        import traceback; traceback.print_exc()
        speak_text("Sorry, something went wrong.")

# ── DOCUMENT INPUT ────────────────────────────────────────────────────────────
def do_document_input():
    push(lambda: show_overlay('Select document...', '#E8711A', 3000))
    try:
        r = subprocess.run(["osascript", "-e",
            'set f to POSIX path of (choose file with prompt "Select a document for CODEC:" of type {"public.item"})'],
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
                with open(filepath, "rb") as imgf:
                    img_b64 = base64.b64encode(imgf.read()).decode()
                content_text = vision_describe(img_b64, "Describe this image in detail. Read any text visible.", 1000)[:5000]
            except Exception as e:
                print(f"[CODEC] Image vision error: {e}")
        elif ext == '.pdf':
            try:
                import fitz
                doc = fitz.open(filepath)
                content_text = "\n".join(p.get_text() for p in doc[:5])[:5000]
                doc.close()
                print(f"[CODEC] PDF extracted: {len(content_text)} chars from {len(doc)} pages")
            except Exception as e:
                print(f"[CODEC] PDF extraction error: {e}")

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
    push(lambda: show_overlay('Analyzing screen...', '#E8711A', 3000))
    ctx = screenshot_ctx()
    if not ctx:
        push(lambda: show_overlay('Screenshot failed', '#ff3333', 2000))
        return
    print(f"[CODEC] Screenshot captured ({len(ctx)} chars)")
    # Show brief summary of what was captured, then open question dialog
    summary = ctx[:120].replace('"', '\\"').replace('\n', ' ')
    try:
        r = subprocess.run(["osascript", "-e",
            f'tell application "System Events"\nset frontmost of first process whose frontmost is true to true\nend tell\n'
            f'set t to text returned of (display dialog '
            f'"I captured your screen:\\n\\n{summary}…\\n\\nWhat would you like to know about it?" '
            f'default answer "" with title "CODEC Screenshot" '
            f'buttons {{"Cancel","Ask"}} default button "Ask")'],
            capture_output=True, text=True, timeout=120)
        question = r.stdout.strip()
        if question:
            task = question + " [SCREEN CONTEXT: " + ctx[:800] + "]"
            dispatch(task)
        else:
            # User cancelled — save for later F18/F16 AND inject into voice session
            state["screen_ctx"] = ctx
            voice_session["messages"].append({
                "role": "system",
                "content": f"[SCREEN CAPTURE: The user's screen currently shows: {ctx[:1000]}]"
            })
            push(lambda: show_overlay('Screenshot saved — use voice or text to ask', '#E8711A', 3000))
    except Exception as e:
        print(f"[CODEC] Screenshot dialog error: {e}")
        state["screen_ctx"] = ctx

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
        [SOX, "-t", "coreaudio", "default", "-r", "16000", "-c", "1", "-b", "16", "-e", "signed-integer", state["audio_path"]],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    state["rec_proc"] = rec
    print("[CODEC] Recording...")

def do_stop_voice():
    audio = state.get("audio_path")
    rec = state.get("rec_proc")
    if rec:
        try: rec.terminate(); rec.wait(timeout=3)
        except Exception as e: log.debug("Recording process cleanup failed: %s", e)
    state["rec_proc"] = None; state["recording"] = False
    ovl = state.get("overlay_proc")
    if ovl:
        try: ovl.terminate()
        except Exception as e: log.debug("Overlay process cleanup failed: %s", e)
        state["overlay_proc"] = None
    if not audio or not os.path.exists(audio): return
    if os.path.getsize(audio) < 1000:
        try: os.unlink(audio)
        except Exception as e: log.debug("Audio file cleanup failed: %s", e)
        return
    print("[CODEC] Transcribing...")
    push(lambda: show_processing_overlay('Transcribing...', 2000))
    task = transcribe(audio)
    if not task: print("[CODEC] No speech detected"); return
    print(f"[CODEC] Heard: {task}")
    if state.get("screen_ctx"):
        task = task + " [SCREEN CONTEXT: " + state["screen_ctx"][:800] + "]"
        state["screen_ctx"] = ""
    dispatch(task)

# ── WAKE WORD LISTENER ───────────────────────────────────────────────────────
def wake_word_listener():
    import requests as req_wake
    sample_rate = 16000
    chunk_sec = WAKE_CHUNK_SEC
    # Find the Anker webcam mic — always use it for wake word regardless of BT devices
    wake_device = "default"
    try:
        import sounddevice as sd
        for i, d in enumerate(sd.query_devices()):
            if d['max_input_channels'] > 0 and 'anker' in d['name'].lower():
                wake_device = d['name']
                break
    except Exception as e: log.debug("Wake mic device detection failed: %s", e)
    print(f"[CODEC] Wake word listener started (mic: {wake_device}). Say 'Hey CODEC' to activate.")
    _wake_diag_done = False
    while True:
        if not WAKE_WORD or state["recording"]:
            time.sleep(0.3); continue
        # Skip while TTS is actively playing (prevents mic hearing our own voice)
        if _core.tts_playing:
            time.sleep(0.3); continue
        # Skip for 8s after TTS finishes (audio echo / reverb decay)
        if _core.tts_finished_at and (time.time() - _core.tts_finished_at) < 8.0:
            time.sleep(0.3); continue
        # Skip wake processing during dispatch cooldown (prevents hearing our own TTS)
        if time.time() < _dispatch_cooldown:
            time.sleep(0.3); continue
        # Skip if a dispatch is already in progress
        if _dispatch_lock.locked():
            time.sleep(0.3); continue
        try:
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False); tmp.close()
            proc = subprocess.run(
                [SOX, "-t", "coreaudio", wake_device, "-r", str(sample_rate), "-c", "1",
                 "-b", "16", "-e", "signed-integer", tmp.name, "trim", "0", str(chunk_sec)],
                timeout=int(chunk_sec) + 3, capture_output=True)
            if not os.path.exists(tmp.name) or os.path.getsize(tmp.name) < 500:
                try: os.unlink(tmp.name)
                except Exception as e: log.debug("Wake temp file cleanup failed: %s", e)
                continue
            # Check actual audio energy
            try:
                import wave, numpy as np
                wf = wave.open(tmp.name, 'rb')
                data = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
                wf.close()
                energy = np.abs(data).mean()
            except Exception as e:
                log.debug("Wake audio energy check failed: %s", e)
                energy = 0
            if not _wake_diag_done or energy > 80:
                print(f"[CODEC] Wake mic: energy={energy:.0f} (threshold={WAKE_ENERGY})")
                _wake_diag_done = True
            if energy < WAKE_ENERGY:
                try: os.unlink(tmp.name)
                except Exception as e: log.debug("Wake temp file cleanup failed: %s", e)
                continue
            try:
                with open(tmp.name, "rb") as f:
                    r = req_wake.post(WHISPER_URL,
                        files={"file": ("wake.wav", f, "audio/wav")},
                        data={"model": "mlx-community/whisper-large-v3-turbo", "language": "en"},
                        timeout=10)
                if r.status_code == 200:
                    text = r.json().get("text", "").lower().strip()
                    if not text or len(text) < 2:
                        continue
                    # Filter Whisper hallucinations (repetitive gibberish)
                    if len(text) > 120:
                        continue
                    print(f"[CODEC] Wake heard: '{text}'")
                    # Simple keyword match — if "codec/codex/kodak/kodec" appears anywhere, it's a wake
                    _WAKE_KEYWORDS = ["codec", "codex", "kodak", "kodec", "kodak", "co-dec", "caudec", "codag"]
                    _matched = any(kw in text for kw in _WAKE_KEYWORDS)
                    if _matched:
                        log_event("voice", "open-codec", "Wake word detected")
                        # Auto-activate if not already on
                        if not state["active"]:
                            state["active"] = True
                            push(lambda: show_toggle_overlay(True, "F18=voice | **=screen | --=chat"))
                        command = text
                        # Strip wake keywords and common prefixes
                        for kw in _WAKE_KEYWORDS + ["hey", "and", "hay", "eh", "ay"]:
                            command = command.replace(kw, "").strip()
                        command = re.sub(r'^[\s,.\-]+|[\s,.\-]+$', '', command)
                        if len(command) > 3:
                            print(f"[CODEC] Wake + command: {command}")
                            push(lambda: show_overlay('Heard you!', '#E8711A', 1500))
                            push(lambda cmd=command: dispatch(cmd))
                        else:
                            print("[CODEC] Wake word detected! Listening...")
                            push(lambda: show_overlay('Listening...', '#E8711A', 5000))
                            # Record follow-up command (8 seconds)
                            tmp2 = tempfile.NamedTemporaryFile(suffix=".wav", delete=False); tmp2.close()
                            subprocess.run(
                                [SOX, "-t", "coreaudio", wake_device, "-r", str(sample_rate), "-c", "1",
                                 "-b", "16", "-e", "signed-integer", tmp2.name, "trim", "0", "8"],
                                timeout=12, capture_output=True)
                            task = transcribe(tmp2.name)
                            if task:
                                print(f"[CODEC] Heard: {task}")
                                push(lambda t=task: dispatch(t))
            except Exception as e:
                print(f"[CODEC] Wake whisper error: {e}")
            finally:
                try: os.unlink(tmp.name)
                except Exception as e: log.debug("Wake temp file cleanup failed: %s", e)
        except Exception as e:
            print(f"[CODEC] Wake listener error: {e}")
            time.sleep(0.5)
        time.sleep(0.1)

# ── KEYBOARD ──────────────────────────────────────────────────────────────────
def on_press(key):
    now = time.time()
    if key == keyboard.Key.f13:
        if now - state["last_f13"] < 0.8: return
        state["last_f13"] = now
        if state["active"]:
            state["active"] = False
            push(lambda: show_toggle_overlay(False))
            push(close_session)
            reset_voice_session()
            print("[CODEC] OFF")
        else:
            state["active"] = True
            reset_voice_session()
            push(lambda: show_toggle_overlay(True, "F18=voice  F16=text  **=screen  ++=doc  --=chat"))
            print("[CODEC] ON -- F18=voice | F16=text | *=screen | +=doc | --=chat")
        return
    if not state["active"]: return
    if key == keyboard.Key.f16:
        if not state["recording"]: push(do_text)
        return
    if key == keyboard.Key.f18:
        if not state["recording"]:
            state["recording"] = True
            push(do_start_recording)
            state["overlay_proc"] = show_recording_overlay('F18')
        return
    if hasattr(key, 'char') and key.char == '*':
        if now - state["last_star"] < 0.35:
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
    if hasattr(key, 'char') and key.char == '-':
        if now - state.get("last_minus", 0.0) < 0.5:
            print("[CODEC] Minus x2 -- live chat mode")
            voice_url = _cfg.get("voice_url", "http://localhost:8090/voice?auto=1")
            push(lambda: show_overlay('Live Chat connecting...', '#E8711A', 3000))
            subprocess.Popen(["open", "-a", "Google Chrome", voice_url])
            state["last_minus"] = 0.0
            return
        state["last_minus"] = now
        return

def on_release(key):
    if key == keyboard.Key.f18 and state["recording"]:
        push(do_stop_voice)

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    from codec_logging import setup_logging
    setup_logging()
    init_db()
    for f in [SESSION_ALIVE, TASK_QUEUE_FILE, DRAFT_TASK_FILE]:
        try: os.unlink(f)
        except Exception as e: log.debug("Startup file cleanup failed (%s): %s", f, e)

    stream_label = "ON" if STREAMING else "OFF"
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
    ║{W}  F13 toggle   F18 voice   ** screen       {O}║
    ║{W}  F16 text     ++ doc     -- chat          {O}║
    ║{W}  Hey CODEC = wake word (hands-free)           {O}║
    ╠═══════════════════════════════════════════╣
    ║{D}  Stream={stream_label}  Wake={wake_label}  Skills=ON            {O}║
    ╚═══════════════════════════════════════════╝{R}""")

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
