"""N5 (re-audit, High): the ?s= query-param session fallback in AuthMiddleware
skipped the TOTP-verified check (unlike the cookie path via
_verify_biometric_session), so a pre-TOTP / stolen-pre-TOTP token could reach
any GET /api endpoint by appending ?s=<token>, bypassing 2FA.

Fix: both the cookie path and the ?s= path route through one
_session_token_valid() that enforces existence + age + TOTP. Unit-tested here.
"""
from datetime import datetime, timedelta

import pytest

import routes._shared as shared


@pytest.fixture(autouse=True)
def _clean_sessions(monkeypatch):
    monkeypatch.setattr(shared, "_auth_sessions", {}, raising=False)
    monkeypatch.setattr(shared, "_save_sessions", lambda: None, raising=False)
    yield


def test_missing_token_invalid():
    assert shared._session_token_valid("") is False
    assert shared._session_token_valid(None) is False


def test_fresh_session_no_totp_valid(monkeypatch):
    monkeypatch.setattr(shared, "_is_totp_enabled", lambda: False, raising=False)
    shared._auth_sessions["t"] = {"created": datetime.now()}
    assert shared._session_token_valid("t") is True


def test_totp_enabled_unverified_invalid(monkeypatch):
    monkeypatch.setattr(shared, "_is_totp_enabled", lambda: True, raising=False)
    shared._auth_sessions["t"] = {"created": datetime.now()}  # no totp_verified
    assert shared._session_token_valid("t") is False, "?s= path must enforce TOTP (N5)"


def test_totp_enabled_verified_valid(monkeypatch):
    monkeypatch.setattr(shared, "_is_totp_enabled", lambda: True, raising=False)
    shared._auth_sessions["t"] = {"created": datetime.now(), "totp_verified": True}
    assert shared._session_token_valid("t") is True


def test_expired_session_invalid(monkeypatch):
    monkeypatch.setattr(shared, "_is_totp_enabled", lambda: False, raising=False)
    old = datetime.now() - timedelta(hours=shared.AUTH_SESSION_HOURS + 1)
    shared._auth_sessions["t"] = {"created": old}
    assert shared._session_token_valid("t") is False
