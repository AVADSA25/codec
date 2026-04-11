#!/usr/bin/env python3
"""
CODEC Dictate — Hold-to-Speak + Live Typing
Hold ⌘R → speak → release → text pasted at cursor
L → live typing mode (words appear in real-time wherever cursor is)

Requirements: pynput, pyautogui, faster-whisper, Pillow, requests
"""

import threading
import tempfile
import subprocess
import sys
import os
import time
import pyautogui
import pyperclip

from pynput import keyboard
from faster_whisper import WhisperModel

# ── CONFIG ──────────────────────────────────────────────────────────────────
WHISPER_MODEL_SIZE = "base"          # tiny / base / small — base is best balance
WHISPER_DEVICE     = "cpu"           # mac uses cpu for faster-whisper
WHISPER_COMPUTE    = "int8"
SAMPLE_RATE        = 16000
CHANNELS           = 1
CHUNK_DURATION_MS  = 30             # ms per audio chunk

# ── STATE ────────────────────────────────────────────────────────────────────
recording        = False
audio_frames     = []
cmd_held         = False
overlay_proc     = None
model            = None
model_loaded     = threading.Event()

# ── HANDS-FREE LIVE DICTATION STATE ─────────────────────────────────────────
live_active      = False
live_overlay     = None
live_thread      = None
live_stop_event  = threading.Event()
live_text_file   = os.path.join(tempfile.gettempdir(), "codec_live_dictate.txt")
# Live dictation triggered by F5 key

# ── WHISPER HALLUCINATION FILTER ──────────────────────────────────────────────
WHISPER_HALLUCINATIONS = {
    "you", "thank you", "thank you.", "thanks.", "thanks for watching.",
    "thank you for watching.", "please subscribe.", "bye.", "the end.",
    "thanks for watching!", "like and subscribe.", "see you next time.",
    "subscribe to the channel.", "please like and subscribe.",
    "subtitles by the amara.org community", "...", "",
}

def is_hallucination(text):
    """Check if transcribed text is a known Whisper hallucination."""
    t = text.strip().lower()
    if not t or len(t) <= 1:
        return True
    if t in WHISPER_HALLUCINATIONS:
        return True
    # Repetitive gibberish (same word 5+ times)
    words = t.split()
    if len(words) >= 5 and len(set(words)) == 1:
        return True
    return False

# ── LOAD WHISPER ─────────────────────────────────────────────────────────────
def load_model():
    global model
    print("[DICTATE] Loading Whisper model...")
    model = WhisperModel(WHISPER_MODEL_SIZE, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE)
    model_loaded.set()
    print("[DICTATE] Whisper ready.")

# ── OVERLAY (tiny floating window) ───────────────────────────────────────────

def show_overlay():
    global overlay_proc
    try:
        script = """
import tkinter as tk
root = tk.Tk()
root.overrideredirect(True)
root.attributes('-topmost', True)
root.attributes('-alpha', 0.95)
root.configure(bg='#111111')
sw = root.winfo_screenwidth()
sh = root.winfo_screenheight()
w, h = 680, 88
x = (sw - w) // 2
y = sh - 120
root.geometry(f'{w}x{h}+{x}+{y}')
c = tk.Canvas(root, bg='#111111', highlightthickness=0, width=w, height=h)
c.pack()
c.create_rectangle(2, 2, w-2, h-2, outline='#E8711A', width=2)
dot = c.create_oval(24, 30, 40, 46, fill='#ff3b3b', outline='')
c.create_text(w//2+10, 28, text='Listening  \\u2014  release \\u2318 to transcribe', fill='#ffffff', font=('SF Pro Display', 16, 'bold'))
c.create_text(w//2+10, 58, text='Press L for live typing at cursor', fill='#777777', font=('SF Pro Display', 12))
def pulse():
    cur = c.itemcget(dot,'fill')
    c.itemconfig(dot, fill='#ff3b3b' if cur=='#440000' else '#440000')
    root.after(500, pulse)
pulse()
root.mainloop()
"""
        overlay_proc = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception as e:
        print(f"[DICTATE] Overlay error: {e}")

def hide_overlay():
    global overlay_proc
    if overlay_proc:
        try:
            overlay_proc.terminate()
            overlay_proc = None
        except:
            pass

# ── SHOW PROCESSING OVERLAY ───────────────────────────────────────────────────
def show_processing():
    try:
        script = """
import tkinter as tk
import sys
root = tk.Tk()
root.overrideredirect(True)
root.attributes('-topmost', True)
root.attributes('-alpha', 0.93)
root.configure(bg='#0a0a0a')
sw = root.winfo_screenwidth()
sh = root.winfo_screenheight()
w, h = 520, 90
x = (sw - w) // 2
y = sh - 130
root.geometry(f'{w}x{h}+{x}+{y}')
c = tk.Canvas(root, bg='#0a0a0a', highlightthickness=0, width=w, height=h)
c.pack()
c.create_rectangle(1,1,w-1,h-1, outline='#00aaff', width=1)
c.create_text(w//2, h//2, text='\u26a1  Transcribing...', fill='#00aaff', font=('Helvetica', 13))
root.after(20000, root.destroy)
root.mainloop()
"""
        p = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return p
    except:
        return None

# ── LIVE DICTATION (hands-free, double-tap Option) ──────────────────────────
WHISPER_SERVER = "http://localhost:8084/v1/audio/transcriptions"
SOX_PATH = "/opt/homebrew/bin/sox"

def _live_overlay_script():
    return f"""
import tkinter as tk, os, time
TFILE = {repr(live_text_file)}
root = tk.Tk()
root.overrideredirect(True)
root.attributes('-topmost', True)
root.attributes('-alpha', 0.93)
root.configure(bg='#0a0a0a')
sw = root.winfo_screenwidth()
sh = root.winfo_screenheight()
w, h = 620, 110
x = (sw - w) // 2
y = sh - 140
root.geometry(f'{{w}}x{{h}}+{{x}}+{{y}}')
c = tk.Canvas(root, bg='#0a0a0a', highlightthickness=0, width=w, height=h)
c.pack()
c.create_rectangle(1, 1, w-1, h-1, outline='#00ff88', width=1)
dot = c.create_oval(14, 12, 27, 25, fill='#ff3b3b', outline='')
c.create_text(20, 18, text='\\U0001f3a4  Live Typing  \\u2014  press L to stop', anchor='w', fill='#00ff88', font=('Helvetica', 10), tags='hdr')
txt = c.create_text(w//2, 62, text='Listening...', fill='#eeeeee', font=('Menlo', 13), width=w-40, tags='live')
def poll():
    try:
        if os.path.exists(TFILE):
            with open(TFILE) as f: content = f.read().strip()
            if content:
                c.itemconfig('live', text=content[-200:])
    except: pass
    root.after(300, poll)
def pulse():
    cur = c.itemcget(dot, 'fill')
    c.itemconfig(dot, fill='#ff3b3b' if cur == '#550000' else '#550000')
    root.after(400, pulse)
poll()
pulse()
root.mainloop()
"""

def _live_record_loop():
    """Record in 3-second chunks, send to Whisper server, accumulate text."""
    import requests
    # Clear text file
    with open(live_text_file, "w") as f:
        f.write("")
    full_text = ""
    chunk_sec = 3
    while not live_stop_event.is_set():
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        try:
            subprocess.run(
                [SOX_PATH, "-t", "coreaudio", "default", "-r", "16000", "-c", "1",
                 "-b", "16", "-e", "signed-integer", tmp.name, "trim", "0", str(chunk_sec)],
                timeout=chunk_sec + 3, capture_output=True
            )
            if live_stop_event.is_set():
                break
            if not os.path.exists(tmp.name) or os.path.getsize(tmp.name) < 1000:
                continue
            # Check energy — skip silence
            try:
                import wave as _wave, numpy as _np
                wf = _wave.open(tmp.name, 'rb')
                data = _np.frombuffer(wf.readframes(wf.getnframes()), dtype=_np.int16)
                wf.close()
                energy = _np.abs(data).mean()
                if energy < 200:
                    continue
            except:
                pass
            # Send to Whisper server
            with open(tmp.name, "rb") as f:
                r = requests.post(WHISPER_SERVER,
                    files={"file": ("chunk.wav", f, "audio/wav")},
                    data={"model": "mlx-community/whisper-large-v3-turbo", "language": "en"},
                    timeout=10)
            if r.status_code == 200:
                chunk_text = r.json().get("text", "").strip()
                # Filter Whisper hallucinations
                if chunk_text and not is_hallucination(chunk_text):
                    full_text += chunk_text + " "
                    with open(live_text_file, "w") as f:
                        f.write(full_text.strip())
                    # Type chunk live at cursor position
                    pyperclip.copy(chunk_text + " ")
                    time.sleep(0.15)
                    pyautogui.hotkey('command', 'v')
                    print(f"[DICTATE] Live: {chunk_text}")
        except Exception as e:
            print(f"[DICTATE] Live chunk error: {e}")
        finally:
            try: os.unlink(tmp.name)
            except: pass
    return full_text.strip()

def start_live_dictation():
    global live_active, live_overlay, live_thread, live_stop_event
    if live_active:
        return
    live_active = True
    live_stop_event.clear()
    print("[DICTATE] \U0001f3a4 Live dictation started — double-tap Option to stop")
    # Sound
    threading.Thread(target=lambda: subprocess.run(
        ['afplay', '/System/Library/Sounds/Blow.aiff'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL), daemon=True).start()
    # Show overlay
    live_overlay = subprocess.Popen(
        [sys.executable, "-c", _live_overlay_script()],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    # Start recording loop in thread
    live_thread = threading.Thread(target=_live_record_loop, daemon=True)
    live_thread.start()

def stop_live_dictation():
    global live_active, live_overlay, live_thread
    if not live_active:
        return
    live_active = False
    live_stop_event.set()
    print("[DICTATE] \u2705 Live dictation stopped — pasting text")
    # Sound
    threading.Thread(target=lambda: subprocess.run(
        ['afplay', '/System/Library/Sounds/Funk.aiff'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL), daemon=True).start()
    # Kill overlay
    if live_overlay:
        try: live_overlay.terminate()
        except: pass
        live_overlay = None
    # Wait for thread
    if live_thread:
        live_thread.join(timeout=5)
        live_thread = None
    # Text was already typed live at cursor — just log
    text = ""
    try:
        with open(live_text_file) as f:
            text = f.read().strip()
    except: pass
    if text:
        print(f"[DICTATE] \u2705 Done: {text[:80]}")
    else:
        print("[DICTATE] No speech detected in live mode")
    # Cleanup
    try: os.unlink(live_text_file)
    except: pass

# ── AUDIO RECORDING ───────────────────────────────────────────────────────────
def record_audio():
    """Record audio using sox (built into macOS via brew or system)"""
    global audio_frames
    audio_frames = []

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_path = tmp.name
    tmp.close()

    # Use sox to record — comes with macOS or brew install sox
    # Falls back to afrecord (built-in macOS)
    proc = None
    sox_cmd = SOX_PATH if os.path.exists(SOX_PATH) else "sox"
    try:
        proc = subprocess.Popen(
            [sox_cmd, "-t", "coreaudio", "default", "-r", str(SAMPLE_RATE), "-c", "1", "-b", "16", tmp_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except FileNotFoundError:
        try:
            proc = subprocess.Popen(
                ["rec", "-r", str(SAMPLE_RATE), "-c", "1", "-b", "16", tmp_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except FileNotFoundError:
            print("[DICTATE] sox/rec not found. Install: brew install sox")
            return None

    return proc, tmp_path

# ── TRANSCRIBE ────────────────────────────────────────────────────────────────
def transcribe_and_type(audio_path):
    if not model_loaded.is_set():
        print("[DICTATE] Model not loaded yet")
        return

    proc_overlay = show_processing()

    try:
        print(f"[DICTATE] Transcribing {audio_path}...")
        segments, info = model.transcribe(
            audio_path,
            language="en",
            beam_size=5,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=300)
        )

        text = " ".join([s.text.strip() for s in segments]).strip()

        if proc_overlay:
            try:
                proc_overlay.terminate()
            except:
                pass

        if not text or is_hallucination(text):
            print(f"[DICTATE] No speech or hallucination: {text!r}")
            return

        print(f"[DICTATE] Transcribed: {text}")

        # Draft mode: ONLY if dictation starts with "draft" — refine with Qwen
        import re as _re
        _lower = text.lower().strip()
        _draft_match = _re.match(r'^draft[\s.,!;:]+', _lower)
        if _draft_match:
            body = text[_draft_match.end():].strip()
            if body:
                try:
                    import requests as _req
                    print("[DICTATE] Draft mode — refining with Qwen...")
                    r = _req.post("http://localhost:8081/v1/chat/completions",
                        json={"model": "mlx-community/Qwen3.5-35B-A3B-4bit",
                              "messages": [
                                  {"role": "system", "content": "Rewrite the user message as a polished, professional message. Output ONLY the final text. No preamble, no explanation."},
                                  {"role": "user", "content": body}
                              ],
                              "max_tokens": 300, "temperature": 0.3,
                              "chat_template_kwargs": {"enable_thinking": False}},
                        timeout=15)
                    if r.status_code == 200:
                        refined = r.json()["choices"][0]["message"]["content"].strip()
                        if refined:
                            text = refined
                            print(f"[DICTATE] Refined: {text}")
                        else:
                            text = body
                            print("[DICTATE] Qwen returned empty, using raw body")
                    else:
                        text = body
                        print(f"[DICTATE] Qwen HTTP {r.status_code}, using raw body")
                except Exception as qe:
                    text = body
                    print(f"[DICTATE] Qwen error: {qe}, using raw body")

        # Copy to clipboard
        pyperclip.copy(text)

        # Small delay to ensure focus is back on target window
        time.sleep(0.15)

        # Type the text using CMD+V (paste) — faster and more reliable than typing
        pyautogui.hotkey('command', 'v')

        print(f"[DICTATE] \u2705 Typed: {text}")

    except Exception as e:
        print(f"[DICTATE] Transcription error: {e}")
        if proc_overlay:
            try:
                proc_overlay.terminate()
            except:
                pass
    finally:
        try:
            os.unlink(audio_path)
        except:
            pass

# ── KEYBOARD LISTENER ─────────────────────────────────────────────────────────
recording_proc = None
recording_path = None

def on_press(key):
    global cmd_held, recording_proc, recording_path

    # ── L key: toggle live dictation (while CMD held → switch; while active → stop) ──
    if hasattr(key, 'char') and key.char == 'l':
        if live_active:
            threading.Thread(target=stop_live_dictation, daemon=True).start()
            return
        if cmd_held:
            # Stop current recording, switch to live mode
            if recording_proc:
                try:
                    recording_proc.terminate()
                    recording_proc.wait(timeout=2)
                except: pass
                recording_proc = None
                recording_path = None
            hide_overlay()
            cmd_held = False
            threading.Thread(target=start_live_dictation, daemon=True).start()
            return

    # ── Hold RIGHT CMD only → classic dictation ──
    if key == keyboard.Key.cmd_r:
        if not cmd_held and not live_active:
            cmd_held = True
            print("[DICTATE] CMD held — starting recording")
            threading.Thread(target=lambda: subprocess.run(
                ['afplay', '/System/Library/Sounds/Blow.aiff'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL), daemon=True).start()
            show_overlay()
            result = record_audio()
            if result:
                recording_proc, recording_path = result

def on_release(key):
    global cmd_held, recording_proc, recording_path

    if key == keyboard.Key.cmd_r:
        if cmd_held:
            cmd_held = False
            threading.Thread(target=lambda: subprocess.run(
                ['afplay', '/System/Library/Sounds/Funk.aiff'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL), daemon=True).start()
            hide_overlay()

            if recording_proc:
                print("[DICTATE] CMD released — stopping recording")
                recording_proc.terminate()
                recording_proc.wait()
                rp = recording_path
                recording_proc = None
                recording_path = None

                # Transcribe in background thread
                t = threading.Thread(target=transcribe_and_type, args=(rp,), daemon=True)
                t.start()

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    print("""
\u2554\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557
\u2551     CODEC Dictate  v2.1.0                  \u2551
\u2551     Hold-to-Speak + Live Typing                 \u2551
\u2560\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2563
\u2551  Hold \u2318R (right CMD) \u2192 speak \u2192 release       \u2551
\u2551  L  \u2192 Live typing at cursor (while \u2318R or L)  \u2551
\u2551    Words type live wherever cursor is          \u2551
\u2551    Press L again to stop                      \u2551
\u2551  Text types into active window               \u2551
\u2551  Press Ctrl+C to quit                        \u2551
\u255a\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u255d
""")

    # Check for sox
    if not os.path.exists(SOX_PATH) and subprocess.run(["which", "sox"], capture_output=True).returncode != 0:
        print("[DICTATE] \u26a0\ufe0f  sox not found \u2014 install with: brew install sox")
        print("[DICTATE] sox is required for microphone recording")
        sys.exit(1)

    # Load whisper in background
    t = threading.Thread(target=load_model, daemon=True)
    t.start()

    print("[DICTATE] Waiting for Whisper to load...")
    model_loaded.wait()
    print("[DICTATE] \U0001f7e2 Ready. Hold right CMD to record. F5 for live dictation.")

    # Cleanup on exit — kill sox, overlays, temp files
    import atexit, glob as _glob
    def _cleanup():
        global recording_proc
        if recording_proc:
            try: recording_proc.terminate(); recording_proc.wait(timeout=2)
            except: pass
            recording_proc = None
        hide_overlay()
        if live_active:
            stop_live_dictation()
        for f in _glob.glob(os.path.join(tempfile.gettempdir(), "dictate_*.wav")):
            try: os.unlink(f)
            except: pass
    atexit.register(_cleanup)
    import signal
    signal.signal(signal.SIGTERM, lambda *a: (print("[DICTATE] SIGTERM received"), _cleanup(), sys.exit(0)))

    # Start keyboard listener
    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        try:
            listener.join()
        except KeyboardInterrupt:
            print("\n[DICTATE] Shutting down.")
            _cleanup()

if __name__ == "__main__":
    main()
