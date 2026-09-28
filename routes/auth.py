"""CODEC Dashboard — Auth routes (biometric, PIN, TOTP, E2E key exchange)."""
import os
import json
# hmac was used by the legacy SHA-256 pin verify path; codec_pinhash.verify_pin
# now owns the constant-time compare. Kept the import removal to avoid F401.
import secrets
import time
import asyncio
import threading
import subprocess
from datetime import datetime

# Audit emits route through the unified log_event adapter (real, not no-op)
# per docs/PHASE1-STEP1-DESIGN.md.
from codec_audit import log_event

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from routes._shared import (
    DASHBOARD_DIR, CONFIG_PATH, _NO_CACHE,
    AUTH_ENABLED, AUTH_SESSION_HOURS, AUTH_BINARY, AUTH_PIN_HASH, AUTH_COOKIE_NAME,
    _auth_sessions, _auth_lock, _e2e_keys,
    _is_auth_compiled, _is_totp_enabled, _verify_biometric_session, _is_remote_request,
    _save_sessions, _save_e2e_keys, _audit_event, _pin_attempts,
)

router = APIRouter()


# ── Brute-force + prompt-spam limits (audit 2026-09-27) ──────────────────
# Behind the Cloudflare tunnel every request arrives from 127.0.0.1, so
# request.client.host was one shared key for the whole internet: an attacker
# could lock the owner out, and each owner login reset the attacker's ladder.
_GLOBAL_FAIL_WINDOW_S = 3600
_GLOBAL_FAIL_MAX = 30          # failed PIN/TOTP attempts per hour, all clients
_global_fails: list = []
_global_fail_lock = threading.Lock()
_TOUCHID_WINDOW_S = 600
_TOUCHID_MAX_PROMPTS = 5       # Touch ID prompts per 10 min, all clients
_touchid_prompts: list = []
_touchid_busy = threading.Lock()

# The session lives in an HttpOnly cookie set here, so page JavaScript never
# sees it (audit 2026-09-27 item 2). Pages echo the separate, random CSRF
# cookie in the x-csrf-token header.
CSRF_COOKIE_NAME = "codec_csrf"


def _is_https(request: Request) -> bool:
    """True when the visitor's connection is HTTPS. Behind the Cloudflare
    tunnel the last hop to this Mac is plain HTTP, so read the forwarded scheme."""
    if request.url.scheme == "https":
        return True
    if request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower() == "https":
        return True
    return '"scheme":"https"' in request.headers.get("cf-visitor", "").replace(" ", "")


def _login_response(request: Request, token: str, body: dict) -> JSONResponse:
    """Answer a successful login: the session and CSRF cookies go in
    Set-Cookie, never in the JSON body."""
    resp = JSONResponse(body)
    max_age = int(AUTH_SESSION_HOURS * 3600)
    secure = _is_https(request)
    resp.set_cookie(AUTH_COOKIE_NAME, token, max_age=max_age, path="/",
                    httponly=True, secure=secure, samesite="lax")
    resp.set_cookie(CSRF_COOKIE_NAME, secrets.token_urlsafe(24), max_age=max_age,
                    path="/", httponly=False, secure=secure, samesite="lax")
    return resp


def _client_key(request: Request) -> str:
    """Per-visitor key. cloudflared connects from loopback and Cloudflare sets
    CF-Connecting-IP (a visitor cannot override it through the tunnel); a
    direct local request has no such header and keeps the peer address."""
    peer = request.client.host if request.client else "unknown"
    if peer in ("127.0.0.1", "::1"):
        cf_ip = request.headers.get("cf-connecting-ip", "").strip()
        if cf_ip:
            return cf_ip
    return peer


def _global_fail_locked() -> bool:
    now = time.time()
    with _global_fail_lock:
        _global_fails[:] = [t for t in _global_fails if t > now - _GLOBAL_FAIL_WINDOW_S]
        return len(_global_fails) >= _GLOBAL_FAIL_MAX


def _record_global_fail() -> None:
    with _global_fail_lock:
        _global_fails.append(time.time())


def _upgrade_pin_hash(pin: str) -> None:
    """After a correct PIN, replace a legacy unsalted SHA-256 hash with argon2id.
    Best effort; never blocks login."""
    global AUTH_PIN_HASH
    try:
        from codec_pinhash import ARGON2_AVAILABLE, hash_pin
        if not ARGON2_AVAILABLE or AUTH_PIN_HASH.startswith("$argon2"):
            return
        new_hash = hash_pin(pin)
        from codec_jsonstore import read_modify_write

        def _mutate(cfg):
            if not cfg:  # missing/corrupt config: never overwrite with {}
                raise ValueError("config unreadable")
            cfg["auth_pin_hash"] = new_hash
            return cfg
        read_modify_write(CONFIG_PATH, _mutate)
        AUTH_PIN_HASH = new_hash
        log_event("auth_pin_rehashed", "codec-auth", "PIN hash upgraded to argon2id")
    except Exception as e:
        log_event("auth_pin_rehash_failed", "codec-auth", f"PIN rehash skipped: {type(e).__name__}",
                  level="warning", outcome="error")


@router.get("/auth", response_class=HTMLResponse)
async def auth_page():
    """Serve the biometric authentication page."""
    auth_path = os.path.join(DASHBOARD_DIR, "codec_auth.html")
    if os.path.exists(auth_path):
        with open(auth_path) as f:
            return HTMLResponse(f.read(), headers=_NO_CACHE)
    return HTMLResponse("<h1>Auth page not found</h1>", status_code=500)


@router.get("/api/auth/check")
async def auth_check(request: Request):
    """Check which auth methods are available (Touch ID and/or PIN).
    Touch ID is offered only to requests from this Mac: the prompt appears
    on the Mac, so a phone or the tunnel gets the PIN."""
    result = {"touchid_available": False, "pin_available": bool(AUTH_PIN_HASH)}

    if _is_auth_compiled() and not _is_remote_request(request):
        try:
            r = await asyncio.to_thread(
                subprocess.run, [AUTH_BINARY, "--check"],
                capture_output=True, text=True, timeout=5)
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


@router.post("/api/auth/verify")
async def auth_verify(request: Request):
    """Trigger Touch ID verification on the Mac. Only requests from this Mac
    may pop the prompt (audit 2026-09-27)."""
    if _is_remote_request(request):
        return JSONResponse({"error": "Touch ID works only on this Mac. Use your PIN."}, status_code=403)
    if not _is_auth_compiled():
        return JSONResponse({"error": "Auth binary not compiled"}, status_code=500)
    now = time.time()
    _touchid_prompts[:] = [t for t in _touchid_prompts if t > now - _TOUCHID_WINDOW_S]
    if len(_touchid_prompts) >= _TOUCHID_MAX_PROMPTS:
        return JSONResponse({"error": "Too many Touch ID requests. Try again in a few minutes."}, status_code=429)
    if not _touchid_busy.acquire(blocking=False):
        return JSONResponse({"error": "A Touch ID prompt is already waiting on the Mac."}, status_code=429)
    _touchid_prompts.append(now)
    try:
        # Off the event loop: the prompt can wait up to 65 s for a finger.
        r = await asyncio.to_thread(
            subprocess.run, [AUTH_BINARY, "--verify"],
            capture_output=True, text=True, timeout=65,
        )
        if r.returncode == 0:
            result = json.loads(r.stdout)
            client_ip = _client_key(request)

            try:
                if result.get("authenticated"):
                    _audit_event("auth_success", method=result.get("method"), ip=client_ip)
                else:
                    _audit_event("auth_failed", outcome="error", level="warning", error=result.get("error"), ip=client_ip)
            except Exception:
                pass

            if result.get("authenticated"):
                method = result.get("method", "unknown")
                log_event("auth_success", "codec-auth", f"Auth success: {method}", extra={"method": method})
                token = result.get("token", secrets.token_hex(32))
                with _auth_lock:
                    _auth_sessions[token] = {
                        "created": datetime.now(),
                        "ip": client_ip,
                        "method": result.get("method", "unknown"),
                    }
                    _save_sessions()
                return _login_response(request, token, {
                    "authenticated": True,
                    "method": result.get("method"),
                    "expires_hours": AUTH_SESSION_HOURS,
                })
            else:
                log_event("auth_reject", "codec-auth", "Auth failed", outcome="denied", level="warning")
                return {
                    "authenticated": False,
                    "error": result.get("error", "Authentication failed"),
                }
        return JSONResponse({"error": "Auth binary failed"}, status_code=500)
    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "Authentication timed out"}, status_code=408)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        _touchid_busy.release()


@router.post("/api/auth/pin")
async def auth_pin(request: Request):
    """Verify a PIN code.

    B8 / SR-31: hash verification routes through codec_pinhash, which
    accepts both argon2id (new) and SHA-256 (legacy) `auth_pin_hash`
    values. New PINs set via `/api/auth/pin/set` (or by hand) should use
    argon2id whenever argon2-cffi is installed.
    """
    from codec_pinhash import verify_pin
    if not AUTH_PIN_HASH:
        return JSONResponse({"error": "PIN authentication not configured"}, status_code=400)
    try:
        body = await request.json()
        pin = str(body.get("pin", ""))
    except Exception:
        return JSONResponse({"error": "Missing pin field"}, status_code=400)

    client_ip = _client_key(request)
    if _global_fail_locked():
        return JSONResponse({"error": "Too many failed attempts. Try again later."}, status_code=429)

    # Brute-force protection — escalating lockout (OWASP standard)
    # Lockout durations: 30s → 60s → 2min → 5min → 15min → 30min (cap)
    _LOCKOUT_LADDER = [30, 60, 120, 300, 900, 1800]
    attempt = _pin_attempts.get(client_ip, {"count": 0, "locked_until": 0.0, "lockout_level": 0})
    if time.time() < attempt.get("locked_until", 0.0):
        remaining = int(attempt["locked_until"] - time.time())
        return JSONResponse({"error": f"Too many failed attempts. Locked out for {remaining}s."}, status_code=429)

    pin_ok = verify_pin(pin, AUTH_PIN_HASH)
    try:
        if pin_ok:
            _audit_event("auth_success", method="pin", ip=client_ip)
        else:
            _audit_event("auth_failed", outcome="error", level="warning", method="pin", error="wrong_pin", ip=client_ip)
    except Exception:
        pass

    if pin_ok:
        method = "pin"
        log_event("auth_success", "codec-auth", f"Auth success: {method}", extra={"method": method})
        _pin_attempts.pop(client_ip, None)
        _upgrade_pin_hash(pin)
        token = secrets.token_hex(32)
        with _auth_lock:
            _auth_sessions[token] = {
                "created": datetime.now(),
                "ip": client_ip,
                "method": "pin",
            }
            _save_sessions()
        return _login_response(request, token, {
            "authenticated": True,
            "method": "pin",
            "expires_hours": AUTH_SESSION_HOURS,
        })
    else:
        log_event("auth_reject", "codec-auth", "Auth failed", outcome="denied", level="warning")
        _record_global_fail()
        attempt = _pin_attempts.get(client_ip, {"count": 0, "locked_until": 0.0, "lockout_level": 0})
        attempt["count"] = attempt.get("count", 0) + 1
        if attempt["count"] >= 5:
            level = min(attempt.get("lockout_level", 0), len(_LOCKOUT_LADDER) - 1)
            lockout_secs = _LOCKOUT_LADDER[level]
            attempt["locked_until"] = time.time() + lockout_secs
            attempt["lockout_level"] = level + 1
            attempt["count"] = 0
            log_event("auth_reject", "codec-auth", f"PIN lockout level {level + 1}: {lockout_secs}s for {client_ip}", outcome="denied", level="warning", extra={"reason": "pin_lockout", "lockout_level": level + 1, "lockout_sec": lockout_secs, "client_ip": client_ip})
        _pin_attempts[client_ip] = attempt
        remaining_attempts = 5 - attempt["count"]
        return {"authenticated": False, "error": f"Incorrect PIN. {remaining_attempts} attempts remaining."}


@router.post("/api/auth/totp/setup")
async def totp_setup(request: Request):
    """Generate TOTP secret + QR code for authenticator app setup."""
    if not _verify_biometric_session(request):
        return JSONResponse({"error": "Authentication required"}, status_code=401)
    import pyotp
    import qrcode
    import io
    import base64
    if not AUTH_ENABLED:
        return JSONResponse({"error": "Auth not enabled"}, status_code=400)
    secret = pyotp.random_base32()
    # C1 layer 2: the server OWNS the enrollment secret. Stash it on the
    # caller's session so /confirm verifies against THIS value — never a
    # client-supplied body `secret`. In-memory only: _save_sessions() whitelists
    # created/ip/method, so the pending secret is never written to disk.
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if token:
        with _auth_lock:
            if token in _auth_sessions:
                _auth_sessions[token]["pending_totp_secret"] = secret
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name="CODEC", issuer_name="CODEC")
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qr_b64 = base64.b64encode(buf.getvalue()).decode()
    return {"secret": secret, "qr_code": qr_b64, "uri": uri}


@router.post("/api/auth/totp/confirm")
async def totp_confirm(request: Request):
    """Verify TOTP code and save secret to config if valid (first-time setup)."""
    if not _verify_biometric_session(request):
        return JSONResponse({"error": "Authentication required"}, status_code=401)
    import pyotp
    body = await request.json()
    code = str(body.get("code", ""))
    # C1 layer 2: ignore any client-supplied `secret`. Verify against the
    # server-stashed pending secret created by /setup, keyed to this session.
    token = request.cookies.get(AUTH_COOKIE_NAME)
    secret = ""
    if token:
        with _auth_lock:
            sess = _auth_sessions.get(token)
            if sess:
                secret = sess.get("pending_totp_secret", "")
    if not code:
        return JSONResponse({"error": "Missing code"}, status_code=400)
    if not secret:
        return JSONResponse(
            {"error": "No pending TOTP enrollment — run setup first"},
            status_code=400,
        )
    totp = pyotp.TOTP(secret)
    if totp.verify(code, valid_window=1):
        try:
            cfg_data = {}
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH) as f:
                    cfg_data = json.load(f)
            cfg_data["totp_secret"] = secret
            cfg_data.pop("totp_disabled", None)
            with open(CONFIG_PATH, "w") as f:
                json.dump(cfg_data, f, indent=2)
        except Exception as e:
            return JSONResponse({"error": f"Failed to save config: {e}"}, status_code=500)
        # Enrollment complete — clear the one-time pending secret.
        if token:
            with _auth_lock:
                sess = _auth_sessions.get(token)
                if sess:
                    sess.pop("pending_totp_secret", None)
        _audit_event("totp_enabled")
        return {"verified": True, "enabled": True, "message": "2FA enabled successfully"}
    return {"verified": False, "error": "Invalid code. Try again."}


@router.post("/api/auth/totp/verify")
async def totp_verify(request: Request):
    """Verify TOTP code during login (after Touch ID/PIN)."""
    import pyotp
    body = await request.json()
    code = str(body.get("code", ""))
    # The PIN / Touch ID step already set the (not yet TOTP-verified) session
    # cookie; a token in the body is no longer read.
    pending_token = request.cookies.get(AUTH_COOKIE_NAME, "")
    if not code or not pending_token:
        return JSONResponse({"error": "Missing code or session. Log in again."}, status_code=400)
    totp_secret = ""
    try:
        with open(CONFIG_PATH) as f:
            totp_secret = json.load(f).get("totp_secret", "")
    except Exception:
        pass
    if not totp_secret:
        return JSONResponse({"error": "TOTP not configured"}, status_code=400)
    totp = pyotp.TOTP(totp_secret)
    client_ip = _client_key(request)
    _tkey = "totp:" + client_ip
    if _global_fail_locked() or time.time() < _pin_attempts.get(_tkey, {}).get("locked_until", 0.0):
        return JSONResponse({"error": "Too many failed attempts. Try again later."}, status_code=429)
    if totp.verify(code, valid_window=1):
        _pin_attempts.pop(_tkey, None)
        with _auth_lock:
            if pending_token in _auth_sessions:
                _auth_sessions[pending_token]["totp_verified"] = True
                _save_sessions()
        _audit_event("totp_success", ip=client_ip)
        return {"verified": True}
    _audit_event("totp_failed", outcome="error", level="warning", ip=client_ip)
    _record_global_fail()
    _t = _pin_attempts.get(_tkey, {"count": 0, "locked_until": 0.0})
    _t["count"] = _t.get("count", 0) + 1
    if _t["count"] >= 5:
        _t["locked_until"], _t["count"] = time.time() + 900, 0
    _pin_attempts[_tkey] = _t
    return {"verified": False, "error": "Invalid code"}


@router.post("/api/auth/totp/disable")
async def totp_disable(request: Request):
    """Disable TOTP 2FA -- requires authenticated session + valid TOTP code."""
    if not _verify_biometric_session(request):
        return JSONResponse({"error": "Authentication required"}, status_code=401)
    import pyotp
    try:
        body = await request.json()
        code = str(body.get("code", ""))
    except Exception:
        return JSONResponse({"error": "Missing TOTP code"}, status_code=400)
    if not code:
        return JSONResponse({"error": "Enter your authenticator code to disable 2FA"}, status_code=400)
    totp_secret = ""
    try:
        with open(CONFIG_PATH) as f:
            totp_secret = json.load(f).get("totp_secret", "")
    except Exception:
        pass
    if not totp_secret:
        return {"disabled": True}
    totp = pyotp.TOTP(totp_secret)
    if not totp.verify(code, valid_window=1):
        return JSONResponse({"error": "Invalid code"}, status_code=400)
    try:
        cfg_data = {}
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH) as f:
                cfg_data = json.load(f)
        cfg_data["totp_disabled"] = True
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg_data, f, indent=2)
    except Exception as e:
        return JSONResponse({"error": f"Failed to update config: {e}"}, status_code=500)
    with _auth_lock:
        for token, session in _auth_sessions.items():
            session.pop("totp_verified", None)
        _save_sessions()
    client_ip = request.client.host if request.client else "unknown"
    _audit_event("totp_disabled", level="warning", ip=client_ip)
    return {"disabled": True}


@router.post("/api/auth/totp/enable")
async def totp_enable(request: Request):
    """Re-enable TOTP using existing secret."""
    if not _verify_biometric_session(request):
        return JSONResponse({"error": "Auth required"}, status_code=401)
    import pyotp
    body = await request.json()
    code = str(body.get("code", ""))
    if not code:
        return JSONResponse({"error": "Enter your authenticator code"}, status_code=400)
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    secret = cfg.get("totp_secret", "")
    if not secret:
        return JSONResponse({"error": "No TOTP secret found -- use Setup 2FA first"}, status_code=400)
    totp = pyotp.TOTP(secret)
    if not totp.verify(code, valid_window=1):
        return JSONResponse({"error": "Invalid code"}, status_code=400)
    cfg.pop("totp_disabled", None)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
    return {"enabled": True}


@router.post("/api/auth/logout")
async def auth_logout(request: Request):
    """Invalidate the current biometric session."""
    token = request.cookies.get(AUTH_COOKIE_NAME)
    with _auth_lock:
        if token and token in _auth_sessions:
            del _auth_sessions[token]
            _save_sessions()
    _e2e_keys.pop(token, None)
    resp = JSONResponse({"logged_out": True})
    resp.delete_cookie(AUTH_COOKIE_NAME, path="/")
    resp.delete_cookie(CSRF_COOKIE_NAME, path="/")
    return resp


@router.get("/api/auth/status")
async def auth_status(request: Request):
    """Check if current session is valid."""
    valid = _verify_biometric_session(request)
    totp_secret_exists = False
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        totp_secret_exists = bool(cfg.get("totp_secret"))
    except Exception:
        pass
    return {
        "authenticated": valid,
        "auth_enabled": AUTH_ENABLED,
        "touchid_compiled": _is_auth_compiled(),
        "pin_configured": bool(AUTH_PIN_HASH),
        "totp_enabled": _is_totp_enabled(),
        "totp_secret_exists": totp_secret_exists,
    }


@router.post("/api/auth/keyexchange")
async def e2e_keyexchange(request: Request):
    """ECDH P-256 key exchange -- derives shared AES-256-GCM key for E2E encryption."""
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from cryptography.hazmat.primitives import hashes, serialization
    except ImportError:
        return JSONResponse({"error": "cryptography library not available"}, status_code=500)
    body = await request.json()
    client_pub_b64 = body.get("pub")
    if not client_pub_b64:
        return JSONResponse({"error": "missing pub"}, status_code=400)
    import base64
    client_pub_raw = base64.b64decode(client_pub_b64)
    client_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), client_pub_raw)
    server_key = ec.generate_private_key(ec.SECP256R1())
    shared = server_key.exchange(ec.ECDH(), client_pub)
    aes_key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"codec-e2e").derive(shared)
    server_pub_raw = server_key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    token = request.cookies.get(AUTH_COOKIE_NAME, "")
    if token:
        _e2e_keys[token] = aes_key
        _save_e2e_keys()
    return {"pub": base64.b64encode(server_pub_raw).decode()}
