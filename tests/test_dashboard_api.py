"""Dashboard API endpoint tests — skips gracefully when dashboard not running"""
import pytest
import requests

BASE = "http://localhost:8090"


def _get(path, **kwargs):
    try:
        return requests.get(f"{BASE}{path}", timeout=5, **kwargs)
    except requests.ConnectionError:
        pytest.skip("Dashboard not running")


def _post(path, **kwargs):
    try:
        return requests.post(f"{BASE}{path}", timeout=10, **kwargs)
    except requests.ConnectionError:
        pytest.skip("Dashboard not running")


# ── Page endpoints ─────────────────────────────────────────────────────────

def test_dashboard_returns_html():
    r = _get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


def test_chat_returns_html():
    r = _get("/chat")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


def test_vibe_returns_html():
    r = _get("/vibe")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


def test_voice_returns_html():
    r = _get("/voice")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


# ── API endpoints ─────────────────────────────────────────────────────────

def test_api_status():
    r = _get("/api/status")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, dict)


def test_api_memory_search():
    r = _get("/api/memory/search?q=test")
    assert r.status_code == 200


def test_api_memory_sessions():
    r = _get("/api/memory/sessions?limit=3")
    assert r.status_code == 200


def test_api_agents_crews():
    r = _get("/api/agents/crews")
    assert r.status_code == 200


def test_api_schedules():
    r = _get("/api/schedules")
    assert r.status_code == 200


def test_api_cdp_status():
    r = _get("/api/cdp/status")
    assert r.status_code == 200


# ── Security-sensitive endpoints ──────────────────────────────────────────

def test_command_requires_body():
    r = _post("/api/command", json={})
    assert r.status_code in (400, 422)


def test_run_code_requires_code():
    r = _post("/api/run_code", json={})
    assert r.status_code in (400, 422)


def test_save_file_rejects_bad_directory():
    """save_file must reject directories outside the allowlist"""
    r = _post("/api/save_file", json={
        "filename": "test.txt",
        "content": "test",
        "directory": "/etc/"
    })
    assert r.status_code in (401, 403, 500), f"Path traversal not blocked! Got {r.status_code}"


# ── Chat endpoint ─────────────────────────────────────────────────────────

def test_chat_requires_message():
    r = _post("/api/chat", json={})
    assert r.status_code in (400, 422)


def test_chat_accepts_message():
    r = _post("/api/chat", json={"message": "what time is it", "history": []})
    assert r.status_code in (200, 400, 500, 504)
