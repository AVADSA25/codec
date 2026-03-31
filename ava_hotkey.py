#!/usr/bin/env python3
"""
AVA Hotkey Daemon - SuperWhisper Replacement
Hold CMD → floating window appears → speak → release CMD → text typed + copied

Requirements: pynput, pyautogui, faster-whisper, Pillow, requests
Install: pip install pynput pyautogui faster-whisper Pillow requests --break-system-packages
"""

import threading
import tempfile
import subprocess
import sys
import os
import time
import wave
import struct
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

# ── LOAD WHISPER ─────────────────────────────────────────────────────────────
def load_model():
    global model
    print("[AVA] Loading Whisper model...")
    model = WhisperModel(WHISPER_MODEL_SIZE, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE)
    model_loaded.set()
    print("[AVA] Whisper ready.")

# ── OVERLAY (tiny floating window via osascript) ──────────────────────────────
OVERLAY_SCRIPT = """
tell application "System Events"
    -- nothing
end tell

set overlayText to "🎙 Listening... release ⌘ to transcribe"

do shell script "python3 -c \\"
import tkinter as tk
import sys

root = tk.Tk()
root.overrideredirect(True)
root.attributes('-topmost', True)
root.attributes('-alpha', 0.92)
root.configure(bg='#0a0a0a')

sw = root.winfo_screenwidth()
sh = root.winfo_screenheight()
w, h = 440, 84
x = (sw - w) // 2
y = sh - 120
root.geometry(f'{w}x{h}+{x}+{y}')

frame = tk.Frame(root, bg='#0a0a0a', bd=0)
frame.pack(fill='both', expand=True)

canvas = tk.Canvas(frame, bg='#0a0a0a', highlightthickness=0, width=w, height=h)
canvas.pack()
canvas.create_rectangle(2, 2, w-2, h-2, outline='#00ff88', width=1)

dot = canvas.create_oval(16, 18, 28, 30, fill='#ff3b3b', outline='')
label = canvas.create_text(w//2 + 8, h//2, text='🎙  Listening  —  release ⌘ to transcribe', fill='#ffffff', font=('SF Pro Display', 13))

def pulse():
    current = canvas.itemcget(dot, 'fill')
    canvas.itemconfig(dot, fill='#ff3b3b' if current == '#ff0000' else '#ff0000')
    root.after(500, pulse)

pulse()
root.mainloop()
\\""
"""

def show_overlay():
    global overlay_proc
    try:
        script = """
import tkinter as tk
root = tk.Tk()
root.overrideredirect(True)
root.attributes('-topmost', True)
root.attributes('-alpha', 0.93)
root.configure(bg='#0a0a0a')
sw = root.winfo_screenwidth()
sh = root.winfo_screenheight()
w, h = 440, 84
x = (sw - w) // 2
y = sh - 130
root.geometry(f'{w}x{h}+{x}+{y}')
c = tk.Canvas(root, bg='#0a0a0a', highlightthickness=0, width=w, height=h)
c.pack()
c.create_rectangle(1,1,w-1,h-1, outline='#00ff88', width=1)
dot = c.create_oval(14,17,27,30, fill='#ff3b3b', outline='')
c.create_text(w//2+10, h//2, text='🎙  Listening  —  release ⌘ to transcribe', fill='#eeeeee', font=('Helvetica', 13))
def pulse():
    cur = c.itemcget(dot,'fill')
    c.itemconfig(dot, fill='#ff3b3b' if cur=='#550000' else '#550000')
    root.after(400, pulse)
pulse()
root.mainloop()
"""
        overlay_proc = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception as e:
        print(f"[AVA] Overlay error: {e}")

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
w, h = 440, 84
x = (sw - w) // 2
y = sh - 130
root.geometry(f'{w}x{h}+{x}+{y}')
c = tk.Canvas(root, bg='#0a0a0a', highlightthickness=0, width=w, height=h)
c.pack()
c.create_rectangle(1,1,w-1,h-1, outline='#00aaff', width=1)
c.create_text(w//2, h//2, text='⚡  Transcribing...', fill='#00aaff', font=('Helvetica', 13))
root.after(4000, root.destroy)
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
    try:
        proc = subprocess.Popen(
            ["sox", "-t", "coreaudio", "default", "-r", "16000", "-c", "1", "-b", "16", tmp_path],
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
            print("[AVA] sox/rec not found. Install: brew install sox")
            return None
    
    return proc, tmp_path

# ── TRANSCRIBE ────────────────────────────────────────────────────────────────
def transcribe_and_type(audio_path):
    if not model_loaded.is_set():
        print("[AVA] Model not loaded yet")
        return
    
    proc_overlay = show_processing()
    
    try:
        print(f"[AVA] Transcribing {audio_path}...")
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
        
        if not text:
            print("[AVA] No speech detected")
            return
        
        print(f"[AVA] Transcribed: {text}")
        
        # Draft detection: if draft/reply intent, refine with Qwen first
        import requests as _req
        DRAFT_KW = ["draft","reply","rephrase","rewrite","fix my","write a","write an",
                    "compose","tell them","say that","respond","correct my","please write",
                    "post saying","comment saying","tweet saying","please say","and say"]
        is_draft = any(k in text.lower() for k in DRAFT_KW)
        
        if is_draft:
            try:
                print("[AVA] Draft detected, calling Qwen...")
                DRAFT_SYS = "You are CODEC, elite writing assistant. The user has dyslexia. OUTPUT ONLY the final message text. No preamble. No explanation. Fix grammar. Keep the user's personality."
                r = _req.post("http://localhost:8081/v1/chat/completions",
                    json={"model":"mlx-community/Qwen3.5-35B-A3B-4bit",
                          "messages":[{"role":"system","content":DRAFT_SYS},
                                      {"role":"user","content":"User instruction: " + text + "\nWrite the final message text now:"}],
                          "max_tokens":300,"temperature":0.6},
                    timeout=60)
                if r.status_code == 200:
                    refined = r.json()["choices"][0]["message"]["content"].strip()
                    if refined:
                        text = refined
                        print(f"[AVA] Refined: {text}")
            except Exception as qe:
                print(f"[AVA] Qwen unavailable, pasting raw: {qe}")
        
        # Copy to clipboard
        pyperclip.copy(text)
        
        # Small delay to ensure focus is back on target window
        time.sleep(0.15)
        
        # Type the text using CMD+V (paste) — faster and more reliable than typing
        pyautogui.hotkey('command', 'v')
        
        print(f"[AVA] ✅ Typed: {text}")
        
    except Exception as e:
        print(f"[AVA] Transcription error: {e}")
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
    
    if key == keyboard.Key.cmd_r:
        if not cmd_held:
            cmd_held = True
            print("[AVA] CMD held — starting recording")
            show_overlay()
            result = record_audio()
            if result:
                recording_proc, recording_path = result

def on_release(key):
    global cmd_held, recording_proc, recording_path
    
    if key == keyboard.Key.cmd_r:
        if cmd_held:
            cmd_held = False
            hide_overlay()
            
            if recording_proc:
                print("[AVA] CMD released — stopping recording")
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
╔══════════════════════════════════════════╗
║     AVA Hotkey Daemon  v1.0              ║
║     SuperWhisper Replacement             ║
╠══════════════════════════════════════════╣
║  Hold ⌘ CMD  →  speak  →  release       ║
║  Text types into active window           ║
║  Press Ctrl+C to quit                    ║
╚══════════════════════════════════════════╝
""")
    
    # Check for sox
    if subprocess.run(["which", "sox"], capture_output=True).returncode != 0:
        print("[AVA] ⚠️  sox not found — install with: brew install sox")
        print("[AVA] sox is required for microphone recording")
        sys.exit(1)
    
    # Load whisper in background
    t = threading.Thread(target=load_model, daemon=True)
    t.start()
    
    print("[AVA] Waiting for Whisper to load...")
    model_loaded.wait()
    print("[AVA] 🟢 Ready. Hold CMD to record.")
    
    # Start keyboard listener
    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        try:
            listener.join()
        except KeyboardInterrupt:
            print("\n[AVA] Shutting down.")

if __name__ == "__main__":
    main()
