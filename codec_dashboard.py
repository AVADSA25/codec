"""CODEC v1.2 — Phone Dashboard & PWA"""
import os, json, sqlite3, time, logging, secrets, subprocess, hmac, threading, uuid, asyncio, re
from datetime import datetime, timedelta

from pathlib import Path
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse as StarletteJSONResponse
import uvicorn

# ── Shared state (canonical source: routes/_shared.py) ──
from routes._shared import (
    log, DASHBOARD_DIR, CONFIG_PATH, TASK_QUEUE, DB_PATH,
    AUDIT_LOG, NOTIFICATIONS_PATH, SCHEDULE_RUNS_LOG,
    _NO_CACHE, _get_skills_dir, _audit_write,
    _notif_lock, _load_notifications, _write_notifications,
    _save_notification, _append_schedule_run_log,
    AUTH_ENABLED, AUTH_SESSION_HOURS, AUTH_BINARY, AUTH_PIN_HASH, AUTH_COOKIE_NAME,
    _auth_sessions, _auth_lock, _e2e_keys,
    _is_auth_compiled, _auth_available, _is_totp_enabled, _verify_biometric_session,
    _save_sessions, _save_e2e_keys,
    get_db,
)

from pydantic import BaseModel, Field
from typing import Optional, List


# ── Pydantic Response Models ───────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str = Field(description="Service status", example="ok")
    service: str = Field(description="Service name", example="CODEC Dashboard")
    timestamp: str = Field(description="ISO 8601 timestamp")

class StatusResponse(BaseModel):
    running: bool = Field(description="Whether CODEC main process is running")
    pm2_status: Optional[str] = Field(None, description="PM2 process status")

class SkillItem(BaseModel):
    name: str = Field(description="Skill identifier")
    description: str = Field(description="Human-readable description")
    triggers: List[str] = Field(description="Trigger phrases")

class ConversationItem(BaseModel):
    id: int
    session_id: str
    timestamp: str
    role: str = Field(description="'user' or 'assistant'")
    content: str

class ScheduleItem(BaseModel):
    id: str = Field(description="Unique schedule ID")
    name: str = Field(description="Schedule name")
    cron: str = Field(description="Cron expression")
    command: str = Field(description="Command to execute")
    enabled: bool = Field(default=True)

class ServiceStatus(BaseModel):
    running: bool
    last_tick: Optional[str] = None
    errors: int = 0

class CommandRequest(BaseModel):
    command: str = Field(description="Command text to execute")
    source: str = Field(default="api", description="Request source identifier")

class ChatRequest(BaseModel):
    message: str = Field(default="", description="User message text")
    messages: Optional[list] = Field(None, description="Full conversation history")
    session_id: Optional[str] = Field(None, description="Session ID for context")

class AgentRunRequest(BaseModel):
    crew: str = Field(description="Crew name from registry")
    task: str = Field(default="", description="Task description")
    context: Optional[str] = Field(None, description="Additional context")

class ErrorResponse(BaseModel):
    error: str = Field(description="Error message")


app = FastAPI(
    title="CODEC Dashboard",
    description="CODEC voice-controlled computer agent — dashboard API. "
                "Full documentation at /docs. Auth via Bearer token or biometric session.",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:8090", "http://127.0.0.1:8090", "https://codec.lucyvpa.com"], allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"], allow_headers=["*"])

# ── Background Services (replaces PM2 daemons for scheduler, heartbeat, watcher) ──
_bg_tasks: dict = {}
_bg_status: dict = {
    "scheduler": {"running": False, "last_tick": None, "errors": 0},
    "heartbeat": {"running": False, "last_tick": None, "errors": 0},
    "watcher":   {"running": False, "last_tick": None, "errors": 0},
}


class AuthMiddleware(BaseHTTPMiddleware):
    """Combined auth: bearer token (API) + biometric Touch ID sessions (dashboard)."""

    # Routes that never require authentication
    PUBLIC_ROUTES = {"/", "/chat", "/vibe", "/voice", "/auth", "/health", "/api/health", "/metrics", "/favicon.ico", "/manifest.json", "/docs", "/redoc", "/openapi.json"}
    PUBLIC_PREFIXES = ("/api/auth/", "/static")
    # CSRF-exempt paths (auth endpoints handle their own protection)
    CSRF_EXEMPT = {"/api/auth/verify", "/api/auth/pin", "/api/auth/logout",
                    "/api/auth/totp/setup", "/api/auth/totp/confirm", "/api/auth/totp/verify",
                    "/api/auth/totp/enable", "/api/auth/keyexchange"}

    async def dispatch(self, request, call_next):
        from codec_config import DASHBOARD_TOKEN
        from codec_metrics import metrics
        path = request.url.path
        metrics.inc("codec_http_requests_total", {"method": request.method, "path": path})

        # Always allow public routes
        if path in self.PUBLIC_ROUTES:
            return await call_next(request)
        if any(path.startswith(p) for p in self.PUBLIC_PREFIXES):
            return await call_next(request)
        # Allow internal localhost requests (scheduler, heartbeat, MCP)
        client_ip = request.client.host if request.client else ""
        if client_ip in ("127.0.0.1", "::1", "localhost") and request.headers.get("x-internal") == "codec":
            return await call_next(request)
        # Allow static assets
        if path.endswith(('.css', '.js', '.png', '.ico', '.svg', '.woff2', '.woff', '.ttf')):
            return await call_next(request)

        # ── CSRF check for state-changing requests ──
        # Only enforce CSRF if the user has a valid auth session (avoids blocking expired sessions
        # with stale CSRF cookies — let them fall through to the 401 auth check instead)
        if request.method in ("POST", "PUT", "DELETE") and path not in self.CSRF_EXEMPT:
            csrf_cookie = request.cookies.get("codec_csrf", "")
            csrf_header = request.headers.get("x-csrf-token", "")
            session_cookie = request.cookies.get(AUTH_COOKIE_NAME, "")
            if (DASHBOARD_TOKEN or AUTH_ENABLED) and csrf_cookie and session_cookie:
                if not csrf_header or not hmac.compare_digest(csrf_cookie, csrf_header):
                    return StarletteJSONResponse(
                        {"error": "CSRF token mismatch. Refresh the page."},
                        status_code=403
                    )

        # ── Layer 0: No auth configured → allow all ──
        if not DASHBOARD_TOKEN and (not AUTH_ENABLED or not _auth_available()):
            return await call_next(request)

        # ── Layer 1: Token check (API key — works as standalone auth for API) ──
        if DASHBOARD_TOKEN and path.startswith("/api/"):
            auth = request.headers.get("Authorization", "")
            if auth and hmac.compare_digest(auth, f"Bearer {DASHBOARD_TOKEN}"):
                return await call_next(request)

        # ── Layer 2: Biometric / PIN session check ──
        if AUTH_ENABLED and _auth_available():
            if _verify_biometric_session(request):
                return await call_next(request)
            # Fallback: accept session token as ?s= query param (for img/stream URLs on mobile)
            qs_token = request.query_params.get("s", "")
            if qs_token and request.method == "GET":
                with _auth_lock:
                    if qs_token in _auth_sessions:
                        session = _auth_sessions[qs_token]
                        if datetime.now() - session["created"] <= timedelta(hours=AUTH_SESSION_HOURS):
                            return await call_next(request)
            # Biometric failed — reject
            cookie_val = request.cookies.get(AUTH_COOKIE_NAME, "<missing>")
            log.warning("AUTH REJECTED: path=%s method=%s ip=%s cookie=%s...",
                        path, request.method, request.client.host if request.client else "?",
                        cookie_val[:12] if cookie_val else "<empty>")
            if path.startswith("/api/") or path.startswith("/ws"):
                return StarletteJSONResponse({"error": "Not authenticated"}, status_code=401)
            from starlette.responses import RedirectResponse
            return RedirectResponse(url="/auth")

        # ── Layer 3: No biometric available — token-only mode ──
        if DASHBOARD_TOKEN and path.startswith("/api/"):
            # Token was already checked above and didn't match
            return StarletteJSONResponse(
                {"error": "Unauthorized. Set dashboard_token in config.json and use the Authorization header."},
                status_code=401
            )

        return await call_next(request)


class CSPMiddleware(BaseHTTPMiddleware):
    """Add Content-Security-Policy header to all HTML responses."""

    CSP = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
        "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
        "img-src 'self' data: https:; "
        "connect-src 'self' ws: wss: http://localhost:* http://127.0.0.1:*; "
        "worker-src 'self' blob:"
    )

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        content_type = response.headers.get("content-type", "")
        if "text/html" in content_type:
            response.headers["Content-Security-Policy"] = self.CSP
        return response


app.add_middleware(CSPMiddleware)
app.add_middleware(AuthMiddleware)


# (Shared state: DB_PATH, AUDIT_LOG, CONFIG_PATH, etc. imported from routes._shared)


# (Auth helpers, DB, notifications loaded from routes._shared)


# (Auth endpoints moved to routes/auth.py)

# ═══════════════════════════════════════════════════════════════
# E2E ENCRYPTION — AES-256-GCM middleware (key exchange in routes/auth.py)
# ═══════════════════════════════════════════════════════════════


class E2EMiddleware(BaseHTTPMiddleware):
    """Transparent AES-256-GCM encryption/decryption for requests with X-E2E: 1 header."""

    async def dispatch(self, request, call_next):
        if request.headers.get("x-e2e") != "1":
            return await call_next(request)
        token = request.cookies.get(AUTH_COOKIE_NAME, "")
        aes_key = _e2e_keys.get(token) if token else None
        if not aes_key:
            # E2E header present but server lost key (e.g. after restart) — tell client to re-negotiate
            if request.method in ("POST", "PUT", "DELETE"):
                log.warning(f"[E2E] Key missing for session, requesting re-negotiation")
                return StarletteJSONResponse(
                    {"error": "E2E key expired. Refreshing encryption.", "e2e_renew": True},
                    status_code=428,  # Precondition Required
                    headers={"x-e2e-renew": "1"}
                )
            return await call_next(request)
        try:
            import base64
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError:
            return await call_next(request)
        # Decrypt request body if present
        if request.method in ("POST", "PUT", "DELETE"):
            raw = await request.body()
            if raw:
                try:
                    envelope = json.loads(raw)
                    if "iv" in envelope and "ct" in envelope:
                        iv = base64.b64decode(envelope["iv"])
                        ct = base64.b64decode(envelope["ct"])
                        plaintext = AESGCM(aes_key).decrypt(iv, ct, None)
                        # Replace request body with decrypted content
                        request._body = plaintext
                except Exception:
                    pass  # Not E2E-encrypted or malformed — pass through
        response = await call_next(request)
        # Encrypt response body
        if response.headers.get("content-type", "").startswith("application/json"):
            body_parts = []
            async for chunk in response.body_iterator:
                body_parts.append(chunk if isinstance(chunk, bytes) else chunk.encode())
            resp_body = b"".join(body_parts)
            iv = os.urandom(12)
            ct = AESGCM(aes_key).encrypt(iv, resp_body, None)
            enc = json.dumps({"iv": base64.b64encode(iv).decode(), "ct": base64.b64encode(ct).decode()})
            return StarletteJSONResponse(
                content=json.loads(enc),
                status_code=response.status_code,
                headers={"x-e2e": "1"}
            )
        return response

app.add_middleware(E2EMiddleware)


# ═══════════════════════════════════════════════════════════════
# ROUTE MODULES
# ═══════════════════════════════════════════════════════════════

from routes.auth import router as auth_router
from routes.skills import router as skills_router
from routes.agents import router as agents_router
from routes.memory import router as memory_router
from routes.websocket import router as websocket_router

app.include_router(auth_router)
app.include_router(skills_router)
app.include_router(agents_router)
app.include_router(memory_router)
app.include_router(websocket_router)


# ═══════════════════════════════════════════════════════════════
# DASHBOARD ROUTES (remaining in codec_dashboard.py)
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
        "description": "CODEC — Your Open-Source Intelligent Command Layer",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0a0a0a",
        "theme_color": "#E8711A",
        "icons": [
            {"src": "https://i.imgur.com/RbrQ7Bt.png", "sizes": "280x280", "type": "image/png"}
        ]
    })

@app.get("/metrics")
async def prometheus_metrics():
    from starlette.responses import PlainTextResponse
    from codec_metrics import metrics
    return PlainTextResponse(metrics.render(), media_type="text/plain; version=0.0.4")

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
    limit = min(limit, 500)
    try:
        c = get_db()
        rows = c.execute(
            "SELECT id, timestamp, task, app, response FROM sessions ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [{"id": r[0], "timestamp": r[1], "task": r[2], "app": r[3], "response": r[4]} for r in rows]
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/conversations")
async def conversations(limit: int = 100):
    """Get recent conversations"""
    limit = min(limit, 500)
    try:
        c = get_db()
        rows = c.execute(
            "SELECT id, session_id, timestamp, role, content FROM conversations ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [{"id": r[0], "session_id": r[1], "timestamp": r[2], "role": r[3], "content": r[4]} for r in rows]
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/audit")
async def audit(limit: int = 50):
    """Get recent audit log entries"""
    limit = min(limit, 500)
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
    source = body.get("source", "pwa")

    # ── Safety: reject dangerous commands before queueing ──
    from codec_config import is_dangerous
    if is_dangerous(task):
        log.warning(f"[Command] BLOCKED dangerous command from {source}: {task[:80]}")
        _audit_write(f"[{datetime.now().isoformat()}] BLOCKED[{source}]: {task[:200]}\n")
        return JSONResponse(
            {"error": "Command blocked: matches a dangerous pattern. Use the terminal directly for system commands."},
            status_code=403
        )

    # Write to task queue file — CODEC's pwa_poller will pick it up
    try:
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

        # Write audit
        _audit_write(f"[{datetime.now().isoformat()}] CMD[{source}]: {task[:200]}\n")

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
        _audit_write(f"[{datetime.now().isoformat()}] VISION: {prompt[:100]}\n")
        return {"response": answer, "model": vision_model}
    except Exception as e:
        import traceback; traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/response")
async def get_response():
    """Get latest PWA command response — returns no-cache headers to prevent stale polls."""
    headers = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"}
    try:
        resp_file = os.path.expanduser("~/.codec/pwa_response.json")
        if os.path.exists(resp_file):
            with open(resp_file) as f:
                data = json.load(f)
            os.unlink(resp_file)
            log.info(f"[Response] Delivered: {str(data.get('response',''))[:80]}")
            return JSONResponse(content=data, headers=headers)
        return JSONResponse(content={"response": None}, headers=headers)
    except Exception as e:
        log.warning(f"[Response] Error reading response file: {e}")
        return JSONResponse(content={"response": None}, headers=headers)

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

@app.post("/api/webcam")
async def webcam_capture(request: Request):
    """Save webcam photo and optionally analyze with vision model"""
    import base64
    body = await request.json()
    image_b64 = body.get("image", "")
    analyze = body.get("analyze", False)
    prompt = body.get("prompt", "Describe what you see in this webcam photo.")
    if not image_b64:
        return JSONResponse({"error": "No image data"}, status_code=400)
    try:
        # Save photo
        photo_dir = os.path.expanduser("~/.codec/photos")
        os.makedirs(photo_dir, exist_ok=True)
        filename = f"webcam_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        filepath = os.path.join(photo_dir, filename)
        with open(filepath, "wb") as f:
            f.write(base64.b64decode(image_b64))
        result = {"saved": filepath, "filename": filename}
        # Optional vision analysis
        if analyze:
            try:
                import requests as rq
                config = {}
                try:
                    with open(CONFIG_PATH) as f: config = json.load(f)
                except Exception:
                    pass
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
                r = rq.post(f"{vision_url}/chat/completions", json=payload,
                            headers={"Content-Type": "application/json"}, timeout=120)
                result["analysis"] = r.json()["choices"][0]["message"]["content"].strip()
                result["model"] = vision_model
            except Exception as e:
                result["analysis_error"] = str(e)
        _audit_write(f"[{datetime.now().isoformat()}] WEBCAM: {filename} analyze={analyze}\n")
        return result
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/webcam/stream")
async def webcam_stream():
    """MJPEG stream from the Mac's webcam — for remote viewing from phone/tablet."""
    import cv2
    from concurrent.futures import ThreadPoolExecutor
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        return JSONResponse({"error": "Cannot open webcam"}, status_code=500)
    _executor = ThreadPoolExecutor(max_workers=1)
    def _read_frame():
        ret, frame = cap.read()
        if not ret:
            return None
        _, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        return jpeg.tobytes()
    async def generate():
        loop = asyncio.get_event_loop()
        try:
            while True:
                data = await loop.run_in_executor(_executor, _read_frame)
                if data is None:
                    break
                yield (b"--frame\r\n"
                       b"Content-Type: image/jpeg\r\n\r\n" + data + b"\r\n")
                await asyncio.sleep(0.066)  # ~15 fps
        finally:
            cap.release()
            _executor.shutdown(wait=False)
    from starlette.responses import StreamingResponse
    return StreamingResponse(generate(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.get("/api/webcam/snapshot")
async def webcam_snapshot():
    """Capture a single frame from the Mac's webcam and return as JPEG."""
    import cv2, base64
    from concurrent.futures import ThreadPoolExecutor
    def _capture():
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            return None, None
        ret, frame = cap.read()
        cap.release()
        if not ret:
            return None, None
        _, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return jpeg.tobytes(), True
    loop = asyncio.get_event_loop()
    data, ok = await loop.run_in_executor(ThreadPoolExecutor(1), _capture)
    if not ok:
        return JSONResponse({"error": "Cannot capture from webcam"}, status_code=500)
    b64 = base64.b64encode(data).decode()
    # Save
    photo_dir = os.path.expanduser("~/.codec/photos")
    os.makedirs(photo_dir, exist_ok=True)
    filename = f"webcam_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    filepath = os.path.join(photo_dir, filename)
    with open(filepath, "wb") as f:
        f.write(data)
    return {"image": b64, "saved": filepath, "filename": filename}


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
    """Extract text from uploaded PDF, DOCX, CSV, or text files (up to 50MB)"""
    import base64, subprocess
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Request too large or malformed. Max file size: 50MB."}, status_code=413)
    filename = body.get("filename", "file")
    data = body.get("data", "")
    if not data:
        return JSONResponse({"error": "No data"}, status_code=400)
    try:
        raw = base64.b64decode(data)
        ext = os.path.splitext(filename)[1].lower()

        # ── PDF ──
        if ext == ".pdf":
            pdf_path = os.path.expanduser("~/.codec/pwa_upload.pdf")
            with open(pdf_path, "wb") as f: f.write(raw)
            r = subprocess.run(["pdftotext", "-layout", pdf_path, "-"],
                               capture_output=True, text=True, timeout=90)
            text_content = r.stdout[:300000].strip()
            if not text_content:
                return JSONResponse({"error": "Could not extract text from PDF (may be image-only)"}, status_code=422)
            return {"status": "ok", "text": text_content, "filename": filename}

        # ── DOCX ──
        if ext == ".docx":
            try:
                import zipfile, io, xml.etree.ElementTree as ET
                zf = zipfile.ZipFile(io.BytesIO(raw))
                xml_content = zf.read("word/document.xml")
                tree = ET.fromstring(xml_content)
                ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                paragraphs = []
                for p in tree.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
                    texts = [t.text for t in p.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t") if t.text]
                    if texts:
                        paragraphs.append("".join(texts))
                text_content = "\n".join(paragraphs)[:300000]
                return {"status": "ok", "text": text_content, "filename": filename}
            except Exception as e:
                return JSONResponse({"error": f"DOCX read error: {e}"}, status_code=422)

        # ── CSV / TSV ──
        if ext in (".csv", ".tsv"):
            text_content = raw.decode("utf-8", errors="replace")[:300000]
            return {"status": "ok", "text": text_content, "filename": filename}

        # ── Common text formats ──
        TEXT_EXTS = {".txt", ".md", ".json", ".xml", ".yaml", ".yml", ".html",
                     ".htm", ".css", ".js", ".ts", ".py", ".sh", ".log", ".sql",
                     ".toml", ".ini", ".cfg", ".env", ".rst", ".tex", ".rtf"}
        if ext in TEXT_EXTS:
            text_content = raw.decode("utf-8", errors="replace")[:300000]
            return {"status": "ok", "text": text_content, "filename": filename}

        # ── Fallback: try UTF-8 decode ──
        try:
            text_content = raw.decode("utf-8")[:300000]
            return {"status": "ok", "text": text_content, "filename": filename}
        except UnicodeDecodeError:
            return JSONResponse({"error": f"Cannot read .{ext.lstrip('.')} files — unsupported binary format"}, status_code=422)
    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "PDF too large or complex — processing timed out"}, status_code=408)
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
            id TEXT PRIMARY KEY, title TEXT, created_at TEXT, updated_at TEXT,
            user_id TEXT DEFAULT 'default')''')
        _qchat_conn.execute('''CREATE TABLE IF NOT EXISTS qchat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,
            content TEXT, timestamp TEXT, user_id TEXT DEFAULT 'default')''')
        # Migrate existing tables: add user_id if missing
        for table in ("qchat_sessions", "qchat_messages"):
            try:
                _qchat_conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id TEXT DEFAULT 'default'")
            except sqlite3.OperationalError:
                pass
        _qchat_conn.execute("CREATE INDEX IF NOT EXISTS idx_qchat_sessions_user ON qchat_sessions(user_id)")
        _qchat_conn.execute("CREATE INDEX IF NOT EXISTS idx_qchat_messages_user ON qchat_messages(user_id)")
        _qchat_conn.commit()
    return _qchat_conn

@app.get("/api/qchat/sessions")
async def qchat_sessions(user_id: str = None):
    conn = qchat_db()
    if user_id is not None:
        rows = conn.execute("SELECT id, title, updated_at FROM qchat_sessions WHERE user_id=? ORDER BY updated_at DESC LIMIT 30", (user_id,)).fetchall()
    else:
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
    user_id = body.get("user_id", "default")
    from datetime import datetime
    now = datetime.now().isoformat()
    conn = qchat_db()
    conn.execute("INSERT OR REPLACE INTO qchat_sessions (id, title, created_at, updated_at, user_id) VALUES (?, ?, COALESCE((SELECT created_at FROM qchat_sessions WHERE id=?), ?), ?, ?)",
        (sid, title[:60], sid, now, now, user_id))
    for m in messages:
        conn.execute("INSERT INTO qchat_messages (session_id, role, content, timestamp, user_id) VALUES (?, ?, ?, ?, ?)",
            (sid, m.get("role","user"), m.get("content",""), now, user_id))
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
            id TEXT PRIMARY KEY, title TEXT, language TEXT, code TEXT, created_at TEXT, updated_at TEXT,
            user_id TEXT DEFAULT 'default')''')
        _vibe_conn.execute('''CREATE TABLE IF NOT EXISTS vibe_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,
            content TEXT, timestamp TEXT, user_id TEXT DEFAULT 'default')''')
        # Migrate existing tables: add user_id if missing
        for table in ("vibe_sessions", "vibe_messages"):
            try:
                _vibe_conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id TEXT DEFAULT 'default'")
            except sqlite3.OperationalError:
                pass
        _vibe_conn.execute("CREATE INDEX IF NOT EXISTS idx_vibe_sessions_user ON vibe_sessions(user_id)")
        _vibe_conn.execute("CREATE INDEX IF NOT EXISTS idx_vibe_messages_user ON vibe_messages(user_id)")
        _vibe_conn.commit()
    return _vibe_conn

@app.get("/api/vibe/sessions")
async def vibe_sessions(user_id: str = None):
    conn = vibe_db()
    if user_id is not None:
        rows = conn.execute("SELECT id, title, language, updated_at FROM vibe_sessions WHERE user_id=? ORDER BY updated_at DESC LIMIT 30", (user_id,)).fetchall()
    else:
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
    user_id = body.get("user_id", "default")
    from datetime import datetime
    now = datetime.now().isoformat()
    full_sync = body.get("full_sync", False)
    conn = vibe_db()
    conn.execute("INSERT OR REPLACE INTO vibe_sessions (id, title, language, code, created_at, updated_at, user_id) VALUES (?, ?, ?, ?, COALESCE((SELECT created_at FROM vibe_sessions WHERE id=?), ?), ?, ?)",
        (sid, title[:60], language, code, sid, now, now, user_id))
    if full_sync and messages:
        conn.execute("DELETE FROM vibe_messages WHERE session_id=?", (sid,))
    for m in messages:
        conn.execute("INSERT INTO vibe_messages (session_id, role, content, timestamp, user_id) VALUES (?, ?, ?, ?, ?)",
            (sid, m.get("role","user"), m.get("content",""), now, user_id))
    conn.commit()
    return {"ok": True}

@app.delete("/api/vibe/session/{sid}")
async def vibe_delete(sid: str):
    conn = vibe_db()
    conn.execute("DELETE FROM vibe_messages WHERE session_id=?", (sid,))
    conn.execute("DELETE FROM vibe_sessions WHERE id=?", (sid,))
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
            "Content-Security-Policy": "default-src 'self' 'unsafe-inline' data: blob:; connect-src 'none'; form-action 'none'",
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
    from codec_config import is_dangerous
    if is_dangerous(code):
        return JSONResponse({"error": "Blocked: code contains dangerous pattern"}, status_code=403)
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


# (Skills endpoints moved to routes/skills.py)
# (Job stores _pending_skills, _research_jobs, _agent_jobs in routes/_shared.py)


# (Deep research endpoints moved to routes/agents.py)



# (Forge endpoint moved to routes/skills.py)


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
                       headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
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
    Auto-detect URLs, search intent, and memory recall in the last user message.
    Injects context messages before the last user message when content is found.
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
    memory_parts = []

    # ── Memory recall ──────────────────────────────────────────────────────────
    # Always inject relevant memory context so the LLM has cross-session recall
    try:
        from codec_memory import CodecMemory
        mem = CodecMemory()
        lower = last_text.lower()
        # Explicit memory triggers → targeted search
        memory_triggers = [
            'remember', 'recall', 'earlier', 'before', 'last time',
            'previously', 'we talked', 'we discussed', 'you said',
            'did i', 'did we', 'have i', 'have we', 'my previous',
            'past conversation', 'history', 'do you know my',
            'what was', 'what did', 'when did',
        ]
        has_memory_trigger = any(t in lower for t in memory_triggers)
        if has_memory_trigger:
            # Targeted FTS search for the user's query
            mem_context = mem.get_context(last_text, n=8)
            if mem_context:
                memory_parts.append(f"[MEMORY — RELEVANT PAST CONVERSATIONS]\n{mem_context}\n[END MEMORY]")
                log.info(f"Memory recall injected (targeted): {len(mem_context)} chars")
        # Always inject recent context (last 5 messages) for continuity
        recent = mem.search_recent(days=3, limit=5)
        if recent:
            lines = ["[RECENT MEMORY — LAST 3 DAYS]"]
            for r in recent:
                ts = r["timestamp"][:16].replace("T", " ")
                snippet = r["content"][:200].replace("\n", " ")
                lines.append(f"  [{ts}] {r['role'].upper()}: {snippet}")
            lines.append("[END RECENT MEMORY]")
            recent_ctx = "\n".join(lines)
            memory_parts.append(recent_ctx)
            log.info(f"Recent memory injected: {len(recent)} messages")
    except Exception as e:
        log.warning(f"Memory enrichment failed: {e}")

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

    if not context_parts and not memory_parts:
        return messages

    enriched = list(messages)

    # Inject memory + other context as a single user message (hidden context)
    # Using role "user" with clear [INTERNAL] framing so local LLMs don't choke on mid-conversation "system" role
    all_context = memory_parts + context_parts
    if all_context:
        prefix = ("(INTERNAL CONTEXT — do not echo this block. Use it to inform your answer naturally. "
                   "Never show raw [MEMORY] or [RECENT MEMORY] tags to the user.)\n\n")
        context_msg = {"role": "user", "content": prefix + "\n\n".join(all_context)}
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
    from codec_metrics import metrics
    metrics.inc("codec_chat_requests_total")
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
        stream_mode = body.get("stream", False)

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": 28000,
            "temperature": 0.7,
            "top_p": 0.9,
            "frequency_penalty": 1.1,
            "stream": stream_mode,
        }
        payload.update(kwargs)

        if stream_mode:
            # SSE streaming — keeps Cloudflare tunnel alive, sends tokens as they arrive
            import re as _re_stream
            def _stream_gen():
                try:
                    with rq.post(f"{base_url}/chat/completions", json=payload,
                                 headers=headers, timeout=300, stream=True) as resp:
                        for line in resp.iter_lines(decode_unicode=True):
                            if not line or not line.startswith("data: "):
                                continue
                            data_str = line[6:]
                            if data_str == "[DONE]":
                                yield "data: [DONE]\n\n"
                                break
                            try:
                                chunk = json.loads(data_str)
                                token = chunk["choices"][0].get("delta", {}).get("content", "")
                                if token:
                                    # Strip thinking tags inline
                                    token = _re_stream.sub(r"<think>[\s\S]*?</think>", "", token)
                                    if token:
                                        yield f"data: {json.dumps({'token': token})}\n\n"
                            except (json.JSONDecodeError, KeyError, IndexError):
                                continue
                except Exception as e:
                    yield f"data: {json.dumps({'error': str(e)})}\n\n"
            from starlette.responses import StreamingResponse as _SR
            return _SR(_stream_gen(), media_type="text/event-stream",
                       headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

        # Non-streaming fallback
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


# (Voice/WebSocket endpoints moved to routes/websocket.py)
# (Agent endpoints moved to routes/agents.py)


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
    """Manually trigger a scheduled task — actually executes it in a background thread."""
    sched_path = os.path.join(os.path.expanduser("~"), ".codec", "schedules.json")
    schedules = []
    try:
        with open(sched_path) as f:
            schedules = json.load(f)
    except Exception:
        return JSONResponse({"error": "No schedules found"}, status_code=404)

    schedule = None
    for s in schedules:
        if s.get("id") == sched_id:
            schedule = s
            break
    if not schedule:
        return JSONResponse({"error": "Not found"}, status_code=404)

    # Pre-create a notification id so we can return it immediately
    notif_id = f"notif_{uuid.uuid4().hex[:10]}"
    crew = schedule.get("crew", "general")
    topic = schedule.get("topic", "")
    title = schedule.get("label", topic[:60] or "Scheduled Task")

    def _execute_task():
        """Background thread: run the task, generate report, save to Google Doc."""
        try:
            import requests as rq, re
            config = {}
            try:
                with open(CONFIG_PATH) as f:
                    config = json.load(f)
            except Exception:
                pass
            base_url = config.get("llm_base_url", "http://localhost:8081/v1")
            model = config.get("llm_model", "mlx-community/Qwen3.5-35B-A3B-4bit")
            api_key = config.get("llm_api_key", "")
            kwargs = config.get("llm_kwargs", {})

            # ── Step 1: If it's a skill-based task, run the skill directly ──
            skill_output = None
            if "ai news digest" in topic.lower() or "news digest" in topic.lower():
                try:
                    import importlib.util
                    spec = importlib.util.spec_from_file_location("ai_news", os.path.join(DASHBOARD_DIR, "skills", "ai_news_digest.py"))
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    skill_output = mod.run()
                except Exception as e:
                    log.warning(f"Skill execution failed, falling back to LLM: {e}")

            # ── Step 2: Build prompt for LLM report ──
            crew_instructions = {
                "research": "You are a senior research analyst at CODEC. Produce a structured, professional report with markdown formatting: use ## headings, bullet points, and a summary section.",
                "security": "You are a CODEC security analyst. Produce a structured security assessment report with markdown formatting: use ## headings for each area, risk levels, and recommendations.",
                "writer": "You are a professional content writer at CODEC. Write a well-structured article with markdown formatting: ## headings, clear sections, and a conclusion.",
                "analyst": "You are a CODEC data analyst. Produce a structured analysis report with markdown formatting: ## headings, key metrics, insights, and action items.",
            }
            system_msg = crew_instructions.get(crew, "You are CODEC, an AI assistant. Produce a structured, professional report with markdown formatting: ## headings, bullet points, key findings, and a summary.")

            if skill_output:
                prompt = f"Here is raw data collected for the task '{title}':\n\n{skill_output}\n\nProduce a well-structured, professional report based on this data. Use ## headings, highlight the most important items, add brief analysis, and end with key takeaways."
            elif crew == "custom" and topic:
                prompt = f"Execute this task and produce a detailed, structured report:\n\n{topic}"
            else:
                prompt = f"Task: {topic}\n\nProduce a detailed, structured report."

            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 6000,
                "temperature": 0.7,
                "stream": False
            }
            payload.update(kwargs)
            r = rq.post(f"{base_url}/chat/completions", json=payload, headers=headers, timeout=300)
            data = r.json()
            answer = data["choices"][0]["message"]["content"].strip()
            answer = re.sub(r'<think>[\s\S]*?</think>', '', answer).strip()

            # ── Step 3: Save to Google Doc ──
            doc_url = None
            try:
                from codec_gdocs import create_google_doc
                doc_title = f"CODEC Report — {title} — {datetime.now().strftime('%b %d, %Y')}"
                doc_url = create_google_doc(doc_title, answer)
                log.info(f"Report saved to Google Doc: {doc_url}")
            except Exception as e:
                log.warning(f"Google Doc creation failed (report still saved locally): {e}")

            # ── Step 4: Save success notification with doc link ──
            body_text = answer[:2000]
            if doc_url:
                body_text = f"📄 [View Full Report]({doc_url})\n\n{body_text}"
            with _notif_lock:
                notifications = _load_notifications()
                for n in notifications:
                    if n["id"] == notif_id:
                        n["body"] = body_text
                        n["status"] = "success"
                        if doc_url:
                            n["doc_url"] = doc_url
                        break
                _write_notifications(notifications)
            _append_schedule_run_log(sched_id, title, "success", (doc_url or answer[:200]))
            log.info(f"Schedule {sched_id} executed successfully")

        except Exception as e:
            error_msg = f"Task execution failed: {str(e)}"
            log.error(f"Schedule {sched_id} failed: {e}")
            with _notif_lock:
                notifications = _load_notifications()
                for n in notifications:
                    if n["id"] == notif_id:
                        n["body"] = error_msg
                        n["status"] = "error"
                        break
                _write_notifications(notifications)
            _append_schedule_run_log(sched_id, title, "error", error_msg)

    # Save a pending notification immediately
    pending_notif = {
        "id": notif_id,
        "type": "task_report",
        "title": title,
        "body": "Task is running...",
        "status": "running",
        "created": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "read": False,
        "schedule_id": sched_id
    }
    with _notif_lock:
        notifications = _load_notifications()
        notifications.insert(0, pending_notif)
        _write_notifications(notifications)

    # Launch background execution
    thread = threading.Thread(target=_execute_task, daemon=True)
    thread.start()

    return {"status": "running", "notification_id": notif_id, "id": sched_id, "crew": crew}


# ── Notification endpoints ──

@app.get("/api/notifications")
async def get_notifications(request: Request):
    """Return all notifications, newest first. Use ?unread=true to filter."""
    notifications = _load_notifications()
    unread_filter = request.query_params.get("unread", "").lower()
    if unread_filter == "true":
        notifications = [n for n in notifications if not n.get("read", False)]
    # Sort newest first by created timestamp
    notifications.sort(key=lambda n: n.get("created", ""), reverse=True)
    return {"notifications": notifications}


@app.get("/api/notifications/count")
async def get_notification_count():
    """Return unread notification count for badge display.
    Only counts completed notifications (success/error), not 'running' ones."""
    notifications = _load_notifications()
    unread = sum(1 for n in notifications
                 if not n.get("read", False)
                 and n.get("status", "success") != "running")
    return {"unread": unread}


@app.post("/api/notifications/{notif_id}/read")
async def mark_notification_read(notif_id: str):
    """Mark a single notification as read."""
    with _notif_lock:
        notifications = _load_notifications()
        for n in notifications:
            if n["id"] == notif_id:
                n["read"] = True
                _write_notifications(notifications)
                return {"status": "ok", "id": notif_id}
    return JSONResponse({"error": "Notification not found"}, status_code=404)


@app.post("/api/notifications/read-all")
async def mark_all_notifications_read():
    """Mark all notifications as read."""
    with _notif_lock:
        notifications = _load_notifications()
        count = 0
        for n in notifications:
            if not n.get("read", False):
                n["read"] = True
                count += 1
        _write_notifications(notifications)
    return {"status": "ok", "marked": count}


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


@app.get("/api/heartbeat/alerts")
async def get_heartbeat_alerts():
    """Get heartbeat alerts configuration."""
    config = {}
    try:
        with open(CONFIG_PATH) as f:
            config = json.load(f)
    except Exception:
        pass
    return {"alerts": config.get("heartbeat_alerts", [])}


@app.put("/api/heartbeat/alerts")
async def update_heartbeat_alerts(request: Request):
    """Update heartbeat alerts configuration."""
    body = await request.json()
    alerts = body.get("alerts", [])
    if not isinstance(alerts, list):
        return JSONResponse({"error": "alerts must be a list"}, status_code=422)
    config = {}
    try:
        with open(CONFIG_PATH) as f:
            config = json.load(f)
    except Exception:
        pass
    config["heartbeat_alerts"] = alerts
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)
    return {"saved": True, "message": f"{len(alerts)} alert(s) saved."}


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


# (Memory endpoints moved to routes/memory.py)
# (Skills list endpoint moved to routes/skills.py)

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

# ═══════════════════════════════════════════════════════════════
# BACKGROUND SERVICES (scheduler, heartbeat, watcher)
# ═══════════════════════════════════════════════════════════════

async def _bg_scheduler():
    """Run schedule checks every 60s, aligned to minute boundaries."""
    from codec_scheduler import check_and_run
    _bg_status["scheduler"]["running"] = True
    log.info("[SCHEDULER] Background service started")
    while True:
        try:
            await asyncio.to_thread(check_and_run)
            _bg_status["scheduler"]["last_tick"] = datetime.now().isoformat()
        except asyncio.CancelledError:
            break
        except Exception as e:
            _bg_status["scheduler"]["errors"] += 1
            log.error(f"[SCHEDULER] Error: {e}")
        now = time.time()
        await asyncio.sleep(max(1, 60 - (now % 60)))
    _bg_status["scheduler"]["running"] = False


async def _bg_heartbeat():
    """Run heartbeat checks every 30 minutes."""
    from codec_heartbeat import heartbeat
    _bg_status["heartbeat"]["running"] = True
    log.info("[HEARTBEAT] Background service started")
    while True:
        try:
            await asyncio.to_thread(heartbeat)
            _bg_status["heartbeat"]["last_tick"] = datetime.now().isoformat()
        except asyncio.CancelledError:
            break
        except Exception as e:
            _bg_status["heartbeat"]["errors"] += 1
            log.error(f"[HEARTBEAT] Error: {e}")
        await asyncio.sleep(1800)
    _bg_status["heartbeat"]["running"] = False


async def _bg_watcher():
    """Poll for draft tasks every 200ms."""
    from codec_watcher import TASK_FILE, handle_draft
    _bg_status["watcher"]["running"] = True
    log.info("[WATCHER] Background service started")
    while True:
        try:
            if os.path.exists(TASK_FILE):
                with open(TASK_FILE) as f:
                    data = json.load(f)
                os.unlink(TASK_FILE)
                await asyncio.to_thread(
                    handle_draft, data["task"], data.get("ctx", ""), data.get("app", "")
                )
                _bg_status["watcher"]["last_tick"] = datetime.now().isoformat()
        except asyncio.CancelledError:
            break
        except Exception as e:
            _bg_status["watcher"]["errors"] += 1
            log.error(f"[WATCHER] Error: {e}")
        await asyncio.sleep(0.2)
    _bg_status["watcher"]["running"] = False


async def _warmup_vision():
    """Pre-load Qwen Vision model so first real request is fast (~7s vs ~23s cold)."""
    await asyncio.sleep(5)  # let other services start first
    try:
        config = {}
        try:
            with open(CONFIG_PATH) as f: config = json.load(f)
        except Exception:
            pass
        vision_url = config.get("vision_base_url", "http://localhost:8082/v1")
        import requests as rq
        # Tiny request just to load model weights into GPU memory
        payload = {
            "model": config.get("vision_model", "mlx-community/Qwen2.5-VL-7B-Instruct-4bit"),
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 1,
        }
        r = rq.post(f"{vision_url}/chat/completions", json=payload,
                     headers={"Content-Type": "application/json"}, timeout=60)
        if r.status_code == 200:
            log.info("[WARMUP] Vision model pre-loaded successfully")
        else:
            log.warning(f"[WARMUP] Vision warmup returned {r.status_code}")
    except Exception as e:
        log.warning(f"[WARMUP] Vision warmup failed (model may cold-start on first use): {e}")


async def _vision_keepalive():
    """Ping vision model every 10 minutes to prevent GPU memory eviction."""
    await asyncio.sleep(120)  # first ping after 2 min (warmup already ran)
    while True:
        try:
            config = {}
            try:
                with open(CONFIG_PATH) as f: config = json.load(f)
            except Exception:
                pass
            vision_url = config.get("vision_base_url", "http://localhost:8082/v1")
            import requests as rq
            r = rq.get(f"{vision_url}/models", timeout=10)
            if r.status_code == 200:
                log.debug("[KEEPALIVE] Vision model alive")
        except Exception:
            pass
        await asyncio.sleep(600)  # every 10 minutes


@app.on_event("startup")
async def _start_background_services():
    """Launch scheduler, heartbeat, watcher, and vision warmup as background async tasks."""
    _bg_tasks["scheduler"] = asyncio.create_task(_bg_scheduler())
    _bg_tasks["heartbeat"] = asyncio.create_task(_bg_heartbeat())
    _bg_tasks["watcher"]   = asyncio.create_task(_bg_watcher())
    _bg_tasks["vision_warmup"] = asyncio.create_task(_warmup_vision())
    _bg_tasks["vision_keepalive"] = asyncio.create_task(_vision_keepalive())
    log.info("[STARTUP] Background services launched: scheduler, heartbeat, watcher, vision-warmup")


@app.on_event("shutdown")
async def _shutdown_services():
    """Cancel background services and close SQLite connections."""
    for name, task in _bg_tasks.items():
        if task and not task.done():
            task.cancel()
            log.info(f"[SHUTDOWN] Cancelling {name}")
    if _bg_tasks:
        await asyncio.gather(*_bg_tasks.values(), return_exceptions=True)
    _bg_tasks.clear()
    log.info("[SHUTDOWN] All background services stopped")
    import routes._shared as _shared
    global _qchat_conn, _vibe_conn
    for conn in (_shared._db_conn, _qchat_conn, _vibe_conn):
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    _shared._db_conn = None
    _qchat_conn = _vibe_conn = None


@app.get("/api/health", response_model=HealthResponse, tags=["Health"])
@app.get("/health", response_model=HealthResponse, include_in_schema=False)
async def health_check():
    """Public health check — returns service status. No authentication required."""
    return {"status": "ok", "service": "CODEC Dashboard", "timestamp": datetime.now().isoformat()}


@app.get("/api/services/status")
async def services_status():
    """Show status of background services (scheduler, heartbeat, watcher)."""
    result = {}
    for name, info in _bg_status.items():
        task = _bg_tasks.get(name)
        result[name] = {
            "running": task is not None and not task.done() if task else False,
            "last_tick": info["last_tick"],
            "errors": info["errors"],
        }
    return result


if __name__ == "__main__":
    from codec_logging import setup_logging
    setup_logging()
    uvicorn.run(app, host="0.0.0.0", port=8090,
                h11_max_incomplete_event_size=50 * 1024 * 1024)  # 50MB for large doc uploads
