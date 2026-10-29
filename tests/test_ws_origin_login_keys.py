"""P-005: Origin check on /ws/voice, and login keys protected in the config routes.

The config tests point CONFIG_PATH at a temp file, never ~/.codec/config.json.
"""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import routes.config as config_routes
import routes.websocket as ws


class _FakeWS:
    def __init__(self, headers):
        self.headers = headers


HOSTILE = [
    "null",
    "https://evil.example",
    "https://evil.avadigital.ai",
    "file://",
    "http://127.0.0.1:5300",
    "http://",
]


@pytest.mark.parametrize("origin", HOSTILE)
def test_hostile_origin_closed_before_accept(monkeypatch, origin):
    monkeypatch.setattr(ws, "_ws_authorized", lambda _w: True)
    app = FastAPI()
    app.include_router(ws.router)
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as e:
            with client.websocket_connect("/ws/voice", headers={"origin": origin}):
                pass
    assert e.value.code == 4403


def test_missing_origin_is_allowed():
    assert ws._ws_origin_ok(_FakeWS({"host": "127.0.0.1:8090"})) is True


def test_same_host_origin_is_allowed():
    h = {"host": "127.0.0.1:8090", "origin": "http://127.0.0.1:8090"}
    assert ws._ws_origin_ok(_FakeWS(h)) is True


def test_trusted_tunnel_origin_is_allowed():
    h = {"host": "127.0.0.1:8090", "origin": "https://codec.avadigital.ai"}
    assert ws._ws_origin_ok(_FakeWS(h)) is True


# --- config routes -------------------------------------------------------------

REMOTE = {"cf-connecting-ip": "203.0.113.9"}


@pytest.fixture
def cfg_client(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"auth_enabled": True, "auth_session_hours": 24,
                                "dashboard_token": "tok_original_1234",
                                "auth_pin_hash": "argon2-original"}, indent=2))
    monkeypatch.setattr(config_routes, "CONFIG_PATH", str(path))
    app = FastAPI()
    app.include_router(config_routes.router)
    return TestClient(app), path


@pytest.mark.parametrize("headers", [{}, REMOTE])
def test_pin_hash_refused_for_every_caller(cfg_client, headers):
    client, path = cfg_client
    before = path.read_bytes()
    r = client.put("/api/config", json={"auth": {"auth_pin_hash": "attacker"}}, headers=headers)
    assert r.status_code == 403
    assert path.read_bytes() == before


@pytest.mark.parametrize("key,value", [
    ("auth_enabled", False), ("auth_session_hours", 720), ("dashboard_token", "tok_new_value"),
])
def test_login_keys_refused_remotely(cfg_client, key, value):
    client, path = cfg_client
    before = path.read_bytes()
    r = client.put("/api/config", json={"auth": {key: value}}, headers=REMOTE)
    assert r.status_code == 403
    assert path.read_bytes() == before


@pytest.mark.parametrize("key,value", [("auth_enabled", False), ("auth_session_hours", 48)])
def test_login_keys_still_change_from_this_mac(cfg_client, key, value):
    client, path = cfg_client
    r = client.put("/api/config", json={"auth": {key: value}})
    assert r.status_code == 200, r.text
    assert json.loads(path.read_text())[key] == value


def test_masked_or_unchanged_login_keys_pass_remotely(cfg_client):
    client, path = cfg_client
    r = client.put("/api/config", json={"auth": {"auth_enabled": True,
                                                 "dashboard_token": "*************1234"}},
                   headers=REMOTE)
    assert r.status_code == 200, r.text


def test_raw_editor_refuses_login_keys_remotely(cfg_client):
    client, path = cfg_client
    before = path.read_bytes()
    r = client.put("/api/config/raw", json={"config": {"auth_session_hours": 720}}, headers=REMOTE)
    assert r.status_code == 403
    assert path.read_bytes() == before
