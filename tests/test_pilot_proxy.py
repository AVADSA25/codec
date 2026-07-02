"""Tests for the Pilot proxy token injection (PP-1 auth handshake).

The dashboard proxy must inject x-pilot-token (read server-side from
~/.codec/pilot_token) into every upstream request — the browser never
sees the token. Missing/unreadable token file → empty header (pilot-runner
fail-closes with 401, proxy must not crash).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from routes import pilot_proxy  # noqa: E402


def test_pilot_token_read_and_stripped(tmp_path, monkeypatch):
    tok = tmp_path / "pilot_token"
    tok.write_text("  secret-token-value\n")
    monkeypatch.setattr(pilot_proxy, "_TOKEN_PATH", str(tok))
    assert pilot_proxy._pilot_token() == "secret-token-value"


def test_pilot_token_missing_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(pilot_proxy, "_TOKEN_PATH", str(tmp_path / "nope"))
    assert pilot_proxy._pilot_token() == ""


def test_headers_always_include_token(tmp_path, monkeypatch):
    tok = tmp_path / "pilot_token"
    tok.write_text("tok123")
    monkeypatch.setattr(pilot_proxy, "_TOKEN_PATH", str(tok))
    h = pilot_proxy._build_headers(None)
    assert h["x-pilot-token"] == "tok123"
    assert "content-type" not in h
    h2 = pilot_proxy._build_headers("application/json")
    assert h2["x-pilot-token"] == "tok123"
    assert h2["content-type"] == "application/json"


def test_stream_paths_cover_mjpeg():
    assert "screenshot/stream" in pilot_proxy._STREAM_PATHS
