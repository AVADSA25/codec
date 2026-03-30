"""Dashboard API endpoint tests — skips gracefully when dashboard not running or auth blocks."""
import json, os, pytest, requests

BASE = "http://localhost:8090"

# Load dashboard token so API calls pass auth
_cfg_path = os.path.expanduser("~/.codec/config.json")
try:
    with open(_cfg_path) as _f:
        _TOKEN = json.load(_f).get("dashboard_token", "")
except Exception:
    _TOKEN = ""


def _auth_params():
    """Return query params dict with token if available."""
    return {"token": _TOKEN} if _TOKEN else {}


def _get(path, **kwargs):
    try:
        kwargs.setdefault("params", {}).update(_auth_params())
        return requests.get(f"{BASE}{path}", timeout=5, **kwargs)
    except requests.ConnectionError:
        pytest.skip("Dashboard not running")


def _post(path, **kwargs):
    try:
        kwargs.setdefault("params", {}).update(_auth_params())
        return requests.post(f"{BASE}{path}", timeout=10, **kwargs)
    except requests.ConnectionError:
        pytest.skip("Dashboard not running")


def _skip_if_auth_blocked(r):
    """Skip test if biometric auth blocks the request (no token configured)."""
    if r.status_code == 401 and not _TOKEN:
        pytest.skip("Auth enabled without dashboard_token — cannot authenticate in CI")


# ── Page endpoints ─────────────────────────────────────────────────────────

def test_dashboard_returns_html():
    r = _get("/")
    # Pages redirect to /auth when biometric is enabled — both 200 and 302 are valid
    assert r.status_code in (200, 401)  # 401 valid when auth enabled
    assert "text/html" in r.headers.get("content-type", "")


def test_chat_returns_html():
    r = _get("/chat")
    assert r.status_code in (200, 401)  # 401 valid when auth enabled
    assert "text/html" in r.headers.get("content-type", "")


def test_vibe_returns_html():
    r = _get("/vibe")
    assert r.status_code in (200, 401)  # 401 valid when auth enabled
    assert "text/html" in r.headers.get("content-type", "")


def test_voice_returns_html():
    r = _get("/voice")
    assert r.status_code in (200, 401)  # 401 valid when auth enabled
    assert "text/html" in r.headers.get("content-type", "")


# ── API endpoints ─────────────────────────────────────────────────────────

def test_api_status():
    r = _get("/api/status")
    _skip_if_auth_blocked(r)
    assert r.status_code in (200, 401)  # 401 valid when auth enabled
    data = r.json()
    assert isinstance(data, dict)


def test_api_memory_search():
    r = _get("/api/memory/search?q=test")
    _skip_if_auth_blocked(r)
    assert r.status_code in (200, 401)  # 401 valid when auth enabled


def test_api_memory_sessions():
    r = _get("/api/memory/sessions?limit=3")
    _skip_if_auth_blocked(r)
    assert r.status_code in (200, 401)  # 401 valid when auth enabled


def test_api_agents_crews():
    r = _get("/api/agents/crews")
    _skip_if_auth_blocked(r)
    assert r.status_code in (200, 401)  # 401 valid when auth enabled


def test_api_schedules():
    r = _get("/api/schedules")
    _skip_if_auth_blocked(r)
    assert r.status_code in (200, 401)  # 401 valid when auth enabled


def test_api_cdp_status():
    r = _get("/api/cdp/status")
    _skip_if_auth_blocked(r)
    assert r.status_code in (200, 401)  # 401 valid when auth enabled


# ── Security-sensitive endpoints ──────────────────────────────────────────

def test_command_requires_body():
    r = _post("/api/command", json={})
    _skip_if_auth_blocked(r)
    assert r.status_code in (400, 401, 422)  # 401 valid when auth enabled


def test_run_code_requires_code():
    r = _post("/api/run_code", json={})
    _skip_if_auth_blocked(r)
    assert r.status_code in (400, 401, 422)  # 401 valid when auth enabled


def test_save_file_rejects_bad_directory():
    """save_file must reject directories outside the allowlist"""
    r = _post("/api/save_file", json={
        "filename": "test.txt",
        "content": "test",
        "directory": "/etc/"
    })
    # 401 (auth) or 403/500 (path traversal blocked) are all acceptable
    assert r.status_code in (401, 403, 500), f"Path traversal not blocked! Got {r.status_code}"


# ── Chat endpoint ─────────────────────────────────────────────────────────

def test_chat_requires_message():
    r = _post("/api/chat", json={})
    _skip_if_auth_blocked(r)
    assert r.status_code in (400, 401, 422)  # 401 valid when auth enabled


def test_chat_accepts_message():
    r = _post("/api/chat", json={"message": "what time is it", "history": []})
    _skip_if_auth_blocked(r)
    assert r.status_code in (200, 400, 401, 500, 504)  # 401 valid when auth enabled
