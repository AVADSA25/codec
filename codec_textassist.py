#!/usr/bin/env python3.13
"""CODEC Text Assistant — mode passed as argument, no popup"""
import sys, os, json, requests, subprocess, re, time

MODE = sys.argv[1] if len(sys.argv) > 1 else "proofread"

def get_config():
    try:
        with open(os.path.expanduser("~/.codec/config.json")) as f: return json.load(f)
    except: return {}

def call_qwen(text, mode):
    cfg = get_config()
    base = cfg.get("llm_base_url", "http://localhost:8081/v1")
    model = cfg.get("llm_model", "mlx-community/Qwen3.5-35B-A3B-4bit")
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
    payload = {"model": model, "messages": [
        {"role": "system", "content": prompts.get(mode, prompts["proofread"])},
        {"role": "user", "content": text}
    ], "max_tokens": 4000, "temperature": 0.3, "stream": False}
    payload.update(kwargs)
    r = requests.post(f"{base}/chat/completions", json=payload, timeout=60)
    result = r.json()["choices"][0]["message"]["content"].strip()
    result = re.sub(r'<think>[\s\S]*?</think>', '', result).strip()
    return re.sub(r'###\s*FINAL ANSWER:\s*', '', result).strip()

def overlay(text, color, duration):
    import json as _json
    env = os.environ.copy()
    env["_OVERLAY_TEXT"] = text
    env["_OVERLAY_COLOR"] = color
    env["_OVERLAY_DURATION"] = str(duration)
    subprocess.Popen([sys.executable, "-c", """import tkinter as tk, os
t=os.environ['_OVERLAY_TEXT'];c=os.environ['_OVERLAY_COLOR'];d=int(os.environ['_OVERLAY_DURATION'])
r=tk.Tk();r.overrideredirect(True);r.attributes('-topmost',True);r.attributes('-alpha',0.95);r.configure(bg='#0a0a0a')
sw=r.winfo_screenwidth();sh=r.winfo_screenheight()
r.geometry(f'440x84+{(sw-440)//2}+{sh-130}')
cv=tk.Canvas(r,bg='#0a0a0a',highlightthickness=0,width=440,height=84);cv.pack()
cv.create_rectangle(1,1,439,83,outline=c,width=1)
cv.create_text(220,42,text=t,fill=c,font=('Helvetica',16,'bold'))
r.after(d,r.destroy);r.mainloop()"""], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)

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

# ── Save: save to Google Keep or local fallback, no LLM needed ───────────────
if MODE == "save":
    save_text = text[:2000]
    saved = False
    # Try Google Keep skill
    try:
        import importlib.util
        keep_path = os.path.expanduser("~/.codec/skills/google_keep.py")
        spec = importlib.util.spec_from_file_location("google_keep", keep_path)
        keep_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(keep_mod)
        result = keep_mod.run(f"save note: {save_text[:500]}")
        if result and any(kw in str(result).lower() for kw in
                          ("saved", "added", "created", "done", "success", "note saved")):
            saved = True
    except Exception:
        pass
    # Fallback: local file
    if not saved:
        notes_path = os.path.expanduser("~/.codec/saved_notes.txt")
        # Ensure Desktop shortcut exists
        desktop_link = os.path.expanduser("~/Desktop/CODEC_Notes.txt")
        if not os.path.exists(desktop_link):
            try: os.symlink(notes_path, desktop_link)
            except: pass
        from datetime import datetime
        with open(notes_path, "a") as nf:
            nf.write(f"\n--- {datetime.now().strftime('%Y-%m-%d %H:%M')} ---\n")
            nf.write(save_text + "\n")
        saved = True
    if saved:
        subprocess.run(["osascript", "-e",
            'display notification "Text saved to notes" with title "CODEC Save"'],
            capture_output=True)
        overlay("\u2705 Saved!", "#44cc66", 2000)
    sys.exit(0)

overlay("\\u26a1 Processing...", "#00aaff", 8000)
try:
    result = call_qwen(text, MODE)
    if MODE in ("explain", "translate"):
        # Show result in a styled floating window (no Terminal)
        title = "CODEC Explain" if MODE == "explain" else "CODEC Translate"
        # Also copy to clipboard so user can paste if needed
        subprocess.run(["pbcopy"], input=result.encode(), check=True)
        # Launch a clean floating result window
        safe_result = result.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"').replace("\n", "\\n")
        subprocess.Popen([sys.executable, "-c", f"""import tkinter as tk
from tkinter import font as tkfont
r=tk.Tk()
r.title('{title}')
r.attributes('-topmost', True)
r.configure(bg='#1a1a1a')
sw=r.winfo_screenwidth();sh=r.winfo_screenheight()
w,h=560,400
r.geometry(f'{{w}}x{{h}}+{{(sw-w)//2}}+{{(sh-h)//2}}')
r.minsize(400,250)
# Title bar
hdr=tk.Frame(r,bg='#E8711A',height=36);hdr.pack(fill='x')
hdr.pack_propagate(False)
tk.Label(hdr,text='{title}',fg='white',bg='#E8711A',font=('Helvetica',14,'bold')).pack(side='left',padx=12)
tk.Button(hdr,text='Copy',fg='white',bg='#cc5a00',relief='flat',font=('Helvetica',11),padx=8,
    command=lambda:[r.clipboard_clear(),r.clipboard_append(txt.get('1.0','end-1c'))]).pack(side='right',padx=6,pady=4)
# Text area
txt=tk.Text(r,wrap='word',bg='#1a1a1a',fg='#e0e0e0',font=('Menlo',13),relief='flat',
    padx=16,pady=12,insertbackground='#E8711A',selectbackground='#E8711A',borderwidth=0)
txt.pack(fill='both',expand=True)
txt.insert('1.0','{safe_result}')
txt.config(state='normal')
# Footer
ft=tk.Frame(r,bg='#111',height=32);ft.pack(fill='x')
ft.pack_propagate(False)
tk.Label(ft,text='Copied to clipboard  \u00b7  \u2318V to paste',fg='#666',bg='#111',font=('Helvetica',10)).pack(side='left',padx=12)
tk.Button(ft,text='Close',fg='#999',bg='#222',relief='flat',font=('Helvetica',11),padx=10,
    command=r.destroy).pack(side='right',padx=8,pady=3)
r.bind('<Escape>',lambda e:r.destroy())
r.mainloop()
"""], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        overlay("\u2705 {title}", "#44cc66", 2000)
    else:
        subprocess.run(["pbcopy"], input=result.encode(), check=True)
        time.sleep(0.3)
        subprocess.run(["osascript", "-e", 'tell application "System Events" to keystroke "v" using command down'])
        overlay("\\u2705 Text replaced!", "#44cc66", 2000)
except Exception as e:
    overlay("Error - check terminal", "#ff3333", 3000)
