"""Connector-tab route tests — GET /api/mcp/servers + POST toggle.

Pins the two endpoints that back the dashboard "Connector" tab (routes/mcp.py):
  - GET lists external MCP servers with a safe projection (no secret headers).
  - POST flips ONLY the `enabled` flag, never any other field, cross-process-safe.
"""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def env(tmp_path, monkeypatch):
    import routes.mcp as mcp_routes
    cfg = tmp_path / "mcp_servers.json"
    cfg.write_text(json.dumps({"servers": [
        {"name": "notion", "transport": "http", "url": "https://mcp.notion.com/mcp",
         "enabled": False, "auth": "oauth", "note": "Notion"},
        {"name": "hugging-face", "transport": "http", "url": "https://huggingface.co/mcp",
         "enabled": True, "auth": "none", "note": "HF"},
        {"name": "stripe", "transport": "http", "url": "https://mcp.stripe.com",
         "enabled": False, "auth": "api_key",
         "headers": {"Authorization": "Bearer secret"}, "note": "Stripe"},
    ]}))
    monkeypatch.setattr(mcp_routes, "MCP_SERVERS_PATH", str(cfg))
    app = FastAPI()
    app.include_router(mcp_routes.router)
    return TestClient(app), cfg


def test_registered_on_main_app():
    from codec_dashboard import app
    paths = {r.path for r in app.routes if hasattr(r, "path")}
    assert "/api/mcp/servers" in paths
    assert "/api/mcp/servers/{name}/toggle" in paths


def test_list_servers(env):
    client, _ = env
    r = client.get("/api/mcp/servers")
    assert r.status_code == 200
    d = r.json()
    assert d["count"] == 3
    by = {s["name"]: s for s in d["servers"]}
    assert set(by) == {"notion", "hugging-face", "stripe"}
    # needs_auth: none→False, oauth→True, api_key(headers)→True
    assert by["hugging-face"]["needs_auth"] is False
    assert by["notion"]["needs_auth"] is True
    assert by["stripe"]["needs_auth"] is True
    # secret headers never leak to the UI
    assert "headers" not in by["stripe"]
    assert by["hugging-face"]["enabled"] is True


def test_toggle_flips_only_enabled(env):
    client, cfg = env
    r = client.post("/api/mcp/servers/hugging-face/toggle", json={"enabled": False})
    assert r.json() == {"ok": True, "name": "hugging-face", "enabled": False}
    data = json.loads(cfg.read_text())
    hf = next(s for s in data["servers"] if s["name"] == "hugging-face")
    assert hf["enabled"] is False
    assert hf["url"] == "https://huggingface.co/mcp"  # untouched
    # sibling server + its secret untouched
    st = next(s for s in data["servers"] if s["name"] == "stripe")
    assert st["headers"] == {"Authorization": "Bearer secret"}
    # round-trips back on
    client.post("/api/mcp/servers/hugging-face/toggle", json={"enabled": True})
    data = json.loads(cfg.read_text())
    assert next(s for s in data["servers"] if s["name"] == "hugging-face")["enabled"] is True


def test_toggle_ignores_arbitrary_fields(env):
    client, cfg = env
    client.post("/api/mcp/servers/notion/toggle",
                json={"enabled": True, "url": "http://evil", "auth": "none", "injected": "x"})
    data = json.loads(cfg.read_text())
    n = next(s for s in data["servers"] if s["name"] == "notion")
    assert n["enabled"] is True
    assert n["url"] == "https://mcp.notion.com/mcp"  # NOT overwritten
    assert "injected" not in n
    assert n["auth"] == "oauth"  # NOT overwritten


def test_toggle_unknown_server_no_write(env):
    client, cfg = env
    before = cfg.read_text()
    r = client.post("/api/mcp/servers/doesnotexist/toggle", json={"enabled": True})
    body = r.json()
    assert body["ok"] is False
    assert "doesnotexist" in body["error"]
    assert cfg.read_text() == before  # no phantom write / reformat


def test_toggle_name_case_insensitive(env):
    client, cfg = env
    r = client.post("/api/mcp/servers/NOTION/toggle", json={"enabled": True})
    assert r.json()["ok"] is True
    data = json.loads(cfg.read_text())
    assert next(s for s in data["servers"] if s["name"] == "notion")["enabled"] is True
