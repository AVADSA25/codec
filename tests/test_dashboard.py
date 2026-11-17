"""Test dashboard endpoints — requires codec_dashboard.py running on :8090"""
import pytest
import requests

BASE = "http://localhost:8090"


def _skip_if_auth(r):
    """Skip when the running dashboard asks for a login: these requests carry none.

    The suite never reads the owner's config.json (docs/TEST-CONFIG-ISOLATION-DESIGN.md),
    so there is no dashboard_token to send."""
    if r.status_code == 401:
        pytest.skip("The running dashboard asks for a login")


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
    assert r.status_code in (200, 401)  # 401 valid when auth enabled


def test_chat_page():
    r = requests.get(f"{BASE}/chat", timeout=5)
    assert r.status_code in (200, 401)  # 401 valid when auth enabled


def test_vibe_page():
    r = requests.get(f"{BASE}/vibe", timeout=5)
    assert r.status_code in (200, 401)  # 401 valid when auth enabled


def test_voice_page():
    r = requests.get(f"{BASE}/voice", timeout=5)
    assert r.status_code in (200, 401)  # 401 valid when auth enabled


def test_memory_search_api():
    r = requests.get(f"{BASE}/api/memory/search?q=test", timeout=5)
    _skip_if_auth(r)
    assert r.status_code in (200, 401)  # 401 valid when auth enabled
    data = r.json()
    assert isinstance(data, (list, dict))  # API returns list or dict depending on endpoint


def test_memory_sessions_api():
    r = requests.get(f"{BASE}/api/memory/sessions?limit=5", timeout=5)
    _skip_if_auth(r)
    assert r.status_code in (200, 401)  # 401 valid when auth enabled


def test_forge_endpoint_removed():
    """PR-1B removed /api/forge (closes D-2). Endpoint must return 404 now.
    Replacement: /api/skill/review → /api/skill/approve."""
    r = requests.post(f"{BASE}/api/forge", json={}, timeout=5)
    _skip_if_auth(r)
    assert r.status_code == 404, f"/api/forge must be removed, got {r.status_code}"


def test_save_skill_endpoint_removed():
    """PR-1B removed /api/save_skill (closes D-3). Endpoint must return 404 now."""
    r = requests.post(
        f"{BASE}/api/save_skill",
        json={"filename": "x.py", "content": "..."},
        timeout=5,
    )
    _skip_if_auth(r)
    assert r.status_code == 404, f"/api/save_skill must be removed, got {r.status_code}"
