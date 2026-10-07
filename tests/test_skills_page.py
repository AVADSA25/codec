"""Skills page (UI phase 3, P3.6; docs/P3.6-DESIGN.md).

Every skill with where it works, whether it is ready, its triggers and an on/off
switch that every path enforces (config.json:skills_off). Nothing here touches
~/.codec: a tmp config and fakes.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
HOME = (REPO / "codec_dashboard.html").read_text(encoding="utf-8")


@pytest.fixture
def sk(tmp_path, monkeypatch):
    import codec_skill_switches as sw
    import routes.skills_page as sp
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"skills": ["calculator"], "llm_model": "x"}))  # an old allow list: ignored
    monkeypatch.setattr(sw, "CONFIG_PATH", str(cfg))
    monkeypatch.setitem(sw._CACHE, "stamp", None)
    monkeypatch.setattr(sp, "GOOGLE_TOKEN", str(tmp_path / "google_token.json"))
    monkeypatch.setattr(sp, "_keys", lambda: {"serper": False, "pexels": False})
    perms = {"screen": False, "ax": False}
    monkeypatch.setattr(sp, "screen_recording_ok", lambda: perms["screen"])
    monkeypatch.setattr(sp, "accessibility_ok", lambda: perms["ax"])
    audits = []
    import codec_audit
    monkeypatch.setattr(codec_audit, "log_event", lambda *a, **k: audits.append((a, k)))
    app = FastAPI()
    app.include_router(sp.router)
    yield SimpleNamespace(client=TestClient(app), cfg=cfg, sw=sw, sp=sp, perms=perms, audits=audits, tmp=tmp_path)
    sw._CACHE["stamp"] = None


def _by_name(d):
    return {s["name"]: s for s in d["skills"]}


def test_catalog_groups_paths_and_readiness(sk):
    d = sk.client.get("/api/skills/catalog").json()
    s = _by_name(d)
    assert d["groups"][0] == "Google" and d["groups"][-1] == "Other" and "dashboard" in d["permissions_note"]
    cal = s["google_calendar"]
    assert cal["group"] == "Google" and cal["ready"] == "setup" and "Connectors" in cal["reason"]
    assert s["web_search"]["ready"] == "limited" and "Serper" in s["web_search"]["reason"]
    assert s["screenshot_text"]["ready"] == "setup" and "Screen Recording" in s["screenshot_text"]["reason"]
    assert s["ax_control"]["ready"] == "setup" and "Accessibility" in s["ax_control"]["reason"]
    assert s["calculator"]["paths"]["chat"] is True and s["calculator"]["paths"]["voice"] is False, "the voice call skips it"
    assert s["terminal"]["paths"]["mcp"] is False, "never over MCP HTTP"
    for name in ("health_check", "backup_status", "audit_report", "memory_history"):
        assert s[name]["paths"]["chat"] is True, name
    asks = [n for n, x in s.items() if x["paths"]["mcp_asks"]]
    import codec_config
    assert asks and set(asks) <= set(codec_config._HTTP_CONSENT_REQUIRED)
    assert all(x["on"] for x in s.values()), "the old allow list is ignored: everything is on"
    (sk.tmp / "google_token.json").write_text("{}")
    sk.perms.update(screen=True, ax=True)
    s = _by_name(sk.client.get("/api/skills/catalog").json())
    assert s["google_calendar"]["ready"] == "ready" and s["screenshot_text"]["ready"] == "ready" and s["ax_control"]["ready"] == "ready"


def test_customized_triggers_are_flagged(sk, monkeypatch):
    import codec_skill_registry
    from codec_dispatch import registry
    if not registry.names():
        registry.scan()
    path = sk.tmp / "custom_triggers.json"
    monkeypatch.setattr(codec_skill_registry, "CUSTOM_TRIGGERS_PATH", str(path))
    # Saved after the registry scanned (Save on the page): the page shows it at once.
    path.write_text(json.dumps({"timer": {"triggers": ["my own phrase"]}}))
    s = _by_name(sk.client.get("/api/skills/catalog").json())
    assert s["timer"]["customized"] is True and s["timer"]["triggers"] == ["my own phrase"]
    assert s["calculator"]["customized"] is False
    path.write_text("{}")  # Reset to default
    assert _by_name(sk.client.get("/api/skills/catalog").json())["timer"]["customized"] is False


def test_the_switch_writes_the_deny_list_and_one_value_free_audit_line(sk):
    c = sk.client
    assert c.put("/api/skills/switch", json={"name": "not_a_skill", "on": False}).status_code == 400
    assert c.put("/api/skills/switch", json={"name": "timer", "on": "no"}).status_code == 400
    assert c.put("/api/skills/switch", json={"name": "timer", "on": False}).json() == {"name": "timer", "on": False}
    cfg = json.loads(sk.cfg.read_text())
    assert cfg["skills_off"] == ["timer"] and cfg["llm_model"] == "x", "other keys kept"
    assert _by_name(c.get("/api/skills/catalog").json())["timer"]["on"] is False
    assert sk.audits == [(("skill_switched", "codec-dashboard", "Skill off"), {"tool": "timer", "extra": {"on": False}})]
    c.put("/api/skills/switch", json={"name": "timer", "on": True})
    assert json.loads(sk.cfg.read_text())["skills_off"] == []


def test_every_path_refuses_a_skill_that_is_off(sk, monkeypatch):
    import codec_agents
    import codec_dispatch
    import codec_voice
    from routes.palette import pickable_skills
    assert codec_dispatch.check_skill("set a timer for 5 minutes")["name"] == "timer"
    sk.sw.set_on("timer", False)
    m = codec_dispatch.check_skill("set a timer for 5 minutes")
    assert m is None or m["name"] != "timer"
    monkeypatch.setattr(codec_dispatch.registry, "run", lambda *a, **k: pytest.fail("ran a skill that is off"))
    out = codec_dispatch.run_skill({"name": "timer", "_all_matches": ["timer"]}, "set a timer for 5 minutes")
    assert "turned off" in out
    fake_reg = SimpleNamespace(load=lambda n: pytest.fail("loaded a skill that is off"))
    assert "turned off" in codec_agents._make_lazy_fn(fake_reg, "timer")("5 minutes")
    voice = SimpleNamespace(skills={"timer": {"triggers": ["set a timer"]}}, _VOICE_SKIP_SKILLS=set(),
                            _skill_registry=SimpleNamespace(load=lambda n: pytest.fail("voice loaded it")))
    assert codec_voice.VoicePipeline._match_skill(voice, "set a timer for 5 minutes") is None
    assert "timer" not in pickable_skills()
    mcp = (REPO / "codec_mcp.py").read_text(encoding="utf-8")
    assert "if codec_skill_switches.is_off(rkey):" in mcp and "return codec_skill_switches.off_message(rkey)" in mcp
    auto = (REPO / "codec_autopilot.py").read_text(encoding="utf-8")
    assert "if is_off(skill):  # P3.6" in auto and 'error_type="SkillSwitchedOff"' in auto


def test_the_slash_command_uses_the_same_list(sk):
    import codec_slash_commands
    assert codec_slash_commands._cmd_skills(["disable", "timer"]) == "Skill `timer` disabled."
    assert json.loads(sk.cfg.read_text())["skills_off"] == ["timer"]
    listing = codec_slash_commands._cmd_skills(["list"])
    assert "| off | `timer` |" in listing and "| on | `calculator` |" in listing
    codec_slash_commands._cmd_skills(["enable", "timer"])
    assert json.loads(sk.cfg.read_text())["skills_off"] == []


def test_the_voice_skip_list_matches_the_voice_call():
    import codec_voice
    import routes.skills_page as sp
    assert sp.VOICE_SKIP == codec_voice.VoicePipeline._VOICE_SKIP_SKILLS


def test_home_has_the_skills_page_and_settings_points_to_it():
    assert "if (id === 'skills') { loadSkillReviews(); loadSkillsCatalog(); }" in HOME
    assert 'id="skCatalog"' in HOME and 'id="skFilter"' in HOME and "Waiting for your review" in HOME
    assert "fetch('/api/skills/catalog')" in HOME and "fetch('/api/skills/switch', { method: 'PUT'" in HOME
    assert "_ssSet('codec-chat-pick', JSON.stringify({ kind: 'skill', name: s.name }));" in HOME
    assert "ta.id = 'ta-' + s.name;" in HOME and "renderSkillCards" not in HOME and 'id="skillsList"' not in HOME
    assert "onclick=\"showTab('skills')\">Open Skills</button>" in HOME
    assert 'href="/#skills"' in (REPO / "codec_cortex.html").read_text(encoding="utf-8")
