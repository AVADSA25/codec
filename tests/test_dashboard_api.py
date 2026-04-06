"""Dashboard API tests — verifies every endpoint returns correct status/schema.

Uses FastAPI TestClient for in-process testing (no running server needed).
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    os.environ.setdefault("CODEC_LOG_JSON", "0")
    from codec_dashboard import app
    return TestClient(app)


@pytest.fixture(scope="module")
def auth_headers():
    try:
        from codec_config import DASHBOARD_TOKEN
        if DASHBOARD_TOKEN:
            return {"Authorization": f"Bearer {DASHBOARD_TOKEN}"}
    except ImportError:
        pass
    # TestClient uses "testclient" as host, not 127.0.0.1, so x-internal won't work.
    # If no token configured, tests needing auth will be skipped.
    return None


def _skip_if_no_auth(auth_headers):
    if auth_headers is None:
        pytest.skip("No dashboard token configured — auth-required test skipped")


# ── Public endpoints ───────────────────────────────────────────────────

class TestPublicEndpoints:
    def test_health_returns_200(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert "timestamp" in data

    def test_health_alias(self, client):
        r = client.get("/health")
        assert r.status_code == 200

    def test_root_returns_html(self, client):
        r = client.get("/")
        assert r.status_code == 200

    def test_docs_accessible(self, client):
        r = client.get("/docs")
        assert r.status_code == 200

    def test_openapi_json(self, client):
        r = client.get("/openapi.json")
        assert r.status_code == 200
        data = r.json()
        assert "paths" in data

    def test_auth_check(self, client):
        r = client.get("/api/auth/check")
        assert r.status_code == 200


# ── Auth-required endpoints ────────────────────────────────────────────

class TestAuthRequired:
    def test_history_requires_auth(self, client):
        r = client.get("/api/history")
        assert r.status_code in (401, 403)

    def test_conversations_requires_auth(self, client):
        r = client.get("/api/conversations")
        assert r.status_code in (401, 403)

    def test_audit_requires_auth(self, client):
        r = client.get("/api/audit")
        assert r.status_code in (401, 403)

    def test_skills_requires_auth(self, client):
        r = client.get("/api/skills")
        assert r.status_code in (401, 403)

    def test_config_requires_auth(self, client):
        r = client.get("/api/config")
        assert r.status_code in (401, 403)


# ── Authenticated endpoint responses ──────────────────────────────────

@pytest.mark.skipif(
    not os.environ.get("CODEC_TEST_TOKEN"),
    reason="Set CODEC_TEST_TOKEN env var to run authenticated endpoint tests"
)
class TestAuthenticatedEndpoints:
    """These tests require a valid dashboard token. Set CODEC_TEST_TOKEN to run."""

    def test_skills_list(self, client, auth_headers):
        r = client.get("/api/skills", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, (list, dict))

    def test_history(self, client, auth_headers):
        r = client.get("/api/history", headers=auth_headers)
        assert r.status_code == 200

    def test_agents_crews(self, client, auth_headers):
        r = client.get("/api/agents/crews", headers=auth_headers)
        assert r.status_code == 200

    def test_memory_search(self, client, auth_headers):
        r = client.get("/api/memory/search?q=test&limit=5", headers=auth_headers)
        assert r.status_code == 200

    def test_services_status(self, client, auth_headers):
        r = client.get("/api/services/status", headers=auth_headers)
        assert r.status_code == 200


@pytest.mark.skipif(
    not os.environ.get("CODEC_TEST_TOKEN"),
    reason="Set CODEC_TEST_TOKEN env var to run malformed input tests"
)
class TestMalformedInput:
    def test_command_empty_body(self, client, auth_headers):
        r = client.post("/api/command", json={}, headers=auth_headers)
        assert r.status_code in (200, 400, 422)

    def test_chat_empty(self, client, auth_headers):
        r = client.post("/api/chat", json={}, headers=auth_headers)
        assert r.status_code in (200, 400, 422)
