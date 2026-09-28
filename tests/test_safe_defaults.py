"""Safe defaults (audit 2026-09-27, item 3).

Host allowlist against DNS rebinding, the voice WebSocket and /api/run_code
limited to this Mac, a timed-out program killed with everything it started,
and an installer that asks for a login by default.
"""
import hashlib
import os
import stat
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import codec_config
import codec_dashboard
import routes._shared as shared
import routes.auth as auth
import routes.vibe_exec as vibe_exec
import routes.websocket as ws_routes
import setup_codec
from codec_dashboard import app

TUNNEL = {"cf-connecting-ip": "203.0.113.9"}
REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def no_login(monkeypatch):
    monkeypatch.setattr(codec_config, "get_dashboard_token", lambda: "")
    monkeypatch.setattr(codec_dashboard, "AUTH_ENABLED", False)
    monkeypatch.setattr(shared, "AUTH_ENABLED", False)


@pytest.fixture
def pin_session(monkeypatch):
    """A logged-in PIN session; returns a client carrying its cookies."""
    pin_hash = hashlib.sha256(b"4821").hexdigest()
    monkeypatch.setattr(codec_config, "get_dashboard_token", lambda: "")
    monkeypatch.setattr(codec_dashboard, "AUTH_ENABLED", True)
    monkeypatch.setattr(shared, "AUTH_ENABLED", True)
    monkeypatch.setattr(shared, "AUTH_PIN_HASH", pin_hash)
    monkeypatch.setattr(auth, "AUTH_PIN_HASH", pin_hash)
    monkeypatch.setattr(shared, "_is_totp_enabled", lambda: False)
    for mod in (auth, shared):
        monkeypatch.setattr(mod, "_save_sessions", lambda: None)
    monkeypatch.setattr(auth, "_upgrade_pin_hash", lambda pin: None)
    monkeypatch.setattr(auth, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(auth, "_audit_event", lambda *a, **k: None)
    monkeypatch.setattr(auth, "_global_fails", [])
    monkeypatch.setattr(auth, "_pin_attempts", {})
    client = TestClient(app, client=("127.0.0.1", 5000))
    assert client.post("/api/auth/pin", json={"pin": "4821"}).json()["authenticated"] is True
    return client


# ── Host allowlist ──────────────────────────────────────────────────────

def test_host_foreign_name_is_rejected():
    r = TestClient(app).get("/api/health", headers={"host": "rebind.evil.example:8090"})
    assert r.status_code == 400


def test_host_local_names_ips_and_tunnel_hosts_pass():
    client = TestClient(app)
    for host in ("localhost:8090", "127.0.0.1:8090", "[::1]:8090", "192.168.1.20:8090",
                 "codec.avadigital.ai", "codec.lucyvpa.com"):
        assert client.get("/api/health", headers={"host": host}).status_code == 200, host


def test_host_this_macs_own_name_passes_for_lan_setups():
    import socket
    own = socket.gethostname().lower().removesuffix(".local")
    for name in (own, own + ".local"):
        assert name in codec_dashboard._ALLOWED_HOST_NAMES


def test_host_config_public_hosts_are_added(monkeypatch):
    monkeypatch.setattr(codec_config, "cfg", {"dashboard_public_hosts": ["Mine.Example"]})
    assert "mine.example" in codec_dashboard._public_host_names()
    monkeypatch.setattr(codec_dashboard, "_ALLOWED_HOST_NAMES", {"localhost", "mine.example"})
    assert TestClient(app).get("/api/health", headers={"host": "mine.example"}).status_code == 200


def test_host_foreign_name_cannot_open_the_websocket(no_login):
    with pytest.raises(Exception):
        with TestClient(app).websocket_connect("/ws/voice", headers={"host": "rebind.evil.example"}):
            pass


# ── WebSocket with no login configured ─────────────────────────────────

class _FakeWS:
    def __init__(self, peer, headers):
        self.client = type("C", (), {"host": peer})()
        self.headers = headers
        self.query_params = {}
        self.cookies = {}


def test_websocket_no_login_refuses_tunnel_and_accepts_this_mac(no_login):
    assert ws_routes._ws_authorized(_FakeWS("127.0.0.1", {})) is True
    assert ws_routes._ws_authorized(_FakeWS("127.0.0.1", TUNNEL)) is False
    assert ws_routes._ws_authorized(_FakeWS("192.168.1.30", {})) is False


# ── /api/run_code ───────────────────────────────────────────────────────

def test_run_code_refuses_tunnel_requests(pin_session):
    csrf = pin_session.cookies.get("codec_csrf")
    r = pin_session.post("/api/run_code", json={"code": "echo hi", "language": "bash"},
                         headers={**TUNNEL, "x-csrf-token": csrf})
    assert r.status_code == 403
    assert "only on this Mac" in r.json()["error"]


def test_run_code_timeout_kills_the_program_and_its_children(no_login, monkeypatch, tmp_path):
    monkeypatch.setattr(vibe_exec, "_RUNCODE_TIMEOUT_S", 2)
    pidfile = tmp_path / "pids"
    code = f'sleep 120 &\necho "$$ $!" > {pidfile}\nsleep 120\n'
    r = TestClient(app).post("/api/run_code", json={"code": code, "language": "bash"})
    assert r.status_code == 200 and r.json()["exit_code"] == -1
    pids = [int(p) for p in pidfile.read_text().split()]
    assert len(pids) == 2
    deadline = time.time() + 5
    alive = pids
    while alive and time.time() < deadline:
        alive = [p for p in pids if _running(p)]
        time.sleep(0.1)
    assert alive == [], f"still running after timeout: {alive}"


def _running(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie (exited, not yet reaped by launchd) no longer runs.
    import subprocess
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout
    return bool(state.strip()) and not state.strip().startswith("Z")


# ── installer ───────────────────────────────────────────────────────────

def test_setup_asks_for_a_login_by_default():
    assert setup_codec.DEFAULT_AUTH_CHOICE == "Both Touch ID + PIN"
    assert setup_codec.DEFAULT_AUTH_CHOICE in setup_codec.AUTH_CHOICES
    assert "None" in setup_codec.AUTH_CHOICES  # still available on purpose
    src = (REPO / "setup_codec.py").read_text()
    assert 'ask("Authentication method:", AUTH_CHOICES, default=DEFAULT_AUTH_CHOICE)' in src


def test_setup_saves_config_for_this_user_only(tmp_path):
    fresh = tmp_path / "config.json"
    setup_codec.save_config({"a": 1}, str(fresh))
    assert stat.S_IMODE(fresh.stat().st_mode) == 0o600
    old = tmp_path / "old.json"
    old.write_text("{}")
    old.chmod(0o644)
    setup_codec.save_config({"b": 2}, str(old))
    assert stat.S_IMODE(old.stat().st_mode) == 0o600 and '"b": 2' in old.read_text()


def test_setup_calls_the_token_an_api_token_not_a_login():
    src = (REPO / "setup_codec.py").read_text()
    assert "API token for scripts" in src
    assert "Leave blank for no auth" not in src
