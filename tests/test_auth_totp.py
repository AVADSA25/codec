"""Tests for TOTP enrollment auth gate — audit finding C1 (CRITICAL).

Closes the unauthenticated-TOTP-takeover:
  - /api/auth/totp/setup and /api/auth/totp/confirm must require an
    authenticated session (Touch ID / PIN), like their siblings
    /totp/disable and /totp/enable already do.
  - /confirm must verify the code against the SERVER-stored pending secret
    created by /setup, not an attacker-supplied `secret` in the request body.

Reference: docs/SECURITY-REMEDIATION-DESIGN.md Fix #1a.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
from datetime import datetime
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


@pytest.fixture
def auth_app(monkeypatch, tmp_path):
    """Bare FastAPI app mounting only the auth router, with auth forced ON
    so `_verify_biometric_session` actually gates (it short-circuits to True
    when auth is disabled / unavailable). CONFIG_PATH is redirected to a tmp
    file so no test can ever touch the real ~/.codec/config.json."""
    import routes._shared as shared
    import routes.auth as auth_routes

    cfg = str(tmp_path / "config.json")
    # Isolation: never write the real config.
    monkeypatch.setattr(shared, "CONFIG_PATH", cfg)
    monkeypatch.setattr(auth_routes, "CONFIG_PATH", cfg)
    # Force auth ON so the session gate is live.
    monkeypatch.setattr(shared, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_routes, "AUTH_ENABLED", True)
    monkeypatch.setattr(shared, "_auth_available", lambda: True)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(auth_routes.router)
    return TestClient(app)


def test_totp_confirm_rejects_unauthenticated(auth_app):
    """C1 layer 1: an unauthenticated caller must NOT be able to enroll a TOTP
    secret. Before the fix, /confirm trusted a body-supplied secret+code and
    wrote it to config.json — full account takeover. It must return 401.

    The gate must reject BEFORE any TOTP processing, so this sends a dummy
    code+secret (no pyotp needed) — a 401 proves the short-circuit fires
    ahead of secret verification."""
    r = auth_app.post(
        "/api/auth/totp/confirm",
        json={"code": "123456", "secret": "DUMMYSECRET234567ABCD"},
    )
    assert r.status_code == 401, (
        f"Unauthenticated TOTP enrollment must be rejected (C1); "
        f"got {r.status_code}: {r.text[:200]}"
    )


def test_totp_setup_rejects_unauthenticated(auth_app):
    """C1 layer 1: /setup generates a server-side secret + QR. An
    unauthenticated caller must not be able to start enrollment either —
    otherwise an attacker can drive the whole setup→confirm flow. 401."""
    r = auth_app.post("/api/auth/totp/setup", json={})
    assert r.status_code == 401, (
        f"Unauthenticated TOTP setup must be rejected (C1); "
        f"got {r.status_code}: {r.text[:200]}"
    )


# ── Layer 2: server owns the secret ─────────────────────────────────────────


@pytest.fixture
def authed(auth_app):
    """Yield a client with a FORGED valid biometric session cookie so the auth
    gate passes — letting us exercise the post-gate server-owns-secret logic.
    The session is in-memory only and torn down after the test."""
    import routes._shared as shared

    token = "test-session-" + secrets.token_hex(8)
    with shared._auth_lock:
        shared._auth_sessions[token] = {
            "created": datetime.now(),
            "ip": "127.0.0.1",
            "method": "test",
        }
    auth_app.cookies.set(shared.AUTH_COOKIE_NAME, token)
    try:
        yield auth_app
    finally:
        with shared._auth_lock:
            shared._auth_sessions.pop(token, None)


def test_totp_confirm_uses_server_secret_not_body(authed):
    """C1 layer 2 (THE security invariant): /confirm must verify against the
    SERVER-stashed pending secret created by /setup, NOT an attacker-supplied
    body `secret`. Even an authenticated user (or a CSRF'd browser) must not
    be able to enroll a secret of their own choosing."""
    import pyotp
    import routes._shared as shared

    client = authed
    # 1. Begin enrollment — server generates + must stash its own secret.
    r_setup = client.post("/api/auth/totp/setup", json={})
    assert r_setup.status_code == 200, r_setup.text
    server_secret = r_setup.json()["secret"]

    # 2. Attacker submits a DIFFERENT secret + a code valid FOR THAT secret.
    attacker_secret = pyotp.random_base32()
    assert attacker_secret != server_secret
    attacker_code = pyotp.TOTP(attacker_secret).now()
    r = client.post(
        "/api/auth/totp/confirm",
        json={"code": attacker_code, "secret": attacker_secret},
    )
    body = r.json()
    assert body.get("verified") is not True, (
        f"Body-supplied secret must be IGNORED (C1 layer 2); got {body}"
    )
    # 3. The attacker secret must NEVER reach config.json.
    cfg = {}
    if os.path.exists(shared.CONFIG_PATH):
        with open(shared.CONFIG_PATH) as f:
            cfg = json.load(f)
    assert cfg.get("totp_secret") != attacker_secret, (
        "Attacker-chosen secret must never be persisted to config (C1 layer 2)"
    )


def test_totp_confirm_accepts_server_secret_code(authed):
    """C1 layer 2 happy path: a code computed from the SERVER secret (with NO
    body secret at all) confirms and enrolls exactly that server secret."""
    import pyotp
    import routes._shared as shared

    client = authed
    r_setup = client.post("/api/auth/totp/setup", json={})
    assert r_setup.status_code == 200, r_setup.text
    server_secret = r_setup.json()["secret"]

    code = pyotp.TOTP(server_secret).now()
    # Body deliberately omits `secret` — the server must use its own pending one.
    r = client.post("/api/auth/totp/confirm", json={"code": code})
    assert r.status_code == 200, r.text
    assert r.json().get("verified") is True, r.text
    with open(shared.CONFIG_PATH) as f:
        cfg = json.load(f)
    assert cfg.get("totp_secret") == server_secret, (
        "Confirmed enrollment must persist the SERVER secret (C1 layer 2)"
    )
