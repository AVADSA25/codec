"""CODEC Dashboard — Auth routes (biometric, PIN, TOTP, E2E key exchange)."""
import os, json, secrets, time, subprocess, hmac
from datetime import datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from routes._shared import (
    log, DASHBOARD_DIR, CONFIG_PATH, AUDIT_LOG, _NO_CACHE,
    AUTH_ENABLED, AUTH_SESSION_HOURS, AUTH_BINARY, AUTH_PIN_HASH, AUTH_COOKIE_NAME,
    _auth_sessions, _auth_lock, _e2e_keys,
    _is_auth_compiled, _auth_available, _is_totp_enabled, _verify_biometric_session,
    _save_sessions, _save_e2e_keys, _audit_write, _pin_attempts,
)

router = APIRouter()


@router.get("/auth", response_class=HTMLResponse)
async def auth_page():
    """Serve the biometric authentication page."""
    auth_path = os.path.join(DASHBOARD_DIR, "codec_auth.html")
    if os.path.exists(auth_path):
        with open(auth_path) as f:
            return HTMLResponse(f.read(), headers=_NO_CACHE)
    return HTMLResponse("<h1>Auth page not found</h1>", status_code=500)


@router.get("/api/auth/check")
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


@router.post("/api/auth/verify")
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

            try:
                if result.get("authenticated"):
                    _audit_write(f"[{datetime.now().isoformat()}] AUTH_SUCCESS: method={result.get('method')} ip={client_ip}\n")
                else:
                    _audit_write(f"[{datetime.now().isoformat()}] AUTH_FAILED: error={result.get('error')} ip={client_ip}\n")
            except Exception:
                pass

            if result.get("authenticated"):
                token = result.get("token", secrets.token_hex(32))
                with _auth_lock:
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


@router.post("/api/auth/pin")
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

    # Brute-force protection
    attempt = _pin_attempts.get(client_ip, {"count": 0, "locked_until": 0.0})
    if time.time() < attempt.get("locked_until", 0.0):
        remaining = int(attempt["locked_until"] - time.time())
        return JSONResponse({"error": f"Too many failed attempts. Locked out for {remaining}s."}, status_code=429)

    try:
        if pin_hash == AUTH_PIN_HASH:
            _audit_write(f"[{datetime.now().isoformat()}] AUTH_SUCCESS: method=pin ip={client_ip}\n")
        else:
            _audit_write(f"[{datetime.now().isoformat()}] AUTH_FAILED: method=pin error=wrong_pin ip={client_ip}\n")
    except Exception:
        pass

    if pin_hash == AUTH_PIN_HASH:
        _pin_attempts.pop(client_ip, None)
        token = secrets.token_hex(32)
        with _auth_lock:
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
        attempt = _pin_attempts.get(client_ip, {"count": 0, "locked_until": 0.0})
        attempt["count"] = attempt.get("count", 0) + 1
        if attempt["count"] >= 5:
            attempt["locked_until"] = time.time() + 300
            attempt["count"] = 0
        _pin_attempts[client_ip] = attempt
        return {"authenticated": False, "error": "Incorrect PIN"}


@router.post("/api/auth/totp/setup")
async def totp_setup(request: Request):
    """Generate TOTP secret + QR code for authenticator app setup."""
    import pyotp, qrcode, io, base64
    if not AUTH_ENABLED:
        return JSONResponse({"error": "Auth not enabled"}, status_code=400)
    secret = pyotp.random_base32()
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
    import pyotp
    body = await request.json()
    code = str(body.get("code", ""))
    secret = body.get("secret", "")
    if not code or not secret:
        return JSONResponse({"error": "Missing code or secret"}, status_code=400)
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
        _audit_write(f"[{datetime.now().isoformat()}] TOTP_SETUP: 2FA enabled\n")
        return {"verified": True, "enabled": True, "message": "2FA enabled successfully"}
    return {"verified": False, "error": "Invalid code. Try again."}


@router.post("/api/auth/totp/verify")
async def totp_verify(request: Request):
    """Verify TOTP code during login (after Touch ID/PIN)."""
    import pyotp
    body = await request.json()
    code = str(body.get("code", ""))
    pending_token = body.get("token", "")
    if not code or not pending_token:
        return JSONResponse({"error": "Missing code or token"}, status_code=400)
    totp_secret = ""
    try:
        with open(CONFIG_PATH) as f:
            totp_secret = json.load(f).get("totp_secret", "")
    except Exception:
        pass
    if not totp_secret:
        return JSONResponse({"error": "TOTP not configured"}, status_code=400)
    totp = pyotp.TOTP(totp_secret)
    client_ip = request.client.host if request.client else "unknown"
    if totp.verify(code, valid_window=1):
        with _auth_lock:
            if pending_token in _auth_sessions:
                _auth_sessions[pending_token]["totp_verified"] = True
                _save_sessions()
        _audit_write(f"[{datetime.now().isoformat()}] TOTP_SUCCESS: ip={client_ip}\n")
        return {"verified": True, "token": pending_token}
    _audit_write(f"[{datetime.now().isoformat()}] TOTP_FAILED: ip={client_ip}\n")
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
    _audit_write(f"[{datetime.now().isoformat()}] TOTP_DISABLED: 2FA disabled by ip={client_ip}\n")
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
    return {"logged_out": True}


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
