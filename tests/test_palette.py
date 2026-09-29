"""'/' commands, '@' picks and the Cmd+K palette (UI phase 2, P2.3; docs/P2.3-DESIGN.md).

The routes list what the composer and the palette offer; the chat `skill` field runs
exactly the picked skill (chat allowlist only, consent first, through run_skill), or
mcp_connect for an MCP server. Nothing here touches ~/.codec: tmp paths and fakes.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
CHAT = (REPO / "codec_chat.html").read_text(encoding="utf-8")
SHELL = (REPO / "static" / "codec-shell.js").read_text(encoding="utf-8")

NAMES = ["AI News Digest", "time", "google_calendar", "chrome_open", "create_skill", "python_exec", "terminal",
         "mcp_connect"]


@pytest.fixture
def pal(tmp_path, monkeypatch):
    import codec_dispatch
    import routes._shared as shared
    import routes.mcp as mcp
    import routes.palette as palette
    fake = SimpleNamespace(names=lambda: list(NAMES), scan=lambda: None, get_triggers=lambda n: [],
                           get_description=lambda n: f"{n} does its job. More detail here.", run=lambda *a, **k: None)
    monkeypatch.setattr(codec_dispatch, "registry", fake)
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "helper.json").write_text(json.dumps({"id": "helper", "name": "Helper", "role": "R" * 300,
                                                    "tools": ["web_search"], "max_iterations": 5}))
    (agents / "broken.json").write_text("{not json")
    monkeypatch.setattr(shared, "_AGENTS_DIR", str(agents))
    servers = tmp_path / "mcp_servers.json"
    servers.write_text(json.dumps({"servers": [{"name": "notion", "url": "https://mcp.example/n", "enabled": True},
                                               {"name": "github", "url": "https://mcp.example/g", "enabled": False}]}))
    monkeypatch.setattr(mcp, "MCP_SERVERS_PATH", str(servers))
    app = FastAPI()
    app.include_router(palette.router)
    ran, consent = [], []
    monkeypatch.setattr(codec_dispatch, "run_skill", lambda skill, task, app="": ran.append((skill["name"], task)) or f"ran {skill['name']}")
    import codec_consent
    monkeypatch.setattr(codec_consent, "chat_consent_ok", lambda name, task: consent.append(name) or name != "terminal")
    return SimpleNamespace(client=TestClient(app), ran=ran, consent=consent, tmp=tmp_path)


def test_slash_commands_lists_every_command_with_its_usage(pal):
    from codec_slash_commands import SLASH_COMMANDS
    got = pal.client.get("/api/slash_commands").json()["commands"]
    assert [c["name"] for c in got] == [c.name for c in SLASH_COMMANDS]
    skills = next(c for c in got if c["name"] == "skills")
    assert skills["usage"].startswith("/skills [") and next(c for c in got if c["name"] == "status")["usage"] == "/status"
    assert "?" in next(c for c in got if c["name"] == "help")["aliases"]


def test_mentions_lists_picks_by_skill_name_in_groups(pal):
    d = pal.client.get("/api/mentions").json()
    assert {s["name"]: s["group"] for s in d["skills"]} == {
        "AI News Digest": "Reports", "time": "Tools", "google_calendar": "Google", "chrome_open": "Browser",
        "terminal": "Mac"}, "the chat allowlist only, minus create_skill; never python_exec or mcp_connect"
    assert d["skills"][0]["description"] == "AI News Digest does its job"
    crews = {c["name"]: c for c in d["crews"]}
    assert len(crews) == 12 and crews["deep_research"]["arg"] == "topic" and crews["daily_briefing"]["arg"] is None
    assert crews["trip_planner"]["label"] == "Trip Planner"
    assert d["agents"] == [{"id": "helper", "name": "Helper", "role": "R" * 300, "tools": ["web_search"], "max_iterations": 5}]
    assert d["mcp"] == [{"name": "notion", "description": "https://mcp.example/n"}], "switched-on servers only"


def test_a_picked_skill_runs_that_skill_not_the_trigger_match(pal):
    from routes.chat import _try_explicit_skill
    assert _try_explicit_skill("time", "what's the weather in Paris") == ("time", "ran time")
    assert pal.ran == [("time", "what's the weather in Paris")] and pal.consent == ["time"]


def test_skills_outside_the_list_are_refused_without_running(pal):
    from routes.chat import _try_explicit_skill
    for name in ("python_exec", "create_skill", "mcp_connect", "no_such_skill", ""):
        assert _try_explicit_skill(name, "x")[1] == "That skill can't be run from chat.", name
    assert pal.ran == []


def test_a_destructive_pick_asks_first(pal):
    from routes.chat import _try_explicit_skill
    name, result = _try_explicit_skill("terminal", "ls ~")
    assert name == "terminal" and "wasn't confirmed" in result and pal.ran == [] and pal.consent == ["terminal"]


def test_an_mcp_server_goes_to_mcp_connect(pal):
    from routes.chat import _try_explicit_skill
    _try_explicit_skill("mcp:notion", 'search {"query": "taxes"}')
    _try_explicit_skill("mcp:notion", "find my pages about taxes")
    _try_explicit_skill("mcp:notion", "")
    assert pal.ran == [("mcp_connect", 'call notion search {"query": "taxes"}'), ("mcp_connect", "list tools on notion"),
                       ("mcp_connect", "list tools on notion")]
    assert _try_explicit_skill("mcp:bad name!", "x")[1] == "That MCP server name is not valid."


def test_the_chat_route_answers_a_pick_without_the_model(pal):
    import routes.chat as chat
    app = FastAPI()
    app.include_router(chat.router)
    r = TestClient(app).post("/api/chat", json={"messages": [{"role": "user", "content": "weather in Paris"}],
                                                 "skill": "time", "stream": False})
    assert r.status_code == 200 and r.json() == {"response": "**time**: ran time", "skill": "time"}
    r = TestClient(app).post("/api/chat", json={"messages": [{"role": "user", "content": "x"}], "skill": "time",
                                                 "stream": True})
    assert '"skill": "time"' in r.text and "**time**: ran time" in r.text and "[DONE]" in r.text


def test_the_chat_page_wires_slash_and_at(pal):
    for needle in ('id="mentionMenu"', 'id="pickChip"', 'aria-autocomplete="list" aria-controls="mentionMenu"',
                   "_getJSON('/api/slash_commands')", "_getJSON('/api/mentions')", "if(_menuKey(e))return;",
                   "{name:'new',", "{name:'brief',", "{name:'image',", "{name:'think',", "{name:'project',",
                   "skill:pick?pick.name:undefined", "if(pick)setPick(null);", "payload[_ci.arg]=researchText",
                   "if(_pickAgent){payload.agent_name=_pickAgent.name;", "_ssGet('codec-chat-pick')",
                   "fetch('/api/briefing/run',{method:'POST'})"):
        assert needle in CHAT, needle


def test_the_shell_binds_the_palette_and_the_shortcut_sheet():
    keys = SHELL[SHELL.index("// ── Keys: Cmd/Ctrl+Shift+S sidebar"):]
    assert "if (PAL.el && !PAL.el.hidden) palClose(); else palOpen();" in keys
    assert "(k === '/' || e.code === 'Slash')" in keys and "shortcutsOpen();" in keys
    for needle in ("palette: palOpen, shortcuts: shortcutsOpen,", "fetch('/api/qchat/search?q='", "postJSON('/api/model', { model: m.id })",
                   "postJSON('/api/briefing/run', {})", "sessionStorage.setItem('codec-chat-pick'", "fetch('/api/mentions')",
                   'role="combobox" aria-expanded="true" aria-controls="csPalList"'):
        assert needle in SHELL, needle
