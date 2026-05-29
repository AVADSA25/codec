"""N1 (re-audit, Critical): the /ws/voice WebSocket must enforce the same auth
gate as HTTP endpoints. Starlette's BaseHTTPMiddleware (which AuthMiddleware
subclasses) never runs on the websocket scope, so the handshake was previously
unauthenticated — exposing the voice→skill pipeline (terminal/file_ops/pilot)
to anyone who can reach the host when the dashboard is exposed.

We test the pure gate `routes.websocket._ws_authorized` against fake WS objects.
"""
from datetime import datetime

import pytest

import routes._shared as shared
import routes.websocket as ws


class _FakeWS:
    def __init__(self, cookies=None, query=None, headers=None):
        self.cookies = cookies or {}
        self.query_params = query or {}
        self.headers = headers or {}


@pytest.fixture(autouse=True)
def _reset_auth(monkeypatch):
    # Default: nothing configured. Individual tests opt into token/biometric.
    monkeypatch.setattr(shared, "AUTH_ENABLED", False, raising=False)
    monkeypatch.setattr(shared, "_auth_available", lambda: False, raising=False)
    monkeypatch.setattr("codec_config.get_dashboard_token", lambda: "", raising=False)
    yield


def test_open_when_nothing_configured():
    # Loopback dev posture: no token, no biometric → allow (matches HTTP Layer 0).
    assert ws._ws_authorized(_FakeWS()) is True


def test_biometric_enabled_rejects_without_session(monkeypatch):
    monkeypatch.setattr(shared, "AUTH_ENABLED", True, raising=False)
    monkeypatch.setattr(shared, "_auth_available", lambda: True, raising=False)
    assert ws._ws_authorized(_FakeWS()) is False


def test_biometric_enabled_accepts_valid_session_cookie(monkeypatch):
    monkeypatch.setattr(shared, "AUTH_ENABLED", True, raising=False)
    monkeypatch.setattr(shared, "_auth_available", lambda: True, raising=False)
    monkeypatch.setattr(shared, "_is_totp_enabled", lambda: False, raising=False)
    tok = "sess_valid"
    monkeypatch.setitem(shared._auth_sessions, tok, {"created": datetime.now()})
    cookie = _FakeWS(cookies={shared.AUTH_COOKIE_NAME: tok})
    assert ws._ws_authorized(cookie) is True


def test_biometric_enabled_rejects_when_totp_unverified(monkeypatch):
    monkeypatch.setattr(shared, "AUTH_ENABLED", True, raising=False)
    monkeypatch.setattr(shared, "_auth_available", lambda: True, raising=False)
    monkeypatch.setattr(shared, "_is_totp_enabled", lambda: True, raising=False)
    tok = "sess_no_totp"
    monkeypatch.setitem(shared._auth_sessions, tok, {"created": datetime.now()})  # no totp_verified
    assert ws._ws_authorized(_FakeWS(cookies={shared.AUTH_COOKIE_NAME: tok})) is False


def test_token_configured_requires_match(monkeypatch):
    monkeypatch.setattr("codec_config.get_dashboard_token", lambda: "secrettoken", raising=False)
    assert ws._ws_authorized(_FakeWS()) is False
    assert ws._ws_authorized(_FakeWS(query={"token": "wrong"})) is False
    assert ws._ws_authorized(_FakeWS(query={"token": "secrettoken"})) is True
    # also via Authorization header
    assert ws._ws_authorized(_FakeWS(headers={"authorization": "Bearer secrettoken"})) is True
