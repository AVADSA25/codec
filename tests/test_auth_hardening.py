"""Dashboard auth hardening (audit 2026-09-27): API docs/metrics not public,
no-auth mode limited to this Mac, cross-site POST blocked, per-visitor PIN
lockout behind the tunnel, global failure cap, argon2 PIN upgrade, and the
chat link sink escaping quotes."""
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import codec_dashboard
import routes.auth as auth
from codec_dashboard import AuthMiddleware, app

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def no_auth(monkeypatch):
    import codec_config
    monkeypatch.setattr(codec_config, "get_dashboard_token", lambda: "")
    monkeypatch.setattr(codec_dashboard, "AUTH_ENABLED", False)
    return TestClient(app)


@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr(auth, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(auth, "_audit_event", lambda *a, **k: None)
    monkeypatch.setattr(auth, "_save_sessions", lambda: None)
    monkeypatch.setattr(auth, "_global_fails", [])
    monkeypatch.setattr(auth, "_pin_attempts", {})


def test_api_docs_and_metrics_are_not_public():
    for p in ("/docs", "/redoc", "/openapi.json", "/metrics"):
        assert p not in AuthMiddleware.PUBLIC_ROUTES


def test_no_auth_mode_serves_this_mac_but_refuses_tunnel(no_auth):
    assert no_auth.get("/openapi.json").status_code == 200
    r = no_auth.get("/openapi.json", headers={"cf-connecting-ip": "203.0.113.9"})
    assert r.status_code == 403


def test_cross_site_post_blocked(no_auth):
    r = no_auth.post("/api/config", json={}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    r = no_auth.post("/api/config", json={}, headers={"origin": "http://testserver"})
    assert r.status_code != 403 or r.json().get("error") != "Cross-site request blocked"


class _Req:
    def __init__(self, peer, headers):
        self.client = type("C", (), {"host": peer})()
        self.headers = headers


def test_client_key_uses_cloudflare_ip_only_from_loopback_peer():
    assert auth._client_key(_Req("127.0.0.1", {"cf-connecting-ip": "198.51.100.7"})) == "198.51.100.7"
    assert auth._client_key(_Req("192.168.1.20", {"cf-connecting-ip": "198.51.100.7"})) == "192.168.1.20"
    assert auth._client_key(_Req("127.0.0.1", {})) == "127.0.0.1"


def test_global_failure_cap_blocks_even_correct_pin(monkeypatch, quiet):
    monkeypatch.setattr(auth, "AUTH_PIN_HASH", hashlib.sha256(b"4821").hexdigest())
    monkeypatch.setattr(auth, "_global_fails", [time.time()] * auth._GLOBAL_FAIL_MAX)
    r = TestClient(app).post("/api/auth/pin", json={"pin": "4821"})
    assert r.status_code == 429


def test_lockout_is_per_visitor_behind_tunnel(monkeypatch, quiet):
    monkeypatch.setattr(auth, "AUTH_PIN_HASH", hashlib.sha256(b"4821").hexdigest())
    monkeypatch.setattr(auth, "_upgrade_pin_hash", lambda pin: None)
    c = TestClient(app, client=("127.0.0.1", 5000))
    for _ in range(5):
        c.post("/api/auth/pin", json={"pin": "0000"}, headers={"cf-connecting-ip": "203.0.113.9"})
    r = c.post("/api/auth/pin", json={"pin": "4821"}, headers={"cf-connecting-ip": "203.0.113.9"})
    assert r.status_code == 429  # attacker's visitor key is locked
    r = c.post("/api/auth/pin", json={"pin": "4821"}, headers={"cf-connecting-ip": "198.51.100.7"})
    assert r.status_code == 200 and r.json()["authenticated"] is True  # owner is not


def test_correct_pin_upgrades_legacy_hash_to_argon2(monkeypatch, tmp_path, quiet):
    pytest.importorskip("argon2")
    cfg = tmp_path / "config.json"
    legacy = hashlib.sha256(b"4821").hexdigest()
    cfg.write_text(json.dumps({"auth_pin_hash": legacy, "keep": 1}))
    monkeypatch.setattr(auth, "CONFIG_PATH", str(cfg))
    monkeypatch.setattr(auth, "AUTH_PIN_HASH", legacy)
    r = TestClient(app).post("/api/auth/pin", json={"pin": "4821"})
    assert r.json()["authenticated"] is True
    saved = json.loads(cfg.read_text())
    assert saved["auth_pin_hash"].startswith("$argon2") and saved["keep"] == 1
    from codec_pinhash import verify_pin
    assert verify_pin("4821", saved["auth_pin_hash"])


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_chat_escape_keeps_quotes_inside_href():
    src = (REPO / "codec_chat.html").read_text()
    esc = src[src.index("function escHtml"):].split("\n", 1)[0]
    js = ("var document={createElement:function(){var o={};Object.defineProperty(o,'textContent',"
          "{set:function(v){o.innerHTML=String(v).replace(/&/g,'&amp;').replace(/</g,'&lt;')"
          ".replace(/>/g,'&gt;')}});return o}};" + esc +
          ";var f=escHtml('[x](https://a.test/\"onmouseover=\"alert(1))');"
          "console.log(f.indexOf('\"onmouseover')<0?'SAFE':'VULN')")
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=10).stdout
    assert "SAFE" in out
