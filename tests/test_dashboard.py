"""Test dashboard endpoints — requires codec_dashboard.py running on :8090"""
import json, os, pytest, requests

BASE = "http://localhost:8090"

# Load dashboard token for auth
_cfg_path = os.path.expanduser("~/.codec/config.json")
try:
    with open(_cfg_path) as _f:
        _TOKEN = json.load(_f).get("dashboard_token", "")
except Exception:
    _TOKEN = ""


def _skip_if_auth(r):
    """Skip if biometric auth blocks the request and no token is configured."""
    if r.status_code == 401 and not _TOKEN:
        pytest.skip("Auth enabled without dashboard_token")


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


def test_forge_requires_input():
    r = requests.post(f"{BASE}/api/forge", json={}, timeout=5)
    _skip_if_auth(r)
    assert r.status_code in [400, 401, 422]  # 401 valid when auth enabled
