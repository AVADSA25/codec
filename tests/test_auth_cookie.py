"""Session cookie set by the server (audit 2026-09-27, item 2).

Logins answer with an HttpOnly session cookie and a separate random CSRF
cookie; the token never appears in a response body or a URL. Touch ID can
only be triggered from this Mac.
"""
import hashlib
import json
import logging

import pytest
from fastapi.testclient import TestClient

import codec_config
import codec_dashboard
import routes._shared as shared
import routes.auth as auth
from codec_dashboard import app

PIN = "4821"
TUNNEL = {"cf-connecting-ip": "203.0.113.9"}


@pytest.fixture(autouse=True)
def pin_auth(monkeypatch):
    """PIN auth on, no dashboard token, no disk writes, clean session table."""
    pin_hash = hashlib.sha256(PIN.encode()).hexdigest()
    monkeypatch.setattr(codec_config, "get_dashboard_token", lambda: "")
    monkeypatch.setattr(codec_dashboard, "AUTH_ENABLED", True)
    monkeypatch.setattr(shared, "AUTH_ENABLED", True)
    monkeypatch.setattr(shared, "AUTH_PIN_HASH", pin_hash)
    monkeypatch.setattr(auth, "AUTH_PIN_HASH", pin_hash)
    monkeypatch.setattr(shared, "_is_totp_enabled", lambda: False)
    monkeypatch.setattr(auth, "_upgrade_pin_hash", lambda pin: None)
    monkeypatch.setattr(auth, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(auth, "_audit_event", lambda *a, **k: None)
    monkeypatch.setattr(auth, "_save_sessions", lambda: None)
    monkeypatch.setattr(shared, "_save_sessions", lambda: None)
    monkeypatch.setattr(auth, "_global_fails", [])
    monkeypatch.setattr(auth, "_pin_attempts", {})
    monkeypatch.setattr(auth, "_touchid_prompts", [])
    monkeypatch.setattr(auth, "AUTH_TOUCHID_REMOTE", False)
    saved = dict(shared._auth_sessions)
    shared._auth_sessions.clear()
    yield
    shared._auth_sessions.clear()
    shared._auth_sessions.update(saved)


def _cookies(resp):
    """{name: raw Set-Cookie header} for every cookie the response sets."""
    return {h.split("=", 1)[0]: h for h in resp.headers.get_list("set-cookie")}


def _value(header):
    return header.split("=", 1)[1].split(";", 1)[0]


def _login(client, **headers):
    r = client.post("/api/auth/pin", json={"pin": PIN}, headers=headers)
    assert r.status_code == 200 and r.json()["authenticated"] is True
    return r


def test_pin_login_sets_httponly_session_cookie_and_no_token_in_body():
    r = _login(TestClient(app))
    assert "token" not in r.json()
    c = _cookies(r)
    session = c["codec_session"]
    assert "HttpOnly" in session and "SameSite=lax" in session and "Path=/" in session
    assert f"Max-Age={int(auth.AUTH_SESSION_HOURS * 3600)}" in session
    assert "Secure" not in session  # plain http on this Mac
    assert _value(session) in shared._auth_sessions


def test_login_over_https_marks_both_cookies_secure():
    for hdr in ({"x-forwarded-proto": "https"}, {"cf-visitor": '{"scheme":"https"}'}):
        c = _cookies(_login(TestClient(app), **hdr))
        assert "Secure" in c["codec_session"] and "Secure" in c["codec_csrf"]


def test_csrf_cookie_is_random_and_readable_by_pages():
    first, second = _cookies(_login(TestClient(app))), _cookies(_login(TestClient(app)))
    csrf, session = _value(first["codec_csrf"]), _value(first["codec_session"])
    assert "HttpOnly" not in first["codec_csrf"]  # pages echo it in x-csrf-token
    assert len(csrf) >= 24 and csrf not in session and session[:16] != csrf
    assert csrf != _value(second["codec_csrf"])


def test_session_cookie_authenticates_and_query_token_does_not():
    client = TestClient(app)
    token = _value(_cookies(_login(client))["codec_session"])
    assert client.get("/api/status").status_code == 200  # cookie jar sends the cookie
    stranger = TestClient(app)
    assert stranger.get(f"/api/status?s={token}").status_code == 401


def test_totp_step_reads_the_session_from_the_cookie(monkeypatch, tmp_path):
    pyotp = pytest.importorskip("pyotp")
    secret = pyotp.random_base32()
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"totp_secret": secret}))
    monkeypatch.setattr(auth, "CONFIG_PATH", str(cfg))
    monkeypatch.setattr(shared, "_is_totp_enabled", lambda: True)
    client = TestClient(app)
    token = _value(_cookies(_login(client))["codec_session"])
    assert client.get("/api/status").status_code == 401  # not TOTP-verified yet
    # A token in the body alone is not enough any more.
    r = TestClient(app).post("/api/auth/totp/verify",
                             json={"code": pyotp.TOTP(secret).now(), "token": token})
    assert r.status_code == 400
    r = client.post("/api/auth/totp/verify", json={"code": pyotp.TOTP(secret).now()})
    assert r.json() == {"verified": True}
    assert shared._auth_sessions[token].get("totp_verified") is True
    assert client.get("/api/status").status_code == 200


def test_logout_deletes_session_and_clears_both_cookies():
    client = TestClient(app)
    token = _value(_cookies(_login(client))["codec_session"])
    r = client.post("/api/auth/logout")
    c = _cookies(r)
    assert "Max-Age=0" in c["codec_session"] and "Max-Age=0" in c["codec_csrf"]
    assert token not in shared._auth_sessions
    assert client.get("/api/status").status_code == 401


def test_rejected_request_log_shows_no_cookie_characters(caplog):
    with caplog.at_level(logging.WARNING, logger="codec_dashboard"):
        r = TestClient(app, cookies={"codec_session": "abcdef0123456789secret"}).get("/api/status")
    assert r.status_code == 401
    rejected = [m for m in caplog.messages if "AUTH REJECTED" in m]
    assert rejected and all("abcdef" not in m for m in rejected)
    assert "cookie=present" in rejected[0]


def test_touchid_verify_refuses_tunnel_requests(monkeypatch):
    monkeypatch.setattr(auth, "_is_auth_compiled", lambda: True)

    def _no_prompt(*a, **k):
        raise AssertionError("the Touch ID binary must not run for a tunnel request")
    monkeypatch.setattr(auth.subprocess, "run", _no_prompt)
    r = TestClient(app, client=("127.0.0.1", 5000)).post("/api/auth/verify", headers=TUNNEL)
    assert r.status_code == 403


def test_touchid_hidden_from_tunnel_in_auth_check(monkeypatch):
    monkeypatch.setattr(auth, "_is_auth_compiled", lambda: True)
    calls = []

    def _check(*a, **k):
        calls.append(a)
        return type("R", (), {"returncode": 0, "stdout": '{"available": true, "method": "touchid"}'})()
    monkeypatch.setattr(auth.subprocess, "run", _check)
    client = TestClient(app, client=("127.0.0.1", 5000))
    assert client.get("/api/auth/check").json()["touchid_available"] is True
    n = len(calls)
    assert client.get("/api/auth/check", headers=TUNNEL).json()["touchid_available"] is False
    assert len(calls) == n  # not even asked


def _touchid_binary(monkeypatch, stdout):
    """Fake the Touch ID binary; returns the list of calls it received."""
    monkeypatch.setattr(auth, "_is_auth_compiled", lambda: True)
    calls = []

    def _run(*a, **k):
        calls.append(a)
        return type("R", (), {"returncode": 0, "stdout": stdout})()
    monkeypatch.setattr(auth.subprocess, "run", _run)
    return calls


def test_touchid_offered_to_tunnel_when_remote_switch_on(monkeypatch):
    monkeypatch.setattr(auth, "AUTH_TOUCHID_REMOTE", True)
    _touchid_binary(monkeypatch, '{"available": true, "method": "touchid"}')
    client = TestClient(app, client=("127.0.0.1", 5000))
    assert client.get("/api/auth/check", headers=TUNNEL).json()["touchid_available"] is True


def test_touchid_verify_prompts_for_tunnel_when_remote_switch_on(monkeypatch):
    monkeypatch.setattr(auth, "AUTH_TOUCHID_REMOTE", True)
    calls = _touchid_binary(monkeypatch, '{"authenticated": true, "method": "touchid"}')
    r = TestClient(app, client=("127.0.0.1", 5000)).post("/api/auth/verify", headers=TUNNEL)
    assert r.status_code == 200 and r.json()["authenticated"] is True
    assert len(calls) == 1  # the prompt ran on the Mac
    assert _value(_cookies(r)["codec_session"]) in shared._auth_sessions


def test_touchid_rate_limit_applies_to_tunnel_requests(monkeypatch):
    monkeypatch.setattr(auth, "AUTH_TOUCHID_REMOTE", True)
    calls = _touchid_binary(monkeypatch, '{"authenticated": false, "error": "cancelled"}')
    client = TestClient(app, client=("127.0.0.1", 5000))
    codes = [client.post("/api/auth/verify", headers=TUNNEL).status_code
             for _ in range(auth._TOUCHID_MAX_PROMPTS + 1)]
    assert codes == [200] * auth._TOUCHID_MAX_PROMPTS + [429]
    assert len(calls) == auth._TOUCHID_MAX_PROMPTS  # the extra request never prompts
