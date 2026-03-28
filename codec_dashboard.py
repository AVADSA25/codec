"""CODEC v1.2 — Phone Dashboard & PWA"""
import os, json, sqlite3, time
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI(title="CODEC Dashboard")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DB_PATH = os.path.expanduser("~/.q_memory.db")
AUDIT_LOG = os.path.expanduser("~/.codec/audit.log")
CONFIG_PATH = os.path.expanduser("~/.codec/config.json")
TASK_QUEUE = os.path.expanduser("/tmp/q_task_queue.txt")
DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))

def get_db():
    return sqlite3.connect(DB_PATH)

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = os.path.join(DASHBOARD_DIR, "codec_dashboard.html")
    with open(html_path) as f:
        return f.read()

@app.get("/manifest.json")
async def manifest():
    return JSONResponse({
        "name": "CODEC",
        "short_name": "CODEC",
        "description": "CODEC — Computer Command Framework",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0a0a0a",
        "theme_color": "#E8711A",
        "icons": [
            {"src": "https://i.imgur.com/RbrQ7Bt.png", "sizes": "280x280", "type": "image/png"}
        ]
    })

@app.get("/api/status")
async def status():
    """Check if CODEC is running and return config"""
    config = {}
    try:
        with open(CONFIG_PATH) as f:
            config = json.load(f)
    except: pass

    # Check if CODEC process is alive
    import subprocess
    try:
        r = subprocess.run(["pgrep", "-f", "codec.py"], capture_output=True, text=True, timeout=3)
        alive = bool(r.stdout.strip())
    except:
        alive = False

    return {
        "alive": alive,
        "config": {
            "llm_provider": config.get("llm_provider", "unknown"),
            "llm_model": config.get("llm_model", "unknown"),
            "tts_engine": config.get("tts_engine", "unknown"),
            "tts_voice": config.get("tts_voice", "unknown"),
            "key_toggle": config.get("key_toggle", "f13"),
            "key_voice": config.get("key_voice", "f18"),
            "key_text": config.get("key_text", "f16"),
            "wake_word_enabled": config.get("wake_word_enabled", False),
            "streaming": config.get("streaming", True),
        }
    }

@app.get("/api/history")
async def history(limit: int = 50):
    """Get recent task history"""
    try:
        c = get_db()
        rows = c.execute(
            "SELECT id, timestamp, task, app, response FROM sessions ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
        c.close()
        return [{"id": r[0], "timestamp": r[1], "task": r[2], "app": r[3], "response": r[4]} for r in rows]
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/conversations")
async def conversations(limit: int = 100):
    """Get recent conversations"""
    try:
        c = get_db()
        rows = c.execute(
            "SELECT id, session_id, timestamp, role, content FROM conversations ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
        c.close()
        return [{"id": r[0], "session_id": r[1], "timestamp": r[2], "role": r[3], "content": r[4]} for r in rows]
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/audit")
async def audit(limit: int = 50):
    """Get recent audit log entries"""
    try:
        if not os.path.exists(AUDIT_LOG):
            return []
        with open(AUDIT_LOG) as f:
            lines = f.readlines()
        return [{"line": l.strip()} for l in lines[-limit:]][::-1]
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/command")
async def send_command(request: Request):
    """Send a text command to CODEC"""
    body = await request.json()
    task = body.get("task", "").strip()
    if not task:
        return JSONResponse({"error": "Empty task"}, status_code=400)

    # Write to task queue file — CODEC's dispatch will pick it up
    try:
        with open(TASK_QUEUE, "w") as f:
            json.dump({
                "task": task,
                "app": "CODEC Dashboard",
                "ts": datetime.now().isoformat(),
                "source": "pwa"
            }, f)

        # Also save to DB
        c = get_db()
        c.execute(
            "INSERT INTO sessions (timestamp, task, app, response) VALUES (?,?,?,?)",
            (datetime.now().isoformat(), task[:200], "CODEC Dashboard", "")
        )
        c.commit()
        c.close()

        # Write audit
        with open(AUDIT_LOG, "a") as f:
            f.write(f"[{datetime.now().isoformat()}] PWA_CMD: {task[:200]}\n")

        return {"status": "queued", "task": task}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/vision")
async def vision_analyze(request: Request):
    """Send image to Qwen Vision model for analysis"""
    body = await request.json()
    image_b64 = body.get("image", "")
    prompt = body.get("prompt", "Describe and analyze this image in detail.")
    if not image_b64:
        return JSONResponse({"error": "No image data"}, status_code=400)
    try:
        import requests as rq
        config = {}
        try:
            with open(CONFIG_PATH) as f: config = json.load(f)
        except: pass
        vision_url = config.get("vision_base_url", "http://localhost:8082/v1")
        vision_model = config.get("vision_model", "mlx-community/Qwen2.5-VL-7B-Instruct-4bit")
        payload = {
            "model": vision_model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                    {"type": "text", "text": prompt}
                ]
            }],
            "max_tokens": 4000,
            "temperature": 0.7
        }
        headers = {"Content-Type": "application/json"}
        r = rq.post(f"{vision_url}/chat/completions", json=payload, headers=headers, timeout=120)
        data = r.json()
        answer = data["choices"][0]["message"]["content"].strip()
        with open(AUDIT_LOG, "a") as f:
            f.write(f"[{datetime.now().isoformat()}] VISION: {prompt[:100]}\n")
        return {"response": answer, "model": vision_model}
    except Exception as e:
        import traceback; traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/response")
async def get_response():
    """Get latest PWA command response"""
    try:
        resp_file = "/tmp/q_pwa_response.json"
        if os.path.exists(resp_file):
            with open(resp_file) as f:
                data = json.load(f)
            os.unlink(resp_file)
            return data
        return {"response": None}
    except:
        return {"response": None}

@app.get("/api/tts")
async def tts(text: str = ""):
    """Generate speech and return audio file"""
    if not text:
        return JSONResponse({"error": "No text"}, status_code=400)
    try:
        import requests as rq
        config = {}
        try:
            with open(CONFIG_PATH) as f: config = json.load(f)
        except: pass
        tts_url = config.get("tts_url", "http://localhost:8085/v1/audio/speech")
        tts_model = config.get("tts_model", "mlx-community/Kokoro-82M-bf16")
        tts_voice = config.get("tts_voice", "am_adam")
        r = rq.post(tts_url, json={"model": tts_model, "input": text[:500], "voice": tts_voice, "speed": 1.1}, timeout=30)
        if r.status_code == 200:
            audio_path = "/tmp/q_pwa_audio.mp3"
            with open(audio_path, "wb") as f:
                f.write(r.content)
            return FileResponse(audio_path, media_type="audio/mpeg")
        return JSONResponse({"error": "TTS failed"}, status_code=500)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/screenshot")
async def screenshot():
    """Take screenshot of Mac Studio and return image"""
    import subprocess
    try:
        path = "/tmp/q_pwa_screenshot.png"
        subprocess.run(["screencapture", "-x", path], timeout=5)
        if os.path.exists(path):
            return FileResponse(path, media_type="image/png")
        return JSONResponse({"error": "Screenshot failed"}, status_code=500)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/clipboard")
async def get_clipboard():
    """Get Mac Studio clipboard content"""
    import subprocess
    try:
        r = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=3)
        return {"content": r.stdout[:2000]}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/clipboard")
async def set_clipboard(request: Request):
    """Set Mac Studio clipboard content"""
    import subprocess
    body = await request.json()
    text = body.get("text", "")
    try:
        p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
        p.communicate(text.encode())
        return {"status": "copied"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/upload")
async def upload_document(request: Request):
    """Extract text from uploaded PDF or text files"""
    import base64, subprocess
    body = await request.json()
    filename = body.get("filename", "file")
    data = body.get("data", "")
    if not data:
        return JSONResponse({"error": "No data"}, status_code=400)
    try:
        raw = base64.b64decode(data)
        ext = os.path.splitext(filename)[1].lower()
        if ext == ".pdf":
            pdf_path = "/tmp/q_pwa_upload.pdf"
            with open(pdf_path, "wb") as f: f.write(raw)
            r = subprocess.run(["pdftotext", pdf_path, "-"], capture_output=True, text=True, timeout=30)
            text_content = r.stdout[:200000].strip()
            if not text_content:
                text_content = ""
            return {"status": "ok", "text": text_content, "filename": filename}
        else:
            text_content = raw.decode("utf-8", errors="ignore")[:200000]
            return {"status": "ok", "text": text_content, "filename": filename}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/chat", response_class=HTMLResponse)
async def chat_page():
    chat_path = os.path.join(DASHBOARD_DIR, "codec_chat.html")
    with open(chat_path) as f:
        return f.read()


# Mike Chat conversation storage
QCHAT_DB = os.path.expanduser("~/.codec/qchat.db")

def qchat_db():
    import sqlite3
    conn = sqlite3.connect(QCHAT_DB)
    conn.execute('''CREATE TABLE IF NOT EXISTS qchat_sessions (
        id TEXT PRIMARY KEY, title TEXT, created_at TEXT, updated_at TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS qchat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,
        content TEXT, timestamp TEXT)''')
    conn.commit()
    return conn

@app.get("/api/qchat/sessions")
async def qchat_sessions():
    conn = qchat_db()
    rows = conn.execute("SELECT id, title, updated_at FROM qchat_sessions ORDER BY updated_at DESC LIMIT 30").fetchall()
    conn.close()
    return [{"id": r[0], "title": r[1], "updated_at": r[2]} for r in rows]

@app.get("/api/qchat/session/{sid}")
async def qchat_session(sid: str):
    conn = qchat_db()
    rows = conn.execute("SELECT role, content, timestamp FROM qchat_messages WHERE session_id=? ORDER BY id ASC", (sid,)).fetchall()
    conn.close()
    return [{"role": r[0], "content": r[1], "timestamp": r[2]} for r in rows]

@app.post("/api/qchat/save")
async def qchat_save(request: Request):
    body = await request.json()
    sid = body.get("session_id", "")
    title = body.get("title", "New Chat")
    messages = body.get("messages", [])
    from datetime import datetime
    now = datetime.now().isoformat()
    conn = qchat_db()
    conn.execute("INSERT OR REPLACE INTO qchat_sessions (id, title, created_at, updated_at) VALUES (?, ?, COALESCE((SELECT created_at FROM qchat_sessions WHERE id=?), ?), ?)",
        (sid, title[:60], sid, now, now))
    for m in messages:
        conn.execute("INSERT INTO qchat_messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
            (sid, m.get("role","user"), m.get("content",""), now))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/upload_image")
async def upload_image(request: Request):
    """Upload image, send to vision, return description"""
    body = await request.json()
    image_b64 = body.get("data", "")
    filename = body.get("filename", "image.jpg")
    prompt = body.get("prompt", "Describe and analyze this image in detail.")
    if not image_b64 or len(image_b64) < 100:
        return JSONResponse({"error": "No image data"}, status_code=400)
    try:
        import requests as rq
        config = {}
        try:
            with open(CONFIG_PATH) as f: config = json.load(f)
        except: pass
        vision_url = config.get("vision_base_url", "http://localhost:8082/v1")
        vision_model = config.get("vision_model", "mlx-community/Qwen2.5-VL-7B-Instruct-4bit")
        payload = {
            "model": vision_model,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                {"type": "text", "text": prompt}
            ]}],
            "max_tokens": 4000, "temperature": 0.7
        }
        r = rq.post(f"{vision_url}/chat/completions", json=payload, headers={"Content-Type": "application/json"}, timeout=120)
        data = r.json()
        answer = data["choices"][0]["message"]["content"].strip()
        return {"text": answer, "filename": filename}
    except Exception as e:
        import traceback; traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)

# Vibe Code session storage
VIBE_DB = os.path.expanduser("~/.codec/vibe.db")

def vibe_db():
    import sqlite3
    conn = sqlite3.connect(VIBE_DB)
    conn.execute('''CREATE TABLE IF NOT EXISTS vibe_sessions (
        id TEXT PRIMARY KEY, title TEXT, language TEXT, code TEXT, created_at TEXT, updated_at TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS vibe_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,
        content TEXT, timestamp TEXT)''')
    conn.commit()
    return conn

@app.get("/api/vibe/sessions")
async def vibe_sessions():
    conn = vibe_db()
    rows = conn.execute("SELECT id, title, language, updated_at FROM vibe_sessions ORDER BY updated_at DESC LIMIT 30").fetchall()
    conn.close()
    return [{"id": r[0], "title": r[1], "language": r[2], "updated_at": r[3]} for r in rows]

@app.get("/api/vibe/session/{sid}")
async def vibe_session(sid: str):
    conn = vibe_db()
    session = conn.execute("SELECT id, title, language, code FROM vibe_sessions WHERE id=?", (sid,)).fetchone()
    msgs = conn.execute("SELECT role, content, timestamp FROM vibe_messages WHERE session_id=? ORDER BY id ASC", (sid,)).fetchall()
    conn.close()
    return {
        "session": {"id": session[0], "title": session[1], "language": session[2], "code": session[3]} if session else None,
        "messages": [{"role": r[0], "content": r[1], "timestamp": r[2]} for r in msgs]
    }

@app.post("/api/vibe/save")
async def vibe_save(request: Request):
    body = await request.json()
    sid = body.get("session_id", "")
    title = body.get("title", "Untitled")
    language = body.get("language", "python")
    code = body.get("code", "")
    messages = body.get("messages", [])
    from datetime import datetime
    now = datetime.now().isoformat()
    conn = vibe_db()
    conn.execute("INSERT OR REPLACE INTO vibe_sessions (id, title, language, code, created_at, updated_at) VALUES (?, ?, ?, ?, COALESCE((SELECT created_at FROM vibe_sessions WHERE id=?), ?), ?)",
        (sid, title[:60], language, code, sid, now, now))
    for m in messages:
        conn.execute("INSERT INTO vibe_messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
            (sid, m.get("role","user"), m.get("content",""), now))
    conn.commit()
    conn.close()
    return {"ok": True}

@app.get("/vibe", response_class=HTMLResponse)
async def vibe_page():
    vibe_path = os.path.join(DASHBOARD_DIR, "codec_vibe.html")
    with open(vibe_path) as f:
        return f.read()

@app.post("/api/preview")
async def preview_code(request: Request):
    body = await request.json()
    code = body.get("code", "")
    preview_path = "/tmp/codec_preview.html"
    with open(preview_path, "w") as f:
        f.write(code)
    return {"url": "/preview_frame", "path": preview_path}

@app.get("/preview_frame", response_class=HTMLResponse)
async def preview_frame():
    try:
        with open("/tmp/codec_preview.html") as f:
            return f.read()
    except:
        return "<html><body style='background:#0a0a0a;color:#888;padding:40px;font-family:sans-serif'><h2>No preview available</h2><p>Write some HTML and click Preview.</p></body></html>"

@app.post("/api/run_code")
async def run_code(request: Request):
    import asyncio, time as _time
    body = await request.json()
    code = body.get("code", "")
    language = body.get("language", "python")
    filename = body.get("filename", "script.py")
    if not code.strip():
        return JSONResponse({"error": "No code"}, status_code=400)
    BLOCKED = ["rm -rf /", "sudo rm", "mkfs", "> /dev/sd", "dd if=", ":(){ :|:"]
    for b in BLOCKED:
        if b in code.lower():
            return JSONResponse({"error": f"Blocked: {b}"}, status_code=403)
    import tempfile
    ext = {"python": ".py", "javascript": ".js", "bash": ".sh"}.get(language, ".txt")
    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False, mode="w")
    tmp.write(code); tmp.close()
    cmd = {"python": ["python3.13", tmp.name], "javascript": ["node", tmp.name], "bash": ["bash", tmp.name]}.get(language, ["python3.13", tmp.name])
    start = _time.time()
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=os.path.expanduser("~"))
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        return {"stdout": stdout.decode(errors="replace")[:10000], "stderr": stderr.decode(errors="replace")[:5000], "exit_code": proc.returncode, "elapsed": round(_time.time()-start,1)}
    except asyncio.TimeoutError:
        return {"stdout":"","stderr":"Timed out (30s)","exit_code":-1,"elapsed":30}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        try: os.unlink(tmp.name)
        except: pass

@app.post("/api/save_file")
async def save_file(request: Request):
    body = await request.json()
    filename = os.path.basename(body.get("filename", "untitled.py"))
    content = body.get("content", "")
    directory = os.path.expanduser(body.get("directory", "~/codec-workspace"))
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, filename)
    with open(path, "w") as f: f.write(content)
    return {"path": path, "size": len(content)}

@app.post("/api/save_skill")
async def save_skill(request: Request):
    body = await request.json()
    filename = os.path.basename(body.get("filename", "custom_skill.py"))
    if not filename.endswith(".py"): filename += ".py"
    content = body.get("content", "")
    path = os.path.join(os.path.expanduser("~/.codec/skills"), filename)
    with open(path, "w") as f: f.write(content)
    return {"path": path, "skill": filename, "size": len(content)}

# In-memory job store for deep research (survives for session lifetime)
_research_jobs: dict = {}

@app.post("/api/deep_research")
async def deep_research_start(request: Request):
    """Start deep research job — returns job_id immediately (avoids proxy timeouts)"""
    import asyncio, threading, uuid
    body = await request.json()
    topic = body.get("topic", "")
    if not topic or len(topic) < 5:
        return JSONResponse({"error": "Topic too short"}, status_code=400)

    job_id = str(uuid.uuid4())[:8]
    _research_jobs[job_id] = {"status": "running", "topic": topic, "started": datetime.now().isoformat()}

    def _run():
        try:
            from deep_research import run_deep_research
            result = run_deep_research(topic)
            _research_jobs[job_id].update(result)
            _research_jobs[job_id]["status"] = result.get("status", "complete")
        except Exception as e:
            import traceback; traceback.print_exc()
            _research_jobs[job_id]["status"] = "error"
            _research_jobs[job_id]["error"] = str(e)

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id, "status": "running", "topic": topic}


@app.get("/api/deep_research/{job_id}")
async def deep_research_status(job_id: str):
    """Poll research job status"""
    job = _research_jobs.get(job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    return job


@app.post("/api/chat")
async def chat_completion(request: Request):
    """Direct LLM chat with full context window"""
    body = await request.json()
    messages = body.get("messages", [])
    if not messages:
        return JSONResponse({"error": "No messages"}, status_code=400)

    # Check for images — route to vision model
    images = body.get("images", [])
    if images:
        import requests as rq2
        config2 = {}
        try:
            with open(CONFIG_PATH) as f: config2 = json.load(f)
        except: pass
        vision_url = config2.get("vision_base_url", "http://localhost:8082/v1")
        vision_model = config2.get("vision_model", "mlx-community/Qwen2.5-VL-7B-Instruct-4bit")
        # Build multimodal message: last user text + all images
        last_text = ""
        for m in reversed(messages):
            if m.get("role") == "user" and isinstance(m.get("content"), str):
                last_text = m["content"]
                break
        if not last_text:
            last_text = "Describe and analyze this image in detail."
        mm_content = []
        for img_b64 in images:
            mm_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}})
        mm_content.append({"type": "text", "text": last_text})
        v_payload = {
            "model": vision_model,
            "messages": [{"role": "user", "content": mm_content}],
            "max_tokens": 4000,
            "temperature": 0.7
        }
        vr = rq2.post(f"{vision_url}/chat/completions", json=v_payload, headers={"Content-Type": "application/json"}, timeout=120)
        vdata = vr.json()
        vanswer = vdata["choices"][0]["message"]["content"].strip()
        import re as re2
        vanswer = re2.sub(r'<think>[\s\S]*?</think>', '', vanswer).strip()
        return {"response": vanswer, "model": vision_model}

    try:
        import requests as rq
        config = {}
        try:
            with open(CONFIG_PATH) as f: config = json.load(f)
        except: pass
        base_url = config.get("llm_base_url", "http://localhost:8081/v1")
        model = config.get("llm_model", "mlx-community/Qwen3.5-35B-A3B-4bit")
        api_key = config.get("llm_api_key", "")
        kwargs = config.get("llm_kwargs", {})
        headers = {"Content-Type": "application/json"}
        if api_key: headers["Authorization"] = f"Bearer {api_key}"
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": 28000,
            "temperature": 0.7,
            "top_p": 0.9,
            "frequency_penalty": 1.1,
            "stream": False
        }
        payload.update(kwargs)
        r = rq.post(f"{base_url}/chat/completions", json=payload, headers=headers, timeout=300)
        data = r.json()
        answer = data["choices"][0]["message"]["content"].strip()
        # Strip thinking tags
        import re
        answer = re.sub(r'<think>[\s\S]*?</think>', '', answer).strip()
        answer = re.sub(r'###\s*FINAL ANSWER:\s*', '', answer).strip()
        return {"response": answer, "model": model}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/skills")
async def skills():
    """List installed skills"""
    skills_dir = os.path.expanduser("~/.codec/skills/")
    result = []
    try:
        for f in sorted(os.listdir(skills_dir)):
            if f.endswith(".py") and not f.startswith("_"):
                path = os.path.join(skills_dir, f)
                name = f.replace(".py", "")
                triggers = []
                try:
                    with open(path) as sf:
                        for line in sf:
                            if "SKILL_TRIGGERS" in line:
                                triggers = eval(line.split("=", 1)[1].strip())
                                break
                except: pass
                result.append({"name": name, "triggers": triggers})
    except: pass
    return result

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8090)
