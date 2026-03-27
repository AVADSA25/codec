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
        "explain": "Explain this text simply and concisely. What is it about? Key points?"
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
    subprocess.Popen([sys.executable, "-c", f"""import tkinter as tk
r=tk.Tk();r.overrideredirect(True);r.attributes('-topmost',True);r.attributes('-alpha',0.95);r.configure(bg='#0a0a0a')
sw=r.winfo_screenwidth();sh=r.winfo_screenheight()
r.geometry(f'280x54+{{(sw-280)//2}}+{{sh-130}}')
c=tk.Canvas(r,bg='#0a0a0a',highlightthickness=0,width=280,height=54);c.pack()
c.create_rectangle(1,1,279,53,outline='{color}',width=1)
c.create_text(140,27,text='{text}',fill='{color}',font=('Helvetica',13))
r.after({duration},r.destroy);r.mainloop()"""], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

text = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout.strip()
if not text: sys.exit(0)

overlay("\\u26a1 Processing...", "#00aaff", 8000)
try:
    result = call_qwen(text, MODE)
    if MODE == "explain":
        # Write to temp file and open in Terminal
        import tempfile
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, prefix="codec_explain_")
        tmp.write(result)
        tmp.close()
        subprocess.run(["osascript", "-e", 'tell application "Terminal" to activate'])
        subprocess.run(["osascript", "-e", f'tell application "Terminal" to do script "clear && echo && echo CODEC_EXPLAIN && echo && cat {tmp.name} && echo && echo ━━━━━━━━━━━━━━━━━━━━━"'])
        overlay("\u2705 Opened in Terminal", "#44cc66", 2000)
    else:
        subprocess.run(["pbcopy"], input=result.encode(), check=True)
        time.sleep(0.3)
        subprocess.run(["osascript", "-e", 'tell application "System Events" to keystroke "v" using command down'])
        overlay("\\u2705 Text replaced!", "#44cc66", 2000)
except Exception as e:
    overlay("Error - check terminal", "#ff3333", 3000)
