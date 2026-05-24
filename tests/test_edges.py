"""Tests for PR-7O (Audit B / B-16 + B-17) — a custom-agent id can't shadow a Phase-3
Project in the shared ~/.codec/agents/ namespace, and agent CONTENT only leaves the device
for channels the user explicitly opted this agent into (default off; token via Keychain).

Reference: docs/PR7O-EDGES-DESIGN.md.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:] = [p for p in sys.path if p != str(REPO)]
sys.path.insert(0, str(REPO))

import codec_agent_messaging as cam  # noqa: E402


# ── B-16: crew/Project collision guard ────────────────────────────────────────
def test_custom_id_shadows_project_detects_collision(monkeypatch, tmp_path):
    import routes.agents as ra
    agents = tmp_path / "agents"
    (agents / "foo").mkdir(parents=True)
    (agents / "foo" / "manifest.json").write_text("{}")
    monkeypatch.setattr(ra, "_AGENTS_DIR", str(agents))
    assert ra._custom_id_shadows_project("foo") is True
    assert ra._custom_id_shadows_project("bar") is False


def test_save_custom_agent_refuses_project_collision(monkeypatch, tmp_path):
    import routes.agents as ra
    agents = tmp_path / "agents"
    (agents / "foo").mkdir(parents=True)
    (agents / "foo" / "manifest.json").write_text("{}")
    monkeypatch.setattr(ra, "_AGENTS_DIR", str(agents))

    class _Req:
        async def json(self):
            return {"name": "foo", "role": "shadow"}

    resp = asyncio.run(ra.save_custom_agent(_Req()))
    assert getattr(resp, "status_code", 200) == 409, \
        "saving a custom agent that shadows a Project must 409 (B-16)"
    assert not (agents / "foo.json").exists(), "the shadowing custom file must not be written"


# ── B-17: outbound-content opt-in + Keychain token ────────────────────────────
def _spy_requests(monkeypatch):
    import requests
    calls = []

    def _post(url, **kw):
        calls.append({"url": url, **kw})
    monkeypatch.setattr(requests, "post", _post)
    return calls


def _agent_manifest(monkeypatch, tmp_path, agent_id, **extra):
    monkeypatch.setattr(cam, "_AGENTS_DIR", tmp_path / "agents")
    d = tmp_path / "agents" / agent_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps({"agent_id": agent_id, **extra}))


def test_remote_channel_redacts_content_without_optin(monkeypatch, tmp_path):
    _agent_manifest(monkeypatch, tmp_path, "a1")  # no allow_outbound_content
    monkeypatch.setattr(cam, "_channel_config",
                        lambda k: {"telegram_token": "T", "telegram_chat_id": "C"}.get(k, ""))
    monkeypatch.setattr("codec_config.get_telegram_bot_token", lambda: "", raising=False)
    calls = _spy_requests(monkeypatch)

    cam._dispatch_to_channel("telegram", "a1", "SECRET TITLE",
                             "SECRET fetched file contents", "agent_update")

    assert calls, "telegram POST should still fire (content-free ping)"
    text = calls[0]["json"]["text"]
    assert "SECRET fetched file contents" not in text, \
        "agent content must NOT be exfiltrated without per-agent opt-in (B-17)"
    assert "dashboard" in text.lower()


def test_remote_channel_sends_content_with_optin(monkeypatch, tmp_path):
    _agent_manifest(monkeypatch, tmp_path, "a2", allow_outbound_content=True)
    monkeypatch.setattr(cam, "_channel_config",
                        lambda k: {"telegram_token": "T", "telegram_chat_id": "C"}.get(k, ""))
    monkeypatch.setattr("codec_config.get_telegram_bot_token", lambda: "", raising=False)
    calls = _spy_requests(monkeypatch)

    cam._dispatch_to_channel("telegram", "a2", "TITLE",
                             "the real agent body", "agent_update")

    assert calls and "the real agent body" in calls[0]["json"]["text"], \
        "opted-in agent content must be delivered (B-17)"


def test_telegram_token_prefers_keychain(monkeypatch, tmp_path):
    _agent_manifest(monkeypatch, tmp_path, "a3", allow_outbound_content=True)
    monkeypatch.setattr(cam, "_channel_config",
                        lambda k: {"telegram_token": "PLAINTEXT", "telegram_chat_id": "C"}.get(k, ""))
    monkeypatch.setattr("codec_config.get_telegram_bot_token", lambda: "KCTOKEN", raising=False)
    calls = _spy_requests(monkeypatch)

    cam._dispatch_to_channel("telegram", "a3", "t", "b", "agent_update")

    assert calls and "KCTOKEN" in calls[0]["url"], \
        "the Keychain-backed token must be preferred over the plaintext config token (B-17)"
    assert "PLAINTEXT" not in calls[0]["url"]
