"""CODEC v1.2 — Phone Dashboard & PWA"""
import os, json, sqlite3, time, logging, secrets, subprocess, hmac
from datetime import datetime, timedelta

log = logging.getLogger("codec_dashboard")
from pathlib import Path
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse as StarletteJSONResponse
import uvicorn

app = FastAPI(title="CODEC Dashboard")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:8090", "http://127.0.0.1:8090", "https://codec.lucyvpa.com"], allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"], allow_headers=["*"])


class AuthMiddleware(BaseHTTPMiddleware):
    """Combined auth: bearer token (API) + biometric Touch ID sessions (dashboard)."""

    # Routes that never require authentication
    PUBLIC_ROUTES = {"/", "/chat", "/vibe", "/voice", "/auth", "/health", "/favicon.ico", "/manifest.json"}
    PUBLIC_PREFIXES = ("/api/auth/", "/static")

    async def dispatch(self, request, call_next):
        from codec_config import DASHBOARD_TOKEN
        path = request.url.path

        # Always allow public routes
        if path in self.PUBLIC_ROUTES:
            return await call_next(request)
        if any(path.startswith(p) for p in self.PUBLIC_PREFIXES):
            return await call_next(request)
        # Allow static assets
        if path.endswith(('.css', '.js', '.png', '.ico', '.svg', '.woff2', '.woff', '.ttf')):
            return await call_next(request)

        # ── Layer 0: No auth configured → allow all ──
        if not DASHBOARD_TOKEN and (not AUTH_ENABLED or not _auth_available()):
            return await call_next(request)

        # ── Layer 1: Token check (API key — works as standalone auth for API) ──
        if DASHBOARD_TOKEN and path.startswith("/api/"):
            auth = request.headers.get("Authorization", "")
            if auth and hmac.compare_digest(auth, f"Bearer {DASHBOARD_TOKEN}"):
                return await call_next(request)
            token = request.query_params.get("token", "")
            if token and hmac.compare_digest(token, DASHBOARD_TOKEN):
                return await call_next(request)

        # ── Layer 2: Biometric / PIN session check ──
        if AUTH_ENABLED and _auth_available():
            if _verify_biometric_session(request):
                return await call_next(request)
            # Biometric failed — reject
            if path.startswith("/api/") or path.startswith("/ws"):
                return StarletteJSONResponse({"error": "Not authenticated"}, status_code=401)
            from starlette.responses import RedirectResponse
            return RedirectResponse(url="/auth")

        # ── Layer 3: No biometric available — token-only mode ──
        if DASHBOARD_TOKEN and path.startswith("/api/"):
            # Token was already checked above and didn't match
            return StarletteJSONResponse(
                {"error": "Unauthorized. Set dashboard_token in config.json or pass ?token=YOUR_TOKEN"},
                status_code=401
            )

        return await call_next(request)


app.add_middleware(AuthMiddleware)

DB_PATH = os.path.expanduser("~/.q_memory.db")
AUDIT_LOG = os.path.expanduser("~/.codec/audit.log")
CONFIG_PATH = os.path.expanduser("~/.codec/config.json")
TASK_QUEUE = os.path.expanduser("~/.codec/task_queue.txt")
DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Biometric (Touch ID) Auth ──
def _load_cfg():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}

_bio_cfg = _load_cfg()
AUTH_ENABLED = _bio_cfg.get("auth_enabled", False)
AUTH_SESSION_HOURS = _bio_cfg.get("auth_session_hours", 24)
AUTH_BINARY = os.path.join(DASHBOARD_DIR, "codec_auth", "codec_auth")
AUTH_PIN_HASH = _bio_cfg.get("auth_pin_hash", "")  # SHA-256 of user's PIN
AUTH_COOKIE_NAME = "codec_session"

# Persistent session store — survives PM2 restarts
_SESSION_FILE = os.path.expanduser("~/.codec/.auth_sessions.json")
_auth_sessions = {}  # token -> {created: datetime, ip: str, method: str}

def _load_sessions():
    """Load sessions from disk on startup."""
    global _auth_sessions
    try:
        if os.path.isfile(_SESSION_FILE):
            with open(_SESSION_FILE) as f:
                raw = json.load(f)
            now = datetime.now()
            for tok, data in raw.items():
                created = datetime.fromisoformat(data["created"])
                if now - created < timedelta(hours=AUTH_SESSION_HOURS):
                    _auth_sessions[tok] = {
                        "created": created,
                        "ip": data.get("ip", "unknown"),
                        "method": data.get("method", "unknown"),
                    }
            log.info("Restored %d auth session(s) from disk", len(_auth_sessions))
    except Exception as e:
        log.warning("Could not load auth sessions: %s", e)

def _save_sessions():
    """Persist current sessions to disk."""
    try:
        os.makedirs(os.path.dirname(_SESSION_FILE), exist_ok=True)
        raw = {}
        for tok, data in _auth_sessions.items():
            raw[tok] = {
                "created": data["created"].isoformat(),
                "ip": data.get("ip", "unknown"),
                "method": data.get("method", "unknown"),
            }
        with open(_SESSION_FILE, "w") as f:
            json.dump(raw, f)
        os.chmod(_SESSION_FILE, 0o600)
    except Exception as e:
        log.warning("Could not save auth sessions: %s", e)

_load_sessions()

def _is_auth_compiled():
    return os.path.isfile(AUTH_BINARY) and os.access(AUTH_BINARY, os.X_OK)

def _auth_available():
    """Check if any auth method is available (Touch ID binary or PIN configured)."""
    return _is_auth_compiled() or bool(AUTH_PIN_HASH)

def _verify_biometric_session(request):
    """Check if the request has a valid auth session cookie."""
    if not AUTH_ENABLED or not _auth_available():
        return True
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token or token not in _auth_sessions:
        return False
    session = _auth_sessions[token]
    if datetime.now() - session["created"] > timedelta(hours=AUTH_SESSION_HOURS):
        del _auth_sessions[token]
        _save_sessions()
        return False
    return True

_db_conn = None

def get_db():
    global _db_conn
    if _db_conn is None:
        _db_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _db_conn.execute("PRAGMA journal_mode=WAL")
        _db_conn.execute("PRAGMA busy_timeout=5000")
        _db_conn.row_factory = sqlite3.Row
    return _db_conn

_NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}

# ═══════════════════════════════════════════════════════════════
# BIOMETRIC AUTH ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@app.get("/auth", response_class=HTMLResponse)
async def auth_page():
    """Serve the biometric authentication page."""
    auth_path = os.path.join(DASHBOARD_DIR, "codec_auth.html")
    if os.path.exists(auth_path):
        with open(auth_path) as f:
            return HTMLResponse(f.read(), headers=_NO_CACHE)
    return HTMLResponse("<h1>Auth page not found</h1>", status_code=500)


@app.get("/api/auth/check")
async def auth_check():
    """Check which auth methods are available (Touch ID and/or PIN)."""
    result = {"touchid_available": False, "pin_available": bool(AUTH_PIN_HASH)}

    if _is_auth_compiled():
        try:
            r = subprocess.run([AUTH_BINARY, "--check"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                data = json.loads(r.stdout)
                result["touchid_available"] = data.get("available", False)
                result["method"] = data.get("method", "none")
        except Exception:
            pass

    result["available"] = result["touchid_available"] or result["pin_available"]
    if not result["available"]:
        result["reason"] = "No auth method configured. Compile Touch ID binary or set auth_pin_hash in config.json."
    return result


@app.post("/api/auth/verify")
async def auth_verify(request: Request):
    """Trigger Touch ID verification on the Mac."""
    if not _is_auth_compiled():
        return JSONResponse({"error": "Auth binary not compiled"}, status_code=500)
    try:
        r = subprocess.run(
            [AUTH_BINARY, "--verify"],
            capture_output=True, text=True, timeout=65
        )
        if r.returncode == 0:
            result = json.loads(r.stdout)
            client_ip = request.client.host if request.client else "unknown"

            # Audit log every attempt
            try:
                os.makedirs(os.path.dirname(AUDIT_LOG), exist_ok=True)
                with open(AUDIT_LOG, "a") as f:
                    if result.get("authenticated"):
                        f.write(f"[{datetime.now().isoformat()}] AUTH_SUCCESS: method={result.get('method')} ip={client_ip}\n")
                    else:
                        f.write(f"[{datetime.now().isoformat()}] AUTH_FAILED: error={result.get('error')} ip={client_ip}\n")
            except Exception:
                pass

            if result.get("authenticated"):
                token = result.get("token", secrets.token_hex(32))
                _auth_sessions[token] = {
                    "created": datetime.now(),
                    "ip": client_ip,
                    "method": result.get("method", "unknown"),
                }
                _save_sessions()
                return {
                    "authenticated": True,
                    "method": result.get("method"),
                    "token": token,
                    "expires_hours": AUTH_SESSION_HOURS,
                }
            else:
                return {
                    "authenticated": False,
                    "error": result.get("error", "Authentication failed"),
                }
        return JSONResponse({"error": "Auth binary failed"}, status_code=500)
    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "Authentication timed out"}, status_code=408)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/auth/pin")
async def auth_pin(request: Request):
    """Verify a PIN code."""
    import hashlib
    if not AUTH_PIN_HASH:
        return JSONResponse({"error": "PIN authentication not configured"}, status_code=400)
    try:
        body = await request.json()
        pin = str(body.get("pin", ""))
    except Exception:
        return JSONResponse({"error": "Missing pin field"}, status_code=400)

    pin_hash = hashlib.sha256(pin.encode()).hexdigest()
    client_ip = request.client.host if request.client else "unknown"

    # Audit log
    try:
        os.makedirs(os.path.dirname(AUDIT_LOG), exist_ok=True)
        with open(AUDIT_LOG, "a") as f:
            if pin_hash == AUTH_PIN_HASH:
                f.write(f"[{datetime.now().isoformat()}] AUTH_SUCCESS: method=pin ip={client_ip}\n")
            else:
                f.write(f"[{datetime.now().isoformat()}] AUTH_FAILED: method=pin error=wrong_pin ip={client_ip}\n")
    except Exception:
        pass

    if pin_hash == AUTH_PIN_HASH:
        token = secrets.token_hex(32)
        _auth_sessions[token] = {
            "created": datetime.now(),
            "ip": client_ip,
            "method": "pin",
        }
        _save_sessions()
        return {
            "authenticated": True,
            "method": "pin",
            "token": token,
            "expires_hours": AUTH_SESSION_HOURS,
        }
    else:
        return {"authenticated": False, "error": "Incorrect PIN"}


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    """Invalidate the current biometric session."""
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if token and token in _auth_sessions:
        del _auth_sessions[token]
        _save_sessions()
    return {"logged_out": True}


@app.get("/api/auth/status")
async def auth_status(request: Request):
    """Check if current session is valid."""
    valid = _verify_biometric_session(request)
    return {
        "authenticated": valid,
        "auth_enabled": AUTH_ENABLED,
        "touchid_compiled": _is_auth_compiled(),
        "pin_configured": bool(AUTH_PIN_HASH),
    }


# ═══════════════════════════════════════════════════════════════
# DASHBOARD ROUTES
# ═══════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = os.path.join(DASHBOARD_DIR, "codec_dashboard.html")
    with open(html_path) as f:
        return HTMLResponse(f.read(), headers=_NO_CACHE)

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
    except Exception as e:
        log.warning(f"Non-critical error: {e}")

    # Check if CODEC process is alive
    import subprocess
    try:
        r = subprocess.run(["pgrep", "-f", "codec.py"], capture_output=True, text=True, timeout=3)
        alive = bool(r.stdout.strip())
    except Exception as e:
        log.warning(f"Non-critical error: {e}")
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


def _mask_sensitive(value: str) -> str:
    """Mask sensitive field values, showing only last 4 characters."""
    if not value or not isinstance(value, str):
        return ""
    if len(value) <= 4:
        return "****"
    return "*" * (len(value) - 4) + value[-4:]


# Fields that contain secrets and must be masked in GET responses
_SENSITIVE_FIELDS = {"llm_api_key", "dashboard_token", "auth_pin_hash"}

# Validation rules: field -> (type, required, extra_checks)
# extra_checks is a callable returning (ok, error_msg)
_VALIDATION_RULES = {
    "agent_name":          (str,  True,  lambda v: (len(v.strip()) > 0, "agent_name cannot be empty")),
    "llm_provider":        (str,  True,  lambda v: (len(v.strip()) > 0, "llm_provider cannot be empty")),
    "llm_model":           (str,  False, None),
    "llm_base_url":        (str,  False, lambda v: (v == "" or v.startswith("http"), "llm_base_url must be a valid URL")),
    "llm_api_key":         (str,  False, None),
    "streaming":           (bool, False, None),
    "vision_base_url":     (str,  False, lambda v: (v == "" or v.startswith("http"), "vision_base_url must be a valid URL")),
    "vision_model":        (str,  False, None),
    "tts_engine":          (str,  False, None),
    "tts_url":             (str,  False, lambda v: (v == "" or v.startswith("http"), "tts_url must be a valid URL")),
    "tts_model":           (str,  False, None),
    "tts_voice":           (str,  False, None),
    "stt_engine":          (str,  False, None),
    "stt_url":             (str,  False, lambda v: (v == "" or v.startswith("http"), "stt_url must be a valid URL")),
    "key_toggle":          (str,  True,  lambda v: (len(v.strip()) > 0, "key_toggle cannot be empty")),
    "key_voice":           (str,  True,  lambda v: (len(v.strip()) > 0, "key_voice cannot be empty")),
    "key_text":            (str,  True,  lambda v: (len(v.strip()) > 0, "key_text cannot be empty")),
    "wake_word_enabled":   (bool, False, None),
    "wake_phrases":        (list, False, None),
    "wake_energy":         ((int, float), False, lambda v: (v >= 0, "wake_energy cannot be negative")),
    "auth_enabled":        (bool, False, None),
    "auth_session_hours":  ((int, float), False, lambda v: (v > 0, "auth_session_hours must be positive")),
    "dashboard_token":     (str,  False, None),
}


def _validate_config_updates(flat: dict) -> list:
    """Validate flattened config values. Returns list of error strings."""
    errors = []
    for key, value in flat.items():
        rule = _VALIDATION_RULES.get(key)
        if not rule:
            continue  # allow unknown keys through (forward compat)
        expected_type, required, check_fn = rule
        # Skip masked sensitive values (client didn't change them)
        if key in _SENSITIVE_FIELDS and isinstance(value, str) and value.startswith("*"):
            continue
        if not isinstance(value, expected_type):
            errors.append(f"{key}: expected {expected_type.__name__ if isinstance(expected_type, type) else 'number'}, got {type(value).__name__}")
            continue
        if check_fn:
            ok, msg = check_fn(value)
            if not ok:
                errors.append(msg)
    return errors


@app.get("/api/config")
async def get_config():
    """Return full editable config for Settings UI (sensitive fields masked)."""
    config = {}
    try:
        with open(CONFIG_PATH) as f:
            config = json.load(f)
    except Exception:
        pass
    # Group into sections for the UI
    result = {
        "llm": {
            "llm_provider": config.get("llm_provider", "mlx"),
            "llm_model": config.get("llm_model", ""),
            "llm_base_url": config.get("llm_base_url", "http://localhost:8081/v1"),
            "llm_api_key": config.get("llm_api_key", ""),
            "streaming": config.get("streaming", True),
        },
        "vision": {
            "vision_base_url": config.get("vision_base_url", "http://localhost:8082/v1"),
            "vision_model": config.get("vision_model", ""),
        },
        "tts": {
            "tts_engine": config.get("tts_engine", "kokoro"),
            "tts_url": config.get("tts_url", "http://localhost:8085/v1/audio/speech"),
            "tts_model": config.get("tts_model", ""),
            "tts_voice": config.get("tts_voice", "am_adam"),
        },
        "stt": {
            "stt_engine": config.get("stt_engine", "whisper_http"),
            "stt_url": config.get("stt_url", "http://localhost:8084/v1/audio/transcriptions"),
        },
        "keys": {
            "key_toggle": config.get("key_toggle", "f13"),
            "key_voice": config.get("key_voice", "f18"),
            "key_text": config.get("key_text", "f16"),
        },
        "wake": {
            "wake_word_enabled": config.get("wake_word_enabled", True),
            "wake_phrases": config.get("wake_phrases", []),
            "wake_energy": config.get("wake_energy", 200),
        },
        "auth": {
            "auth_enabled": config.get("auth_enabled", False),
            "auth_session_hours": config.get("auth_session_hours", 24),
            "dashboard_token": config.get("dashboard_token", ""),
        },
        "identity": {
            "agent_name": config.get("agent_name", "C"),
        },
    }
    # Mask sensitive fields before sending to the client
    for section in result.values():
        if isinstance(section, dict):
            for key in section:
                if key in _SENSITIVE_FIELDS:
                    section[key] = _mask_sensitive(section[key])
    return result


@app.put("/api/config")
async def update_config(request: Request):
    """Update config.json from Settings UI with input validation."""
    try:
        updates = await request.json()
        config = {}
        try:
            with open(CONFIG_PATH) as f:
                config = json.load(f)
        except Exception:
            pass

        # Flatten sections for validation and merge
        flat = {}
        for section_vals in updates.values():
            if isinstance(section_vals, dict):
                for k, v in section_vals.items():
                    flat[k] = v

        # Validate all incoming values
        errors = _validate_config_updates(flat)
        if errors:
            return JSONResponse({"error": "Validation failed", "details": errors}, status_code=422)

        # Merge validated values, skipping masked sensitive fields
        changed_keys = []
        for k, v in flat.items():
            # If a sensitive field is still masked, the user did not change it — skip
            if k in _SENSITIVE_FIELDS and isinstance(v, str) and v.startswith("*"):
                continue
            config[k] = v
            changed_keys.append(k)

        with open(CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=2)
        return {
            "saved": True,
            "message": f"Configuration saved successfully ({len(changed_keys)} field(s) updated).",
            "updated_fields": changed_keys,
        }
    except json.JSONDecodeError:
        return JSONResponse({"error": "Invalid JSON in request body"}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


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
    """Queue a command for CODEC to execute (used by heartbeat, scheduler, and PWA)."""
    body = await request.json()
    # Accept both 'command' (heartbeat/scheduler) and 'task' (PWA) keys
    task = (body.get("command") or body.get("task") or "").strip()
    if not task:
        return JSONResponse({"error": "No command provided"}, status_code=400)
    source = body.get("source", "api")

    queue_path = os.path.expanduser("~/.codec/task_queue.txt")
    entry = json.dumps({
        "task": task,
        "source": source,
        "timestamp": datetime.now().isoformat()
    }) + "\n"

    # Write to task queue file — CODEC's dispatch will pick it up
    try:
        with open(queue_path, "a") as f:
            f.write(entry)

        # Also write to legacy TASK_QUEUE for backward compat
        with open(TASK_QUEUE, "w") as f:
            json.dump({
                "task": task,
                "app": "CODEC Dashboard",
                "ts": datetime.now().isoformat(),
                "source": source
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
            f.write(f"[{datetime.now().isoformat()}] CMD[{source}]: {task[:200]}\n")

        log.info(f"[Command] Queued from {source}: {task[:80]}")
        return {"status": "queued", "command": task, "source": source}
    except Exception as e:
        log.error(f"[Command] Queue write failed: {e}")
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
        except Exception as e:
            log.warning(f"Non-critical error: {e}")
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
        resp_file = os.path.expanduser("~/.codec/pwa_response.json")
        if os.path.exists(resp_file):
            with open(resp_file) as f:
                data = json.load(f)
            os.unlink(resp_file)
            return data
        return {"response": None}
    except Exception as e:
        log.warning(f"Non-critical error: {e}")
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
        except Exception as e:
            log.warning(f"Non-critical error: {e}")
        tts_url = config.get("tts_url", "http://localhost:8085/v1/audio/speech")
        tts_model = config.get("tts_model", "mlx-community/Kokoro-82M-bf16")
        tts_voice = config.get("tts_voice", "am_adam")
        r = rq.post(tts_url, json={"model": tts_model, "input": text[:500], "voice": tts_voice, "speed": 1.1}, timeout=30)
        if r.status_code == 200:
            audio_path = os.path.expanduser("~/.codec/pwa_audio.mp3")
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
        path = os.path.expanduser("~/.codec/pwa_screenshot.png")
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
            pdf_path = os.path.expanduser("~/.codec/pwa_upload.pdf")
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
        return HTMLResponse(f.read(), headers=_NO_CACHE)


# C Chat conversation storage
QCHAT_DB = os.path.expanduser("~/.codec/qchat.db")

_qchat_conn = None

def qchat_db():
    global _qchat_conn
    if _qchat_conn is None:
        _qchat_conn = sqlite3.connect(QCHAT_DB, check_same_thread=False)
        _qchat_conn.execute("PRAGMA journal_mode=WAL")
        _qchat_conn.execute("PRAGMA busy_timeout=5000")
        _qchat_conn.execute('''CREATE TABLE IF NOT EXISTS qchat_sessions (
            id TEXT PRIMARY KEY, title TEXT, created_at TEXT, updated_at TEXT)''')
        _qchat_conn.execute('''CREATE TABLE IF NOT EXISTS qchat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,
            content TEXT, timestamp TEXT)''')
        _qchat_conn.commit()
    return _qchat_conn

@app.get("/api/qchat/sessions")
async def qchat_sessions():
    conn = qchat_db()
    rows = conn.execute("SELECT id, title, updated_at FROM qchat_sessions ORDER BY updated_at DESC LIMIT 30").fetchall()
    return [{"id": r[0], "title": r[1], "updated_at": r[2]} for r in rows]

@app.get("/api/qchat/session/{sid}")
async def qchat_session(sid: str):
    conn = qchat_db()
    rows = conn.execute("SELECT role, content, timestamp FROM qchat_messages WHERE session_id=? ORDER BY id ASC", (sid,)).fetchall()
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
        except Exception as e:
            log.warning(f"Non-critical error: {e}")
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

_vibe_conn = None

def vibe_db():
    global _vibe_conn
    if _vibe_conn is None:
        _vibe_conn = sqlite3.connect(VIBE_DB, check_same_thread=False)
        _vibe_conn.execute("PRAGMA journal_mode=WAL")
        _vibe_conn.execute("PRAGMA busy_timeout=5000")
        _vibe_conn.execute('''CREATE TABLE IF NOT EXISTS vibe_sessions (
            id TEXT PRIMARY KEY, title TEXT, language TEXT, code TEXT, created_at TEXT, updated_at TEXT)''')
        _vibe_conn.execute('''CREATE TABLE IF NOT EXISTS vibe_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,
            content TEXT, timestamp TEXT)''')
        _vibe_conn.commit()
    return _vibe_conn

@app.get("/api/vibe/sessions")
async def vibe_sessions():
    conn = vibe_db()
    rows = conn.execute("SELECT id, title, language, updated_at FROM vibe_sessions ORDER BY updated_at DESC LIMIT 30").fetchall()
    return [{"id": r[0], "title": r[1], "language": r[2], "updated_at": r[3]} for r in rows]

@app.get("/api/vibe/session/{sid}")
async def vibe_session(sid: str):
    conn = vibe_db()
    session = conn.execute("SELECT id, title, language, code FROM vibe_sessions WHERE id=?", (sid,)).fetchone()
    msgs = conn.execute("SELECT role, content, timestamp FROM vibe_messages WHERE session_id=? ORDER BY id ASC", (sid,)).fetchall()
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
    return {"ok": True}

@app.get("/vibe", response_class=HTMLResponse)
async def vibe_page():
    vibe_path = os.path.join(DASHBOARD_DIR, "codec_vibe.html")
    with open(vibe_path) as f:
        return HTMLResponse(f.read(), headers=_NO_CACHE)

@app.post("/api/preview")
async def preview_code(request: Request):
    body = await request.json()
    code = body.get("code", "")
    preview_path = os.path.expanduser("~/.codec/preview.html")
    with open(preview_path, "w") as f:
        f.write(code)
    return {"url": "/preview_frame", "path": preview_path}

@app.get("/preview_frame", response_class=HTMLResponse)
async def preview_frame():
    try:
        with open(os.path.expanduser("~/.codec/preview.html")) as f:
            content = f.read()
        # Restrict preview with CSP — no access to dashboard APIs or external resources
        return HTMLResponse(content, headers={
            "Content-Security-Policy": "default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:; connect-src 'none'; form-action 'none'",
            "X-Frame-Options": "SAMEORIGIN",
        })
    except Exception as e:
        log.warning(f"Non-critical error: {e}")
        return HTMLResponse("<html><body style='background:#0a0a0a;color:#888;padding:40px;font-family:sans-serif'><h2>No preview available</h2><p>Write some HTML and click Preview.</p></body></html>")

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
    ext_map = {"python": ".py", "javascript": ".js", "typescript": ".ts", "bash": ".sh", "go": ".go", "rust": ".rs", "java": ".java", "cpp": ".cpp", "swift": ".swift", "ruby": ".rb", "sql": ".sql"}
    ext = ext_map.get(language, ".txt")
    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False, mode="w")
    tmp.write(code); tmp.close()
    cmd_map = {
        "python": ["python3.13", tmp.name],
        "javascript": ["node", tmp.name],
        "typescript": ["npx", "ts-node", tmp.name],
        "bash": ["bash", tmp.name],
        "go": ["go", "run", tmp.name],
        "rust": ["rustc", tmp.name, "-o", tmp.name + ".out", "&&", tmp.name + ".out"],
        "swift": ["swift", tmp.name],
        "ruby": ["ruby", tmp.name],
    }
    cmd = cmd_map.get(language, ["python3.13", tmp.name])
    # For rust, compile+run in one shell command
    if language == "rust":
        cmd = ["bash", "-c", f"rustc {tmp.name} -o {tmp.name}.out 2>&1 && {tmp.name}.out"]
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
        except Exception as e:
            log.warning(f"Non-critical error: {e}")

@app.post("/api/save_file")
async def save_file(request: Request):
    body = await request.json()
    filename = os.path.basename(body.get("filename", "untitled.py"))
    content = body.get("content", "")
    ALLOWED_SAVE_DIRS = [
        os.path.expanduser("~/codec-workspace"),
        os.path.expanduser("~/.codec"),
        os.path.expanduser("~/Desktop"),
        os.path.expanduser("~/Documents"),
    ]
    directory = os.path.realpath(os.path.expanduser(body.get("directory", "~/codec-workspace")))
    if not any(directory.startswith(allowed) for allowed in ALLOWED_SAVE_DIRS):
        return JSONResponse({"error": "Directory not allowed"}, status_code=403)
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
    # Validate: must contain SKILL_DESCRIPTION and run function
    if "SKILL_DESCRIPTION" not in content or "def run(" not in content:
        return JSONResponse({"error": "Invalid skill: must contain SKILL_DESCRIPTION and def run()"}, status_code=400)
    # Block dangerous imports/calls in skill code
    BLOCKED_IN_SKILLS = ["os.system(", "subprocess.Popen(", "eval(", "exec(", "__import__"]
    for blocked in BLOCKED_IN_SKILLS:
        if blocked in content:
            return JSONResponse({"error": f"Blocked pattern in skill code: {blocked}"}, status_code=400)
    path = os.path.join(os.path.expanduser("~/.codec/skills"), filename)
    with open(path, "w") as f: f.write(content)
    return {"path": path, "skill": filename, "size": len(content)}

# In-memory pending skill reviews (human review gate)
_pending_skills: dict = {}

@app.post("/api/skill/review")
async def skill_review(request: Request):
    """Stage LLM-generated skill code for human review — does NOT write to disk."""
    import uuid
    body = await request.json()
    code = body.get("code", "")
    filename = os.path.basename(body.get("filename", "custom_skill.py"))
    if not filename.endswith(".py"):
        filename += ".py"
    if not code:
        return JSONResponse({"error": "No code provided"}, status_code=400)
    review_id = str(uuid.uuid4())[:12]
    _pending_skills[review_id] = {"code": code, "filename": filename}
    return {"review_id": review_id, "code": code, "filename": filename}

@app.post("/api/skill/approve")
async def skill_approve(request: Request):
    """Approve a pending skill review — writes to disk and removes from pending."""
    body = await request.json()
    review_id = body.get("review_id", "")
    if review_id not in _pending_skills:
        return JSONResponse({"error": "Review not found or already approved"}, status_code=404)
    pending = _pending_skills.pop(review_id)
    code = pending["code"]
    filename = pending["filename"]
    # Validate: must contain SKILL_DESCRIPTION and run function
    if "SKILL_DESCRIPTION" not in code or "def run(" not in code:
        return JSONResponse({"error": "Invalid skill: must contain SKILL_DESCRIPTION and def run()"}, status_code=400)
    # Block dangerous imports/calls in skill code
    BLOCKED_IN_SKILLS = ["os.system(", "subprocess.Popen(", "eval(", "exec(", "__import__"]
    for blocked in BLOCKED_IN_SKILLS:
        if blocked in code:
            return JSONResponse({"error": f"Blocked pattern in skill code: {blocked}"}, status_code=400)
    skill_dir = os.path.expanduser("~/.codec/skills")
    os.makedirs(skill_dir, exist_ok=True)
    path = os.path.join(skill_dir, filename)
    with open(path, "w") as f:
        f.write(code)
    return {"path": path, "skill": filename, "size": len(code)}

# In-memory job stores (survive for session lifetime)
_research_jobs: dict = {}
_agent_jobs: dict = {}

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


@app.post("/api/forge")
async def forge_skill(request: Request):
    """Convert arbitrary code (or a URL to code) into a CODEC skill using the LLM"""
    import re as _re
    body = await request.json()
    code = body.get("code", "").strip()
    if not code or len(code) < 4:
        return JSONResponse({"error": "No code provided"}, status_code=400)

    # Fix 2 — URL import: if code is a URL, fetch the source first
    source_url = None
    if code.startswith(("http://", "https://")):
        try:
            import requests as _rq_url
            resp = _rq_url.get(code, timeout=15, headers={"User-Agent": "CODEC-Forge/1.0"})
            if resp.status_code != 200:
                return JSONResponse({"error": f"URL fetch failed: {resp.status_code} {code}"}, status_code=400)
            source_url = code
            code = resp.text.strip()
            if not code:
                return JSONResponse({"error": "URL returned empty content"}, status_code=400)
        except Exception as e:
            return JSONResponse({"error": f"URL fetch error: {e}"}, status_code=400)

    cfg = {}
    try:
        with open(CONFIG_PATH) as f: cfg = json.load(f)
    except Exception as e:
        log.warning(f"Non-critical error: {e}")

    base_url = cfg.get("llm_base_url", "http://localhost:8081/v1")
    model = cfg.get("llm_model", "")
    api_key = cfg.get("llm_api_key", "")
    kwargs = {k: v for k, v in cfg.get("llm_kwargs", {}).items() if k != "enable_thinking"}

    headers = {"Content-Type": "application/json"}
    if api_key: headers["Authorization"] = "Bearer " + api_key

    url_note = f"\n(Fetched from: {source_url})" if source_url else ""

    # Fix 3 — Better prompt: explicitly forbid hallucination, anchor on actual code
    prompt = f"""Convert the following code into a CODEC skill Python file.

CRITICAL: Convert THIS EXACT CODE below. Do NOT invent a weather skill or any other unrelated skill.
Base the skill NAME, DESCRIPTION, TRIGGERS, and implementation ENTIRELY on the actual code provided.{url_note}

OUTPUT ONLY the Python file content — no markdown, no backticks, no explanation.

EXACT FORMAT REQUIRED:
\"\"\"CODEC Skill: [Name derived from the actual code]\"\"\"
SKILL_NAME = "[lowercase_name_matching_what_the_code_does]"
SKILL_DESCRIPTION = "[One line describing what THIS code actually does]"
SKILL_TRIGGERS = ["phrase 1", "phrase 2", "phrase 3", "phrase 4"]

import os, json  # only imports actually needed

def run(task, app="", ctx=""):
    # Wrap the actual code logic here
    return "result string"  # must return a string

RULES:
- SKILL_NAME: lowercase, underscores only — name it after what the code ACTUALLY does
- SKILL_TRIGGERS: natural phrases a user would say to run THIS specific skill
- run() must always return a string
- Preserve the core logic of the original code
- Add error handling around external calls

CODE TO CONVERT:
{code}"""

    try:
        import requests as rq_forge
        payload = {"model": model, "messages": [{"role": "user", "content": prompt}],
                   "max_tokens": 1500, "temperature": 0.1}
        payload.update(kwargs)
        r = rq_forge.post(base_url + "/chat/completions", json=payload, headers=headers, timeout=90)
        if r.status_code != 200:
            return JSONResponse({"error": f"LLM returned {r.status_code}"}, status_code=502)

        raw = r.json()["choices"][0]["message"].get("content", "").strip()
        raw = _re.sub(r'<think>[\s\S]*?</think>', '', raw).strip()
        raw = _re.sub(r'^```[\w]*\n?', '', raw).strip()
        raw = _re.sub(r'\n?```$', '', raw).strip()

        # Fix 1 — Title line: if first line isn't valid Python, wrap it as a docstring
        lines = raw.split('\n')
        if lines:
            first = lines[0].strip()
            valid_starts = ('"""', "'''", 'import ', 'from ', 'SKILL_', '#', 'def ', 'class ', '@')
            if first and not any(first.startswith(s) for s in valid_starts):
                lines[0] = '"""' + first + '"""'
                raw = '\n'.join(lines)

        if "SKILL_NAME" not in raw or "def run" not in raw:
            return JSONResponse({"error": "LLM output is not a valid skill", "raw": raw}, status_code=422)

        name_match = _re.search(r'SKILL_NAME\s*=\s*["\'](\w+)["\']', raw)
        skill_name = name_match.group(1) if name_match else "forged_skill"

        # Syntax check
        try:
            compile(raw, f"{skill_name}.py", "exec")
        except SyntaxError as e:
            return JSONResponse({"error": f"Syntax error in generated skill: {e}", "raw": raw}, status_code=422)

        # Save to ~/.codec/skills/
        skills_dir = os.path.expanduser("~/.codec/skills")
        os.makedirs(skills_dir, exist_ok=True)
        filepath = os.path.join(skills_dir, f"{skill_name}.py")
        with open(filepath, "w") as f: f.write(raw)

        # Mirror to repo skills/ if it exists
        repo_skills = os.path.join(DASHBOARD_DIR, "skills")
        if os.path.isdir(repo_skills):
            with open(os.path.join(repo_skills, f"{skill_name}.py"), "w") as f: f.write(raw)

        msg = f"Skill '{skill_name}' forged!"
        if source_url:
            msg += f" (imported from URL)"
        msg += " Run: pm2 restart ava-autopilot"
        return {"skill_name": skill_name, "path": filepath, "code": raw,
                "source_url": source_url, "message": msg}

    except Exception as e:
        import traceback; traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


def _fetch_url_content(url: str, max_chars: int = 8000) -> str:
    """Fetch a URL and return stripped text content."""
    try:
        import httpx
        from html.parser import HTMLParser

        class _Stripper(HTMLParser):
            def __init__(self):
                super().__init__()
                self._skip = False
                self.chunks = []
            def handle_starttag(self, tag, attrs):
                if tag in ('script', 'style', 'nav', 'footer'):
                    self._skip = True
            def handle_endtag(self, tag):
                if tag in ('script', 'style', 'nav', 'footer'):
                    self._skip = False
            def handle_data(self, data):
                if not self._skip:
                    stripped = data.strip()
                    if stripped:
                        self.chunks.append(stripped)

        r = httpx.get(url, timeout=15, follow_redirects=True,
                       headers={"User-Agent": "Mozilla/5.0 (compatible; CODEC/1.0)"})
        if 'text/html' in r.headers.get('content-type', ''):
            parser = _Stripper()
            parser.feed(r.text)
            text = ' '.join(parser.chunks)
        else:
            text = r.text
        return text[:max_chars]
    except Exception as e:
        log.warning(f"URL fetch failed ({url}): {e}")
        return ""


def _enrich_messages(messages: list, config: dict, force_search: bool = False) -> list:
    """
    Auto-detect URLs and search intent in the last user message.
    Injects a context message before the last user message when content is found.
    force_search=True bypasses intent detection and always searches.
    Returns a (possibly modified) copy of the messages list.
    """
    import re as _re
    if not messages:
        return messages

    # Find last user message
    last_user_idx = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            last_user_idx = i
            break
    if last_user_idx is None:
        return messages

    last_text = messages[last_user_idx].get("content", "")
    if not isinstance(last_text, str):
        return messages

    context_parts = []

    # ── URL detection ──────────────────────────────────────────────────────────
    urls = _re.findall(r'https?://[^\s\)\]>,"\']+', last_text)
    for url in urls[:3]:  # cap at 3 URLs per message
        content = _fetch_url_content(url)
        if content:
            context_parts.append(f"[URL CONTENT: {url}]\n{content}\n[END URL CONTENT]")
            log.info(f"Chat URL fetched: {url} ({len(content)} chars)")

    # ── Search intent detection ────────────────────────────────────────────────
    search_triggers = [
        'search for', 'search the web', 'google', 'look up', 'find out',
        'what is the latest', 'current news', 'recent', 'today\'s', 'right now',
        'who won', 'stock price', 'weather in', 'news about'
    ]
    lower = last_text.lower()
    should_search = (any(t in lower for t in search_triggers) or force_search) and not urls
    if should_search:
        try:
            import sys, os as _os
            repo_dir = _os.path.dirname(_os.path.abspath(__file__))
            if repo_dir not in sys.path:
                sys.path.insert(0, repo_dir)
            from codec_search import search, format_results
            results = search(last_text, max_results=5)
            if results:
                context_parts.append(f"[WEB SEARCH RESULTS]\n{format_results(results, max_snippets=5)}\n[END WEB SEARCH RESULTS]")
                log.info(f"Chat search injected for: {last_text[:80]}")
        except Exception as e:
            log.warning(f"Chat search failed: {e}")

    if not context_parts:
        return messages

    # Inject context as an assistant message just before the last user message
    context_msg = {"role": "assistant", "content": "\n\n".join(context_parts)}
    enriched = list(messages)
    enriched.insert(last_user_idx, context_msg)
    return enriched


@app.post("/api/web_search")
async def web_search_endpoint(request: Request):
    """Standalone web search endpoint for the chat UI."""
    body = await request.json()
    query = body.get("query", "").strip()
    if not query:
        return JSONResponse({"error": "query required"}, status_code=400)
    try:
        import sys, os as _os
        repo_dir = _os.path.dirname(_os.path.abspath(__file__))
        if repo_dir not in sys.path:
            sys.path.insert(0, repo_dir)
        from codec_search import search, format_results
        results = search(query, max_results=8)
        return {"results": results, "formatted": format_results(results, max_snippets=8)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


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
        except Exception as e:
            log.warning(f"Non-critical error: {e}")
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
        except Exception as e:
            log.warning(f"Non-critical error: {e}")
        base_url = config.get("llm_base_url", "http://localhost:8081/v1")
        model = config.get("llm_model", "mlx-community/Qwen3.5-35B-A3B-4bit")
        api_key = config.get("llm_api_key", "")
        kwargs = config.get("llm_kwargs", {})
        headers = {"Content-Type": "application/json"}
        if api_key: headers["Authorization"] = f"Bearer {api_key}"
        force_search = body.get("force_search", False)
        messages = _enrich_messages(messages, config, force_search=bool(force_search))
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

# ── CODEC Voice ──────────────────────────────────────────────────────────────

@app.get("/voice", response_class=HTMLResponse)
async def voice_page():
    """Serve the voice call UI."""
    voice_path = os.path.join(DASHBOARD_DIR, "codec_voice.html")
    with open(voice_path) as f:
        return HTMLResponse(f.read(), headers=_NO_CACHE)

@app.websocket("/ws/voice")
async def voice_websocket(websocket: WebSocket):
    """WebSocket endpoint — one VoicePipeline per connection."""
    await websocket.accept()
    print("[Voice] WebSocket connected")
    from codec_voice import VoicePipeline
    pipeline = VoicePipeline(websocket)
    try:
        await pipeline.run()
    except WebSocketDisconnect:
        print("[Voice] WebSocket disconnected cleanly")
    except Exception as e:
        print(f"[Voice] WebSocket error: {e}")
    finally:
        pipeline.save_to_memory()
        await pipeline.close()

# ─────────────────────────────────────────────────────────────────────────────

# ── CODEC Agents ─────────────────────────────────────────────────────────────

@app.get("/api/agents/crews")
async def list_agent_crews():
    """List available agent crews."""
    from codec_agents import list_crews
    return {"crews": list_crews()}


@app.post("/api/agents/run")
async def run_agent_crew(request: Request):
    """Start an agent crew in background — returns job_id immediately to avoid proxy timeouts."""
    import uuid, threading
    body = await request.json()
    crew_name = body.pop("crew", "")
    if not crew_name:
        return JSONResponse({"error": "Missing 'crew' field"}, status_code=400)

    job_id = str(uuid.uuid4())[:8]
    _agent_jobs[job_id] = {
        "status": "running",
        "crew": crew_name,
        "progress": [],
        "started": datetime.now().isoformat(),
    }

    def _run():
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        progress_log = _agent_jobs[job_id]["progress"]

        def on_progress(update):
            progress_log.append(update)
            print(f"[Agents] {update}")

        try:
            if crew_name == "custom":
                from codec_agents import run_custom_agent
                result = loop.run_until_complete(run_custom_agent(
                    name           = body.get("agent_name", "Custom"),
                    role           = body.get("role", ""),
                    tools          = body.get("tools", []),
                    max_iterations = int(body.get("max_iterations", 8)),
                    task           = body.get("task", ""),
                    callback       = on_progress,
                ))
            else:
                from codec_agents import run_crew
                result = loop.run_until_complete(run_crew(crew_name, callback=on_progress, **body))
            _agent_jobs[job_id].update(result)
            _agent_jobs[job_id]["status"] = result.get("status", "complete")
            _agent_jobs[job_id]["progress"] = progress_log
        except Exception as e:
            import traceback; traceback.print_exc()
            _agent_jobs[job_id]["status"] = "error"
            _agent_jobs[job_id]["error"] = str(e)
        finally:
            loop.close()

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id, "status": "running", "crew": crew_name}


@app.get("/api/agents/status/{job_id}")
async def agent_job_status(job_id: str):
    """Poll agent job status. Returns full result when status != 'running'."""
    job = _agent_jobs.get(job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    return job


_AGENTS_DIR = os.path.expanduser("~/.codec/agents")
os.makedirs(_AGENTS_DIR, exist_ok=True)


@app.get("/api/agents/tools")
async def list_agent_tools():
    """Return all available tool names + descriptions for the custom agent builder."""
    from codec_agents import get_all_tools
    tools = get_all_tools()
    return {"tools": [{"name": t.name, "description": t.description} for t in tools]}


@app.post("/api/agents/custom/save")
async def save_custom_agent(request: Request):
    """Save a custom agent definition to ~/.codec/agents/"""
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "Name required"}, status_code=400)
    safe_id = re.sub(r"[^\w\-]", "_", name.lower())
    path = os.path.join(_AGENTS_DIR, safe_id + ".json")
    with open(path, "w") as f:
        json.dump({**body, "id": safe_id}, f, indent=2)
    return {"saved": True, "id": safe_id, "path": path}


@app.get("/api/agents/custom/list")
async def list_custom_agents():
    """List saved custom agent definitions."""
    agents = []
    for f in sorted(os.listdir(_AGENTS_DIR)):
        if f.endswith(".json"):
            try:
                with open(os.path.join(_AGENTS_DIR, f)) as fh:
                    agents.append(json.load(fh))
            except Exception:
                pass
    return {"agents": agents}


@app.get("/api/schedules")
async def list_schedules_api():
    """List all scheduled agent runs."""
    try:
        from codec_scheduler import load_schedules
        return {"schedules": load_schedules()}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/schedules")
async def add_schedule_api(request: Request):
    """Add a new scheduled agent run."""
    body = await request.json()
    required = ["crew"]
    for field in required:
        if field not in body:
            return JSONResponse({"error": f"Missing field: {field}"}, status_code=400)
    try:
        from codec_scheduler import add_schedule
        s = add_schedule(
            body["crew"],
            topic=body.get("topic", ""),
            cron_hour=body.get("hour", 8),
            cron_minute=body.get("minute", 0),
            days=body.get("days"),
        )
        return {"schedule": s}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/api/schedules/{sched_id}")
async def delete_schedule_api(sched_id: str):
    """Remove a schedule by ID."""
    try:
        from codec_scheduler import remove_schedule
        removed = remove_schedule(sched_id)
        return {"removed": removed, "id": sched_id}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.put("/api/schedules/{sched_id}")
async def update_schedule(sched_id: str, request: Request):
    """Update an existing schedule by ID."""
    body = await request.json()
    sched_path = os.path.join(os.path.expanduser("~"), ".codec", "schedules.json")
    schedules = []
    try:
        with open(sched_path) as f:
            schedules = json.load(f)
    except Exception:
        pass
    for s in schedules:
        if s.get("id") == sched_id:
            s.update({k: v for k, v in body.items() if k != "id"})
            with open(sched_path, "w") as f:
                json.dump(schedules, f, indent=2)
            return {"schedule": s}
    return JSONResponse({"error": "Not found"}, status_code=404)


@app.post("/api/schedules/{sched_id}/run")
async def run_schedule_now(sched_id: str):
    """Manually trigger a scheduled task."""
    sched_path = os.path.join(os.path.expanduser("~"), ".codec", "schedules.json")
    schedules = []
    try:
        with open(sched_path) as f:
            schedules = json.load(f)
    except Exception:
        return JSONResponse({"error": "No schedules found"}, status_code=404)
    for s in schedules:
        if s.get("id") == sched_id:
            return {"status": "triggered", "id": sched_id, "crew": s.get("crew", "unknown")}
    return JSONResponse({"error": "Not found"}, status_code=404)


@app.get("/api/heartbeat/config")
async def get_heartbeat_config():
    """Get heartbeat configuration."""
    config = {}
    try:
        with open(CONFIG_PATH) as f:
            config = json.load(f)
    except Exception:
        pass
    return {
        "enabled": config.get("heartbeat_enabled", True),
        "interval_minutes": config.get("heartbeat_interval", 5),
        "tasks": config.get("heartbeat_tasks", ["status_check"])
    }


@app.put("/api/heartbeat/config")
async def update_heartbeat_config(request: Request):
    """Update heartbeat configuration with validation."""
    body = await request.json()
    # Validate
    errors = []
    if "enabled" in body and not isinstance(body["enabled"], bool):
        errors.append("enabled must be a boolean")
    if "interval_minutes" in body:
        iv = body["interval_minutes"]
        if not isinstance(iv, (int, float)):
            errors.append("interval_minutes must be a number")
        elif iv <= 0:
            errors.append("interval_minutes must be positive")
    if "tasks" in body and not isinstance(body["tasks"], list):
        errors.append("tasks must be a list")
    if errors:
        return JSONResponse({"error": "Validation failed", "details": errors}, status_code=422)

    config = {}
    try:
        with open(CONFIG_PATH) as f:
            config = json.load(f)
    except Exception:
        pass
    changed = []
    if "enabled" in body:
        config["heartbeat_enabled"] = body["enabled"]
        changed.append("heartbeat_enabled")
    if "interval_minutes" in body:
        config["heartbeat_interval"] = body["interval_minutes"]
        changed.append("heartbeat_interval")
    if "tasks" in body:
        config["heartbeat_tasks"] = body["tasks"]
        changed.append("heartbeat_tasks")
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)
    return {
        "saved": True,
        "message": f"Heartbeat config saved ({len(changed)} field(s) updated).",
        "updated_fields": changed,
    }


@app.get("/api/schedules/history")
async def schedule_history():
    """Return last 50 schedule run log entries."""
    log_path = os.path.join(os.path.expanduser("~"), ".codec", "schedule_runs.log")
    entries = []
    try:
        with open(log_path) as f:
            for line in f.readlines()[-50:]:
                entries.append({"line": line.strip()})
    except Exception:
        pass
    return entries


@app.get("/tasks", response_class=HTMLResponse)
async def tasks_page():
    """Serve the Tasks/Schedule management page."""
    html_path = os.path.join(DASHBOARD_DIR, "codec_tasks.html")
    if os.path.exists(html_path):
        with open(html_path) as f:
            return HTMLResponse(f.read(), headers=_NO_CACHE)
    return HTMLResponse("<h1>Tasks page not found</h1>", status_code=500)


# ─────────────────────────────────────────────────────────────────────────────

# ── CODEC Memory ─────────────────────────────────────────────────────────────

from codec_memory import CodecMemory as _CM, _sanitize_fts_query
_memory = _CM()

@app.get("/api/memory/search")
async def memory_search(q: str = "", limit: int = 10):
    """Full-text search over all conversations (FTS5 BM25 ranked)."""
    sanitized = _sanitize_fts_query(q)
    if not sanitized:
        return JSONResponse({"error": "Query required"}, status_code=400)
    return _memory.search(sanitized, limit=limit)

@app.get("/api/memory/recent")
async def memory_recent(days: int = 7, limit: int = 50):
    """Return messages from the past N days."""
    return _memory.search_recent(days=days, limit=limit)

@app.get("/api/memory/sessions")
async def memory_sessions(limit: int = 20):
    """Return distinct sessions with message count and preview."""
    return _memory.get_sessions(limit=limit)

@app.post("/api/memory/rebuild")
async def memory_rebuild():
    """Rebuild FTS index from scratch (use after bulk imports)."""
    n = _memory.rebuild_fts()
    return {"indexed": n}

# ─────────────────────────────────────────────────────────────────────────────

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
                except Exception as e:
                    log.warning(f"Non-critical error: {e}")
                result.append({"name": name, "triggers": triggers})
    except Exception as e:
        log.warning(f"Non-critical error: {e}")
    return result

@app.get("/api/cdp/status")
async def cdp_status():
    """Check if Chrome is running with CDP enabled."""
    try:
        import httpx as _httpx
        r = _httpx.get("http://localhost:9222/json", timeout=2)
        tabs = r.json()
        page_tabs = [t for t in tabs if t.get("type") == "page"]
        return {
            "connected": True,
            "total_tabs": len(tabs),
            "page_tabs": len(page_tabs),
            "tabs": [{"title": t.get("title", "")[:60], "url": t.get("url", "")[:80]}
                     for t in page_tabs[:5]]
        }
    except Exception:
        return {"connected": False, "total_tabs": 0, "page_tabs": 0, "tabs": []}

@app.on_event("shutdown")
async def _close_db_connections():
    """Close all reusable SQLite connections on server shutdown."""
    global _db_conn, _qchat_conn, _vibe_conn
    for conn in (_db_conn, _qchat_conn, _vibe_conn):
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    _db_conn = _qchat_conn = _vibe_conn = None

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8090)
