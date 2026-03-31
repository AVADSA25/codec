#!/usr/bin/env python3
"""CODEC Q-Watcher v3.0 | Smart draft/reply with Screenshot Vision"""
import os, time, requests, subprocess, tempfile, json, signal, re, base64
signal.signal(signal.SIGINT, lambda *a: None)
signal.signal(signal.SIGTERM, lambda *a: None)

QWEN_BASE_URL  = "http://localhost:8081/v1"
QWEN_MODEL     = "mlx-community/Qwen3.5-35B-A3B-4bit"
QWEN_VISION_URL = "http://localhost:8082/v1"
QWEN_VISION_MODEL = "mlx-community/Qwen2.5-VL-7B-Instruct-4bit"
KOKORO_URL     = "http://localhost:8085/v1/audio/speech"
KOKORO_MODEL   = "mlx-community/Kokoro-82M-bf16"
TTS_VOICE      = "am_adam"
TASK_FILE      = os.path.expanduser("~/.codec/draft_task.json")

def _load_watcher_config():
    """Load user_name and assistant_name from config."""
    try:
        with open(os.path.expanduser("~/.codec/config.json")) as _f:
            _c = json.load(_f)
        return _c.get("user_name", ""), _c.get("assistant_name", "CODEC")
    except Exception:
        return "", "CODEC"

_W_USER_NAME, _W_ASSISTANT_NAME = _load_watcher_config()
_W_USER_REF = _W_USER_NAME if _W_USER_NAME else "the user"

DRAFT_SYSTEM = f"""You are {_W_ASSISTANT_NAME}, elite AI writing assistant.
The user has dyslexia — fix ALL grammar and spelling mistakes automatically.

STRICT RULES:
- OUTPUT ONLY the final message text. Nothing else.
- No preamble: never start with "Here is", "Sure", "Draft:", "Reply:" etc.
- No sign-off unless {_W_USER_REF} specifically asks (no "Best regards", "Cheers" etc.)
- Fix all grammar and spelling while keeping {_W_USER_REF}'s natural voice.
- The user is direct, warm, confident, and professional.
- Match platform tone: email=structured, WhatsApp=casual+warm, LinkedIn=professional.
- If {_W_USER_REF} says "say X" or "tell them X", expand X into a polished natural message.
- If screen context shows a conversation, understand who wrote what and reply appropriately.
- NEVER add "Done" or meta-commentary.
- NEVER wrap output in quotes."""

def strip_think(text):
    return re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()

def extract_content(response_json):
    msg = response_json["choices"][0]["message"]
    content = msg.get("content", "").strip()
    if content:
        return strip_think(content)
    reasoning = msg.get("reasoning", "").strip()
    if reasoning:
        return strip_think(reasoning)
    return ""

def clean_draft(text):
    text = strip_think(text)
    preambles = [
        "here is", "here's", "draft:", "reply:", "message:", "email:",
        "sure,", "of course,", "here you go:", "the message:",
        "here is the", "here's the", "below is", "certainly,"
    ]
    lines = text.split("\n", 1)
    if len(lines) > 1 and any(lines[0].lower().strip().startswith(p) for p in preambles):
        text = lines[1].strip()
    if len(text) > 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1]
    if len(text) > 2 and text[0] == "'" and text[-1] == "'":
        text = text[1:-1]
    return text.strip()

def screenshot_ctx():
    """Take screenshot and use Qwen Vision to read screen content"""
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.close()
        subprocess.run(["screencapture", "-x", tmp.name], timeout=5)
        if not os.path.exists(tmp.name) or os.path.getsize(tmp.name) < 1000:
            return ""
        with open(tmp.name, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        os.unlink(tmp.name)

        print(f"[Watcher] Reading screen via Vision...")
        r = requests.post(f"{QWEN_VISION_URL}/chat/completions",
            json={
                "model": QWEN_VISION_MODEL,
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                    {"type": "text", "text": "Read all visible text on this screen. Focus on any chat messages, emails, or conversations visible. Include sender names and message content. Output raw text only, no commentary."}
                ]}],
                "max_tokens": 800
            },
            timeout=60)
        if r.status_code == 200:
            content = r.json()["choices"][0]["message"].get("content", "").strip()
            if content:
                print(f"[Watcher] Screen context: {len(content)} chars")
                return content[:2000]
    except Exception as e:
        print(f"[Watcher] Vision error: {e}")
    return ""

def speak(text):
    try:
        clean = text[:300]
        clean = re.sub(r'\*+', '', clean)
        clean = re.sub(r'#+\s*', '', clean)
        clean = clean.replace('"','').replace("'","").strip()
        if not clean: return
        resp = requests.post(KOKORO_URL,
            json={"model": KOKORO_MODEL, "input": clean, "voice": TTS_VOICE},
            stream=True, timeout=20)
        if resp.status_code == 200:
            tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            for chunk in resp.iter_content(chunk_size=4096):
                tmp.write(chunk)
            tmp.close()
            subprocess.Popen(["afplay", tmp.name])
    except Exception as e:
        print(f"[WARN] TTS/speak error: {e}")

def paste_text(text):
    subprocess.run(["pbcopy"], input=text.encode(), timeout=5)
    time.sleep(0.3)
    subprocess.run(["osascript", "-e",
        'tell application "System Events" to keystroke "v" using command down'],
        capture_output=True, timeout=5)

def handle_draft(task, ctx, app):
    print(f"[Watcher] Drafting: {task[:60]}")
    subprocess.run(["osascript", "-e",
        'display notification "Drafting..." with title "CODEC"'],
        capture_output=True)

    # If context is thin, take a fresh screenshot
    if not ctx or len(ctx.strip()) < 50:
        fresh = screenshot_ctx()
        if fresh:
            ctx = fresh

    app_lower = app.lower()
    if "mail" in app_lower or "gmail" in app_lower:
        platform = "email"
    elif "whatsapp" in app_lower:
        platform = "WhatsApp message"
    elif "linkedin" in app_lower:
        platform = "LinkedIn message"
    elif "slack" in app_lower:
        platform = "Slack message"
    elif "discord" in app_lower:
        platform = "Discord message"
    elif "telegram" in app_lower:
        platform = "Telegram message"
    elif "imessage" in app_lower or "messages" in app_lower:
        platform = "iMessage"
    elif "twitter" in app_lower or " x " in app_lower:
        platform = "X/Twitter post"
    else:
        platform = "message"

    if ctx and len(ctx.strip()) > 10:
        context_block = f"\n\nSCREEN CONTEXT (what M is currently looking at):\n{ctx[:1200]}"
    else:
        context_block = "\n\n(No screen context available - write based on M's instruction alone)"

    prompt = f"Platform: {platform}\nApp: {app}{context_block}\n\nM's instruction: {task}\n\nWrite the final {platform} text now. Output ONLY the message."

    messages = [
        {"role": "system", "content": DRAFT_SYSTEM},
        {"role": "user", "content": prompt}
    ]

    draft = ""
    for attempt in range(3):
        try:
            if attempt > 0:
                subprocess.run(["osascript", "-e",
                    f'display notification "Retrying {attempt+1}/3..." with title "CODEC"'],
                    capture_output=True)
                time.sleep(2 ** attempt)
            r = requests.post(f"{QWEN_BASE_URL}/chat/completions",
                json={
                    "model": QWEN_MODEL,
                    "messages": messages,
                    "max_tokens": 500,
                    "temperature": 0.6,
                    "chat_template_kwargs": {"enable_thinking": False}
                },
                timeout=90)
            if r.status_code == 200:
                raw = extract_content(r.json())
                draft = clean_draft(raw)
                if draft:
                    break
                else:
                    print(f"[Watcher] Attempt {attempt+1}: empty after cleaning")
        except Exception as e:
            print(f"[Watcher] Attempt {attempt+1}: {e}")

    if not draft:
        subprocess.run(["osascript", "-e",
            'display notification "Draft failed" with title "CODEC"'],
            capture_output=True)
        print("[Watcher] Draft failed after 3 attempts")
        return

    paste_text(draft)
    print(f"[Watcher] Pasted: {draft[:80]}")
    subprocess.run(["osascript", "-e",
        'display notification "Pasted!" with title "CODEC"'],
        capture_output=True)
    speak("Draft pasted.")

print("[CODEC Watcher v3.0] Running. Screenshot Vision for context.")
while True:
    if os.path.exists(TASK_FILE):
        try:
            with open(TASK_FILE) as f:
                data = json.load(f)
            os.unlink(TASK_FILE)
            handle_draft(data["task"], data.get("ctx",""), data.get("app",""))
        except Exception as e:
            print(f"[Watcher] Error: {e}")
            import traceback; traceback.print_exc()
    time.sleep(0.2)
