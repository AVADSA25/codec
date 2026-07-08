#!/usr/bin/env python3.13
"""CODEC Text Assistant — mode passed as argument, no popup"""
import sys
import os
import json
import requests
import subprocess
import re
import time

MODE = sys.argv[1] if len(sys.argv) > 1 else "proofread"

def get_config():
    try:
        with open(os.path.expanduser("~/.codec/config.json")) as f: return json.load(f)
    except Exception: return {}

def call_qwen(text, mode):
    cfg = get_config()
    base = cfg.get("llm_base_url", "http://localhost:8083/v1")
    model = cfg.get("llm_model", "mlx-community/Qwen3.6-35B-A3B-4bit")
    kwargs = cfg.get("llm_kwargs", {})
    prompts = {
        "proofread": "Fix all spelling, grammar, and punctuation errors. Keep same tone. Output ONLY corrected text.",
        "elevate": "Rewrite to be more polished and professional. Keep same meaning. Output ONLY improved text.",
        "explain": "Explain this text simply and concisely. What is it about? Key points?",
        "read_aloud": "READ_ALOUD_MODE",
        "save": "SAVE_TO_KEEP_MODE",
        "reply": "You are a smart, natural communicator. The user will give you a message they received, possibly followed by a colon : and their reply direction. If there is a colon with instructions after it, follow those instructions to craft the reply. If there is no colon, write a natural reply matching the tone. Keep it short (1-3 sentences). Output ONLY the reply text. No quotes, no labels, no explanation.",
        "translate": "You are a translator. Translate the following text into English. No matter what language the input is — Ukrainian, Spanish, French, Russian, Chinese, Arabic, anything — always translate to English. Output ONLY the translated English text, nothing else.",
        "prompt": "You are a prompt engineer. Rewrite the following text to be a clear, optimized prompt for an AI language model. Make it specific, structured, and effective. Remove ambiguity, add context where helpful, and ensure the intent is crystal clear. Output ONLY the optimized prompt, nothing else."
    }
    # A-12 (PR-3E-2c): canonical codec_llm.call(raise_on_error=True). Fail-loud
    # is required here — the caller's except shows an Error overlay; never-raise
    # would paste an empty result over the user's selection. codec_llm strips
    # <think>; the `### FINAL ANSWER:` marker is textassist-specific so it stays.
    import codec_llm
    result = codec_llm.call(
        [
            {"role": "system", "content": prompts.get(mode, prompts["proofread"])},
            {"role": "user", "content": text},
        ],
        base_url=base, model=model, max_tokens=4000, temperature=0.3,
        extra_kwargs=kwargs, timeout=60, raise_on_error=True,
    )
    return re.sub(r'###\s*FINAL ANSWER:\s*', '', result).strip()

import codec_overlays


def overlay(text, color, duration):
    """CODEC Instant status overlay — routed through the shared
    codec_overlays module (the same Swift glass-blurred NSPanel that
    F13/F18 use), instead of the bare hand-rolled tkinter box this used
    to spawn (flat black rectangle, no blur, no CODEC branding — visibly
    inconsistent with the rest of the product).

    Returns None: the Swift render path has no killable process handle.
    Call codec_overlays.hide_overlay() to dismiss early (replaces the
    old `_proc_overlay.terminate()` pattern below).
    """
    return codec_overlays.show_overlay(text, color=color, duration=duration)

text = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout.strip()
if not text: sys.exit(0)

# ── Read Aloud: speak via Kokoro TTS, no LLM needed ──────────────────────────
if MODE == "read_aloud":
    tts_text = text[:2000]
    cfg = get_config()
    tts_url   = cfg.get("tts_url",   "http://localhost:8085/v1/audio/speech")
    tts_model = cfg.get("tts_model", "mlx-community/Kokoro-82M-bf16")
    tts_voice = cfg.get("tts_voice", "am_adam")
    overlay("\U0001f50a Reading aloud...", "#E8711A", 6000)
    try:
        import tempfile
        r = requests.post(tts_url, json={
            "model": tts_model, "input": tts_text, "voice": tts_voice
        }, timeout=30)
        if r.status_code == 200:
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                f.write(r.content)
                mp3_path = f.name
            subprocess.run(["afplay", mp3_path])
            os.unlink(mp3_path)
        else:
            overlay("\u26a0 TTS unavailable", "#ff3333", 3000)
    except Exception as e:
        overlay("\u26a0 TTS error", "#ff3333", 3000)
        print(f"TTS error: {e}")
    sys.exit(0)

# ── Save: save to Apple Notes (primary) + local file backup ─────────────────
if MODE == "save":
    save_text = text[:2000]
    safe = save_text.replace('"', '\\"').replace("'", "")
    # Save to Apple Notes
    try:
        subprocess.run(["osascript", "-e",
            f'tell application "Notes" to make new note at folder "Notes" with properties {{body:"{safe}"}}'],
            capture_output=True, text=True, timeout=10)
    except Exception:
        pass
    # Also save local backup
    notes_path = os.path.expanduser("~/.codec/saved_notes.txt")
    from datetime import datetime
    with open(notes_path, "a") as nf:
        nf.write(f"\n--- {datetime.now().strftime('%Y-%m-%d %H:%M')} ---\n")
        nf.write(save_text + "\n")
    subprocess.run(["osascript", "-e",
        'display notification "Saved to Apple Notes" with title "CODEC Save"'],
        capture_output=True)
    overlay("\u2705 Saved to Apple Notes!", "#44cc66", 2000)
    sys.exit(0)

overlay("⚡ Processing...", "#00aaff", 15000)
try:
    result = call_qwen(text, MODE)
    # Dismiss the processing overlay now that we have the result
    codec_overlays.hide_overlay()
    if MODE in ("explain", "translate"):
        # Show result in a styled floating window (no Terminal)
        title = "CODEC Explain" if MODE == "explain" else "CODEC Translate"
        # Also copy to clipboard so user can paste if needed
        subprocess.run(["pbcopy"], input=result.encode(), check=True)
        # Speak the result via Kokoro TTS — spawn as subprocess so it survives parent exit
        cfg = get_config()
        _tts_env = {**os.environ,
            "_TTS_URL": cfg.get("tts_url", "http://localhost:8085/v1/audio/speech"),
            "_TTS_MODEL": cfg.get("tts_model", "mlx-community/Kokoro-82M-bf16"),
            "_TTS_VOICE": cfg.get("tts_voice", "am_adam"),
            "_TTS_TEXT": result[:1500]}
        subprocess.Popen([sys.executable, "-c", """
import requests, tempfile, subprocess, os
try:
    r = requests.post(os.environ['_TTS_URL'], json={
        "model": os.environ['_TTS_MODEL'],
        "input": os.environ['_TTS_TEXT'],
        "voice": os.environ['_TTS_VOICE']
    }, timeout=30)
    if r.status_code == 200:
        f = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        f.write(r.content); f.close()
        subprocess.run(["afplay", f.name])
        os.unlink(f.name)
except Exception:
    pass
"""], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=_tts_env)
        # Launch a floating result window matching CODEC's actual design
        # tokens (dashboard CSS custom properties, 2026-07-07 restyle):
        #   --surface #1a1a1d --surface-2 #212125 --border #303035
        #   --accent #E8711A --text #ececec --text-muted #9a9aa0
        # Was: a solid loud orange header block, plain Helvetica, no
        # border, flat #111 footer — nothing else in CODEC uses a filled
        # accent block like that; the rest of the product treats orange
        # as a sparing accent (text/underline), not a fill color.
        #
        # Three tkinter gotchas fixed after visual verification (screenshot
        # + direct winfo_* geometry introspection — pixel colors alone were
        # not enough to catch these):
        # - tk.Button ignores custom bg on Aqua (renders as a blank white
        #   box regardless of the bg= argument) — Copy/Close are styled
        #   tk.Label widgets with a click binding instead, which DOES
        #   respect custom colors reliably on macOS.
        # - The native window chrome already shows the title ("CODEC
        #   Translate") in its own title bar; repeating it in the custom
        #   header row produced a redundant double-title look. Dropped
        #   the header's title text, kept just the accent dot + Copy.
        # - tk.Text defaults to a NATURAL size of 80×24 character cells
        #   (computed from the font's metrics), independent of the pack()
        #   fill/expand constraints on its parent. At 13pt that 24-row
        #   request exceeds the whole window's height, and pack() silently
        #   squeezed the footer down to 0px to satisfy it — even though
        #   the footer frame itself was correctly built, mapped, and
        #   should have expanded to fill available space. Explicit
        #   height=1 makes the widget's natural request tiny; fill='both'
        #   + expand=True on its wrapper still lets it grow to fill the
        #   actual available space at runtime. Also: 'SF Mono' silently
        #   fails to resolve on this Tk install (falls back to a
        #   proportional font, confirmed via tkinter.font.families()) —
        #   switched to 'Menlo', which the prior version of this window
        #   used successfully and is confirmed present.
        safe_result = result.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"').replace("\n", "\\n")
        subprocess.Popen([sys.executable, "-c", f"""import tkinter as tk
r=tk.Tk()
r.title('{title}')
r.attributes('-topmost', True)
BORDER='#303035'
r.configure(bg=BORDER)
sw=r.winfo_screenwidth();sh=r.winfo_screenheight()
w,h=560,400
r.geometry(f'{{w}}x{{h}}+{{(sw-w)//2}}+{{(sh-h)//2}}')
r.minsize(400,250)
def _mk_pill(parent, text, cmd, fg='#ececec', bg='#2a2a2e', active='#333338'):
    lbl=tk.Label(parent,text=text,fg=fg,bg=bg,font=('Helvetica Neue',10),padx=10,pady=3,
        borderwidth=1,relief='solid',highlightbackground=BORDER)
    lbl.bind('<Button-1>', lambda e: cmd())
    lbl.bind('<Enter>', lambda e: lbl.config(bg=active))
    lbl.bind('<Leave>', lambda e: lbl.config(bg=bg))
    return lbl
body=tk.Frame(r,bg='#1a1a1d')
body.pack(fill='both',expand=True,padx=1,pady=1)
hdr=tk.Frame(body,bg='#212125',height=40);hdr.pack(fill='x')
hdr.pack_propagate(False)
hdr_inner=tk.Frame(hdr,bg='#212125');hdr_inner.pack(fill='both',expand=True,padx=14)
tk.Label(hdr_inner,text='⬤',fg='#E8711A',bg='#212125',font=('Helvetica Neue',7)).pack(side='left',pady=(12,0))
_mk_pill(hdr_inner,'Copy',lambda:[r.clipboard_clear(),r.clipboard_append(txt.get('1.0','end-1c'))]).pack(side='right',pady=(8,0))
underline=tk.Frame(body,bg='#E8711A',height=2);underline.pack(fill='x')
txt_wrap=tk.Frame(body,bg='#1a1a1d');txt_wrap.pack(fill='both',expand=True)
txt=tk.Text(txt_wrap,height=1,wrap='word',bg='#1a1a1d',fg='#ececec',font=('Menlo',13),relief='flat',
    padx=16,pady=14,insertbackground='#E8711A',selectbackground='#E8711A',selectforeground='#ececec',
    borderwidth=0,highlightthickness=0)
txt.pack(fill='both',expand=True)
txt.insert('1.0','{safe_result}')
txt.config(state='normal')
sep=tk.Frame(body,bg=BORDER,height=1);sep.pack(fill='x')
ft=tk.Frame(body,bg='#212125',height=36);ft.pack(fill='x')
ft.pack_propagate(False)
tk.Label(ft,text='Copied to clipboard  ·  ⌘V to paste',fg='#9a9aa0',bg='#212125',font=('Helvetica Neue',10)).pack(side='left',padx=14)
_mk_pill(ft,'Close',r.destroy,fg='#9a9aa0').pack(side='right',padx=10,pady=6)
r.bind('<Escape>',lambda e:r.destroy())
r.mainloop()
"""], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        overlay("\u2705 {title}", "#44cc66", 2000)
    else:
        # Proofread / Elevate / Reply / Prompt: replace the selection in place.
        subprocess.run(["pbcopy"], input=result.encode(), check=True)
        time.sleep(0.3)
        # 2026-07-08: paste via pyautogui keyDown/keyUp (CGEventPost, a
        # hardware-level event) instead of `osascript System Events keystroke
        # "v" using command down`. The osascript path routes through the
        # Accessibility API to the frontmost app and is unreliable in
        # FULL-SCREEN apps — it drops the modifier or misses focus, leaving the
        # result stuck on the clipboard un-pasted (user report). CGEventPost
        # posts to the HID stream and lands regardless of full-screen state.
        # Same fix already applied to codec_watcher.py's draft paste.
        # try/finally so an exception can't leave Cmd stuck held.
        try:
            import pyautogui
            pyautogui.keyDown('command')
            try:
                time.sleep(0.05)
                pyautogui.press('v')
                time.sleep(0.05)
            finally:
                pyautogui.keyUp('command')
        except Exception:
            # Last-resort fallback to the old path if pyautogui is unavailable.
            subprocess.run(["osascript", "-e",
                'tell application "System Events" to keystroke "v" using command down'],
                capture_output=True, timeout=5)
        overlay("✅ Text replaced!", "#44cc66", 2000)
except Exception:
    codec_overlays.hide_overlay()
    overlay("Error - check terminal", "#ff3333", 3000)
