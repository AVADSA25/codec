"""Test dashboard endpoints — requires codec_dashboard.py running on :8090"""
import pytest
import requests

BASE = "http://localhost:8090"


def _dashboard_running():
    try:
        requests.get(f"{BASE}/", timeout=2)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _dashboard_running(),
    reason="Dashboard not running on localhost:8090"
)


def test_dashboard_home():
    r = requests.get(f"{BASE}/", timeout=5)
    assert r.status_code == 200


def test_chat_page():
    r = requests.get(f"{BASE}/chat", timeout=5)
    assert r.status_code == 200


def test_vibe_page():
    r = requests.get(f"{BASE}/vibe", timeout=5)
    assert r.status_code == 200


def test_voice_page():
    r = requests.get(f"{BASE}/voice", timeout=5)
    assert r.status_code == 200


def test_memory_search_api():
    r = requests.get(f"{BASE}/api/memory/search?q=test", timeout=5)
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, (list, dict))  # API returns list or dict depending on endpoint


def test_memory_sessions_api():
    r = requests.get(f"{BASE}/api/memory/sessions?limit=5", timeout=5)
    assert r.status_code == 200


def test_forge_requires_input():
    r = requests.post(f"{BASE}/api/forge", json={}, timeout=5)
    assert r.status_code in [400, 422]
