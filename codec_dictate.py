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

# Shared overlay renderer (Swift HUD + tkinter fallback) — unifies dictate's
# Listening / Transcribing / LIVE pills with the rest of CODEC.
import codec_overlays

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
        # Shared Swift HUD: orange mark + "Listening / release ⌘ to send".
        overlay_proc = codec_overlays.show_recording_overlay("⌘")
    except Exception as e:
        print(f"[DICTATE] Overlay error: {e}")

def hide_overlay():
    global overlay_proc
    if overlay_proc:
        try:
            overlay_proc.terminate()
        except Exception:
            pass
        overlay_proc = None
    codec_overlays.show_recording_stop()

# ── SHOW PROCESSING OVERLAY ───────────────────────────────────────────────────
def show_processing():
    try:
        # Shared Swift HUD: blue "Transcribing\u2026" (auto-hides; or hide_overlay()).
        return codec_overlays.show_processing_overlay("Transcribing...", duration=20000)
    except Exception:
        return None

# ── LIVE DICTATION (hands-free, double-tap Option) ──────────────────────────
# B2 / SR-18: read STT + LLM URLs from codec_config so operators who change
# the port get a consistent experience across dashboard, voice, and dictate.
try:
    from codec_config import WHISPER_URL as WHISPER_SERVER
    from codec_config import QWEN_BASE_URL as _QWEN_BASE_URL
    from codec_config import QWEN_MODEL as _QWEN_MODEL
except ImportError:
    WHISPER_SERVER = "http://localhost:8084/v1/audio/transcriptions"
    _QWEN_BASE_URL = "http://localhost:8083/v1"
    _QWEN_MODEL = "mlx-community/Qwen3.6-35B-A3B-4bit"
SOX_PATH = "/opt/homebrew/bin/sox"


def _live_overlay_script():
    """Visible tkinter pill: 'LIVE · press F5 to stop' top-center.
    Focus is no longer a problem because live mode is now triggered by F5
    (not ⌘+L, which Chrome intercepts as 'focus URL bar')."""
    return """
import tkinter as tk
root = tk.Tk()
root.overrideredirect(True)
root.attributes('-topmost', True)
root.attributes('-alpha', 0.95)
root.configure(bg='#0a0a0a')
sw = root.winfo_screenwidth()
w, h = 260, 40
x = (sw - w) // 2
y = 14
root.geometry(f'{w}x{h}+{x}+{y}')
c = tk.Canvas(root, bg='#0a0a0a', highlightthickness=0, width=w, height=h)
c.pack()
c.create_rectangle(1, 1, w-1, h-1, outline='#ff3b3b', width=2, fill='#0a0a0a')
dot = c.create_oval(14, 13, 28, 27, fill='#ff3b3b', outline='')
c.create_text(w//2 + 10, h//2, text='LIVE  \u00b7  press F5 to stop',
              fill='#ff3b3b', font=('SF Pro Display', 13, 'bold'))
def pulse():
    cur = c.itemcget(dot, 'fill')
    c.itemconfig(dot, fill='#ff3b3b' if cur == '#3a0000' else '#3a0000')
    root.after(500, pulse)
pulse()
root.mainloop()
"""

def _live_record_loop():
    """Pipelined recording + transcription so no audio is dropped between chunks.

    Producer thread: continuously records 2s sox chunks back-to-back into a queue.
    Consumer (this thread): pulls chunks, sends to Whisper, pastes at cursor.

    Gemini-style: each chunk is pasted at the current cursor position. No reflow.
    """
    import requests
    import queue
    chunk_sec = 2
    q = queue.Queue(maxsize=8)

    def _producer():
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
                    try: os.unlink(tmp.name)
                    except OSError: pass
                    break
                if os.path.exists(tmp.name) and os.path.getsize(tmp.name) >= 1000:
                    try:
                        q.put(tmp.name, timeout=1)
                    except queue.Full:
                        try: os.unlink(tmp.name)
                        except OSError: pass
                else:
                    try: os.unlink(tmp.name)
                    except OSError: pass
            except Exception as e:
                print(f"[DICTATE] Producer error: {e}")
                try: os.unlink(tmp.name)
                except OSError: pass

    prod = threading.Thread(target=_producer, daemon=True)
    prod.start()

    full_text = ""
    while not live_stop_event.is_set() or not q.empty():
        try:
            path = q.get(timeout=0.5)
        except Exception:
            continue
        try:
            # Energy check
            try:
                import wave as _wave
                import numpy as _np
                wf = _wave.open(path, 'rb')
                data = _np.frombuffer(wf.readframes(wf.getnframes()), dtype=_np.int16)
                wf.close()
                if _np.abs(data).mean() < 150:
                    continue
            except Exception:
                pass
            with open(path, "rb") as f:
                r = requests.post(WHISPER_SERVER,
                    files={"file": ("chunk.wav", f, "audio/wav")},
                    data={"model": "mlx-community/whisper-large-v3-turbo",
                          "language": "en", "task": "transcribe"},
                    timeout=10)
            if r.status_code == 200:
                chunk_text = r.json().get("text", "").strip()
                if chunk_text and not is_hallucination(chunk_text):
                    full_text += chunk_text + " "
                    paste_text = chunk_text + " "
                    pyperclip.copy(paste_text)
                    time.sleep(0.05)
                    # Use pyautogui (CGEventPost) instead of osascript — does NOT
                    # activate System Events / shift focus. Paste lands in the
                    # field the user has focused, not the URL bar.
                    pyautogui.hotkey('command', 'v')
                    print(f"[DICTATE] Live: '{chunk_text}'")
        except Exception as e:
            print(f"[DICTATE] Live chunk error: {e}")
        finally:
            try: os.unlink(path)
            except OSError: pass

    prod.join(timeout=3)
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
    # Show the LIVE indicator via the shared Swift HUD (tkinter fallback if down)
    live_overlay = codec_overlays.show_live_overlay()
    # Start recording loop in thread
    live_thread = threading.Thread(target=_live_record_loop, daemon=True)
    live_thread.start()

def stop_live_dictation():
    global live_active, live_overlay, live_thread
    if not live_active:
        return
    live_active = False
    live_stop_event.set()
    print("[DICTATE] \u2705 Live dictation stopped")
    # Sound
    threading.Thread(target=lambda: subprocess.run(
        ['afplay', '/System/Library/Sounds/Funk.aiff'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL), daemon=True).start()
    # Hide the LIVE indicator (Swift) + terminate any tkinter fallback process
    codec_overlays.show_recording_stop()
    if live_overlay:
        try: live_overlay.terminate()
        except OSError: pass  # ProcessLookupError covered (subclass of OSError)
        try: live_overlay.wait(timeout=0.5)
        except Exception:
            try: live_overlay.kill()
            except OSError: pass  # ProcessLookupError covered (subclass of OSError)
        live_overlay = None
    # Wait for thread
    if live_thread:
        live_thread.join(timeout=5)
        live_thread = None
    # Text was already typed live at cursor — nothing to paste
    print("[DICTATE] \u2705 Live dictation complete")

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
        # PIN language + task. Without these, faster-whisper auto-detects the
        # language first, and accented English (e.g. a French speaker) is
        # frequently misdetected as French — Whisper then transcribes the
        # English audio AS French text ("I speak English, it pastes French",
        # 2026-07-08). task="transcribe" (never "translate") keeps it in the
        # spoken language. This matches the already-fixed HTTP path (~line 215);
        # the classic local path had been missed. `en` is intentional — Dictate
        # is used for English here; change DICTATE_LANGUAGE below if that shifts.
        _DICTATE_LANGUAGE = "en"
        segments, info = model.transcribe(
            audio_path,
            language=_DICTATE_LANGUAGE,
            task="transcribe",
            beam_size=5,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=300)
        )
        try:
            print(f"[DICTATE] whisper lang={getattr(info, 'language', '?')} "
                  f"prob={getattr(info, 'language_probability', 0):.2f} (pinned={_DICTATE_LANGUAGE})")
        except Exception:
            pass

        text = " ".join([s.text.strip() for s in segments]).strip()

        if proc_overlay:
            try:
                proc_overlay.terminate()
            except Exception:
                pass
        codec_overlays.hide_overlay()  # hide the Swift "Transcribing…" HUD

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
                    # A-12 (PR-3E-2): canonical codec_llm.call. Never raises ->
                    # "" on any failure (non-200, conn error, empty), which maps
                    # exactly onto the old "use raw body" fallback for every
                    # non-success branch.
                    import codec_llm
                    print("[DICTATE] Draft mode — refining with Qwen...")
                    refined = codec_llm.call(
                        [
                            {"role": "system", "content": "Rewrite the user message as a polished, professional message. Output ONLY the final text. No preamble, no explanation."},
                            {"role": "user", "content": body},
                        ],
                        base_url=_QWEN_BASE_URL,
                        model=_QWEN_MODEL,
                        max_tokens=300, temperature=0.3, timeout=15,
                    )
                    if refined:
                        text = refined
                        print(f"[DICTATE] Refined: {text}")
                    else:
                        text = body
                        print("[DICTATE] Qwen unavailable or empty, using raw body")
                except Exception as qe:
                    text = body
                    print(f"[DICTATE] Qwen error: {qe}, using raw body")

        # Copy to clipboard
        pyperclip.copy(text)

        # Small delay to ensure focus is back on target window
        time.sleep(0.15)

        # Paste via osascript — more reliable than pyautogui on macOS
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to keystroke "v" using command down'],
            capture_output=True, timeout=5)

        print(f"[DICTATE] \u2705 Typed: {text}")

    except Exception as e:
        print(f"[DICTATE] Transcription error: {e}")
        if proc_overlay:
            try:
                proc_overlay.terminate()
            except Exception:
                pass
        codec_overlays.hide_overlay()  # hide the Swift "Transcribing…" HUD
    finally:
        try:
            os.unlink(audio_path)
        except Exception:
            pass

# ── KEYBOARD LISTENER ─────────────────────────────────────────────────────────
recording_proc = None
recording_path = None

def on_press(key):
    global cmd_held, recording_proc, recording_path

    # ── F5: toggle live dictation. (Was ⌘+L, but Chrome intercepts ⌘+L to
    #   focus the URL bar — so typing landed there instead of the chat.) ──
    if key == keyboard.Key.f5:
        if live_active:
            threading.Thread(target=stop_live_dictation, daemon=True).start()
            return
        # F5 works standalone OR while CMD held
        if cmd_held:
            # Stop current recording, switch to live mode
            if recording_proc:
                try:
                    recording_proc.terminate()
                    recording_proc.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired): pass
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
\u2551  F5 \u2192 Hands-free live typing at cursor       \u2551
\u2551    Words type live wherever cursor is          \u2551
\u2551    Press F5 again to stop                     \u2551
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
    print("[DICTATE] \U0001f7e2 Ready. Hold right CMD to record. F5 to toggle live dictation.")

    # Cleanup on exit — kill sox, overlays, temp files
    import atexit
    import glob as _glob
    def _cleanup():
        global recording_proc
        if recording_proc:
            try: recording_proc.terminate(); recording_proc.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired): pass
            recording_proc = None
        hide_overlay()
        if live_active:
            stop_live_dictation()
        for f in _glob.glob(os.path.join(tempfile.gettempdir(), "dictate_*.wav")):
            try: os.unlink(f)
            except OSError: pass
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
