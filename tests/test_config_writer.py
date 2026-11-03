"""One safe writer for config.json (P-054, docs/CONFIG-WRITER-DESIGN.md).

codec_config.update_config holds the config lock across the read, the change and
the atomic write, and refuses (writing nothing) a config.json that exists but is
unreadable, not a JSON object or empty. Settings, heartbeat and the TOTP routes
use it. Every test points each module's CONFIG_PATH at a tmp_path file: never
the real ~/.codec/config.json, never a fake HOME.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import threading
import time
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import codec_config
import codec_jsonstore
import routes._shared as shared
import routes.auth as auth_routes
import routes.config as rc
import routes.heartbeat as hb

GOOD = {"agent_name": "C", "llm_model": "local-a", "tts_voice": "am_adam", "config_version": 1}
BAD_FILES = {
    "invalid_json": b'{"llm_model": "local-a", ',
    "empty_file": b"",
    "empty_object": b"{}",
    "a_list": b"[1, 2]",
    "not_utf8": b'{"a": "\xff\xfe"}',
}


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(GOOD, indent=2))
    for mod in (codec_config, rc, hb, auth_routes, shared):
        monkeypatch.setattr(mod, "CONFIG_PATH", str(path))
    return path


@pytest.fixture
def client(cfg):
    app = FastAPI()
    for mod in (rc, hb, auth_routes):
        app.include_router(mod.router)
    return TestClient(app)


def _saved(path):
    return json.loads(path.read_text())


# ── update_config ──────────────────────────────────────────────────────────

def test_a_change_keeps_the_other_keys_and_returns_its_result(cfg):
    def change(c):
        c["tts_voice"] = "bf_emma"
        return "done"
    assert codec_config.update_config(change) == "done"   # default path: codec_config.CONFIG_PATH
    assert _saved(cfg) == {**GOOD, "tts_voice": "bf_emma"}
    assert os.stat(cfg).st_mode & 0o777 == 0o600


def test_a_missing_file_starts_from_empty(tmp_path):
    path = tmp_path / "config.json"
    codec_config.update_config(lambda c: c.update(agent_name="C"), path=path)
    assert _saved(path) == {"agent_name": "C"}


@pytest.mark.parametrize("content", list(BAD_FILES.values()), ids=list(BAD_FILES))
def test_an_unreadable_file_is_refused_and_left_byte_for_byte(cfg, content):
    cfg.write_bytes(content)
    called = []
    with pytest.raises(codec_config.ConfigUnreadable):
        codec_config.update_config(called.append)
    assert cfg.read_bytes() == content
    assert called == [], "the change never runs on an unreadable file"
    assert sorted(p.name for p in cfg.parent.iterdir()) == ["config.json", "config.json.lock"]


def test_a_change_that_raises_writes_nothing(cfg):
    before = cfg.read_bytes()

    def change(c):
        c["llm_model"] = "half-done"
        raise RuntimeError("stop")
    with pytest.raises(RuntimeError):
        codec_config.update_config(change)
    assert cfg.read_bytes() == before


def test_the_version_stamp_write_does_not_share_a_fixed_tmp_name(cfg):
    other = cfg.parent / "config.json.tmp"
    other.write_text("another writer's half-written file")
    assert codec_config._write_config_atomic({**GOOD, "k": 2}) is True
    assert other.read_text() == "another writer's half-written file"
    assert _saved(cfg)["k"] == 2 and os.stat(cfg).st_mode & 0o777 == 0o600


# ── The routes ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url,body", [
    ("/api/config", {"tts": {"tts_voice": "bf_emma"}}),
    ("/api/config/raw", {"config": {"tts_voice": "bf_emma"}}),
    ("/api/heartbeat/alerts", {"alerts": [{"name": "disk"}]}),
])
def test_a_save_on_an_unreadable_file_answers_409_and_changes_nothing(client, cfg, url, body):
    bad = BAD_FILES["invalid_json"]
    cfg.write_bytes(bad)
    r = client.put(url, json=body)
    assert r.status_code == 409 and "nothing was saved" in r.json()["error"]
    assert cfg.read_bytes() == bad


def test_settings_and_heartbeat_saves_still_merge(client, cfg):
    assert client.put("/api/config", json={"tts": {"tts_voice": "bf_emma"}}).status_code == 200
    assert client.put("/api/heartbeat/alerts", json={"alerts": [{"name": "disk"}]}).json()["saved"]
    assert _saved(cfg) == {**GOOD, "tts_voice": "bf_emma", "heartbeat_alerts": [{"name": "disk"}]}


@pytest.fixture
def totp(client, monkeypatch):
    """The TOTP routes with the login gate open and nothing written outside tmp_path."""
    pyotp = pytest.importorskip("pyotp")
    monkeypatch.setattr(shared, "AUTH_ENABLED", False)
    monkeypatch.setattr(auth_routes, "_save_sessions", lambda: None)
    monkeypatch.setattr(auth_routes, "_audit_event", lambda *a, **k: None)
    secret = pyotp.random_base32()
    token = "test-session-" + secrets.token_hex(8)
    with shared._auth_lock:
        shared._auth_sessions[token] = {"created": datetime.now(), "ip": "127.0.0.1",
                                        "method": "test", "pending_totp_secret": secret}
    client.cookies.set(shared.AUTH_COOKIE_NAME, token)
    try:
        yield client, secret, lambda: pyotp.TOTP(secret).now()
    finally:
        with shared._auth_lock:
            shared._auth_sessions.pop(token, None)


def test_totp_confirm_on_an_unreadable_file_saves_nothing(totp, cfg):
    client, _secret, code = totp
    bad = BAD_FILES["invalid_json"]
    cfg.write_bytes(bad)
    r = client.post("/api/auth/totp/confirm", json={"code": code()})
    assert r.status_code == 500 and "nothing was saved" in r.json()["error"]
    assert cfg.read_bytes() == bad


def test_totp_disable_and_enable_keep_the_other_keys(totp, cfg):
    client, secret, code = totp
    cfg.write_text(json.dumps({**GOOD, "totp_secret": secret}))
    assert client.post("/api/auth/totp/disable", json={"code": code()}).json() == {"disabled": True}
    assert _saved(cfg) == {**GOOD, "totp_secret": secret, "totp_disabled": True}
    assert client.post("/api/auth/totp/enable", json={"code": code()}).json() == {"enabled": True}
    assert _saved(cfg) == {**GOOD, "totp_secret": secret}


# ── Two writers at once ────────────────────────────────────────────────────

class _Req:
    """What the two save handlers read from a request."""

    def __init__(self, body):
        self._body = body
        self.headers = {}
        self.client = None

    async def json(self):
        return self._body


def test_two_threads_a_settings_save_and_a_heartbeat_save_keep_both_keys(cfg, monkeypatch):
    real_write = codec_jsonstore.atomic_write_json
    settings_inside = threading.Event()

    def slow_write(path, data, **kw):
        if threading.current_thread().name == "settings":
            settings_inside.set()   # Settings has read the file and holds the lock
            time.sleep(0.3)         # the heartbeat save starts now and has to wait
        return real_write(path, data, **kw)

    monkeypatch.setattr(codec_jsonstore, "atomic_write_json", slow_write)
    results = {}

    def settings():
        results["settings"] = asyncio.run(rc.update_config(_Req({"tts": {"tts_voice": "bf_emma"}})))

    def heartbeat():
        if not settings_inside.wait(5):
            results["heartbeat"] = "the settings save never reached its write"
            return
        results["heartbeat"] = asyncio.run(hb.update_heartbeat_alerts(_Req({"alerts": [{"name": "disk"}]})))

    threads = [threading.Thread(target=settings, name="settings"),
               threading.Thread(target=heartbeat, name="heartbeat")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert results["settings"]["saved"] is True, results
    assert results["heartbeat"]["saved"] is True, results
    saved = _saved(cfg)
    assert saved["tts_voice"] == "bf_emma" and saved["heartbeat_alerts"] == [{"name": "disk"}]
    assert saved["llm_model"] == "local-a"
