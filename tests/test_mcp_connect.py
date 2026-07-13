"""mcp_connect — CODEC as an MCP *client* (bidirectional MCP).

Verifies the bridge logic without touching the network: fastmcp.Client is
replaced with a fake async context manager. Covers config seeding, listing
servers, listing a server's tools, an explicit tool call, and the
disabled-server guard.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "skills"))

import mcp_connect  # noqa: E402


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """Point the skill at a throwaway config with one ENABLED fake server."""
    path = tmp_path / "mcp_servers.json"
    path.write_text(json.dumps({"servers": [
        {"name": "acme", "transport": "http", "url": "https://acme.test/mcp", "enabled": True},
        {"name": "notion", "transport": "http", "url": "https://mcp.notion.com/mcp",
         "enabled": False, "auth": "oauth", "note": "Notion"},
    ]}))
    monkeypatch.setattr(mcp_connect, "CONFIG_PATH", str(path))
    return path


class _FakeTool:
    def __init__(self, name, description=""):
        self.name = name
        self.description = description


class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeClient:
    """Async-context-manager stand-in for fastmcp.Client."""
    last_call = None

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def list_tools(self):
        return [_FakeTool("create_page", "Create a page"), _FakeTool("search")]

    async def call_tool(self, name, args):
        _FakeClient.last_call = (name, args)
        return _FakeResult({"ok": True, "tool": name, "args": args})


@pytest.fixture
def fake_client(monkeypatch):
    monkeypatch.setattr(mcp_connect, "_client_for", lambda server: _FakeClient())
    _FakeClient.last_call = None


# ── config seeding ──────────────────────────────────────────────────────────

def test_seeds_config_on_first_use(tmp_path, monkeypatch):
    path = tmp_path / "mcp_servers.json"
    monkeypatch.setattr(mcp_connect, "CONFIG_PATH", str(path))
    out = mcp_connect.run("list mcp")
    assert path.exists(), "config must be seeded on first use"
    seeded = json.loads(path.read_text())
    names = {s["name"] for s in seeded["servers"]}
    assert {"notion", "github", "linear"} <= names, "popular servers must be seeded"
    assert "notion" in out


# ── listing ─────────────────────────────────────────────────────────────────

def test_list_servers_shows_state(cfg):
    out = mcp_connect.run("list mcp")
    assert "acme" in out and "on" in out
    assert "notion" in out and "off" in out


def test_list_tools_on_enabled_server(cfg, fake_client):
    out = mcp_connect.run("list tools on acme")
    assert "create_page" in out and "search" in out


def test_bare_server_mention_lists_its_tools(cfg, fake_client):
    out = mcp_connect.run("what can acme do")
    assert "create_page" in out


# ── calling ─────────────────────────────────────────────────────────────────

def test_explicit_call_invokes_the_tool(cfg, fake_client):
    out = mcp_connect.run('call acme create_page {"title": "Demo notes"}')
    assert _FakeClient.last_call == ("create_page", {"title": "Demo notes"})
    assert "create_page" in out and "ok" in out.lower()


def test_call_with_bad_json_is_reported(cfg, fake_client):
    out = mcp_connect.run("call acme create_page {not json}")
    assert "json" in out.lower()


# ── guards ──────────────────────────────────────────────────────────────────

def test_disabled_server_is_not_contacted(cfg, monkeypatch):
    monkeypatch.setattr(mcp_connect, "_client_for",
                        lambda s: (_ for _ in ()).throw(AssertionError("must not connect")))
    out = mcp_connect.run("list tools on notion")
    assert "disabled" in out.lower()


def test_unknown_server_call_is_friendly(cfg, fake_client):
    out = mcp_connect.run("call doesnotexist sometool {}")
    assert "no mcp server" in out.lower()


def test_network_error_is_caught(cfg, monkeypatch):
    def boom(server):
        raise ConnectionError("refused")
    monkeypatch.setattr(mcp_connect, "_client_for", boom)
    out = mcp_connect.run("list tools on acme")
    assert "could not connect" in out.lower() and "acme" in out
