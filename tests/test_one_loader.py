"""P-098: built-in features run skills only through codec_dispatch.

The bridges' briefings, the heartbeat's mail alert, Daybreak, the check-in and
the observer's shift report used to load skill files by hand, so the Skills page
switch, the registry's load gate, plugin hooks and the audit line did not apply.
They now call codec_dispatch.run_feature / feature_module
(docs/PROJECT-MODE-GATE-DESIGN.md). Everything here runs on fake skills in a
temp folder: nothing touches ~/.codec, Google or the network.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent

SEVEN_FILES = ("codec_telegram.py", "codec_imessage.py", "codec_heartbeat.py",
               "codec_checkin.py", "codec_daybreak.py", "codec_observer.py",
               "routes/mcp.py")
EXEMPTION = "loader exemption (P-098)"
_LOAD_RE = re.compile(r"spec_from_file_location|import_module|__import__\(\s*f?[\"']skills")

FAKE_SKILLS = {
    "weather": "Sunny, 24 C",
    "google_calendar": "10:00 Standup",
    "google_tasks": "Pay the invoice",
    "google_gmail": "Found 0 emails:",
}


def _hand_loads(src: str) -> list:
    """(line number, text) of every line that loads a module by hand, except
    the one documented exemption."""
    return [(no, line.strip()) for no, line in enumerate(src.splitlines(), 1)
            if _LOAD_RE.search(line) and EXEMPTION not in line]


def _write_skill(folder: Path, name: str, body: str) -> None:
    (folder / f"{name}.py").write_text(textwrap.dedent(f'''
        SKILL_NAME = "{name}"
        SKILL_TRIGGERS = ["p098 {name} trigger"]
        SKILL_DESCRIPTION = "fake {name} for the P-098 tests"
        CALLS = []
    ''') + textwrap.dedent(body), encoding="utf-8")


@pytest.fixture
def feat(tmp_path, monkeypatch):
    """A fresh, unscanned registry over fake skills, one post_tool plugin, the
    audit log, config and every ~/.codec path in tmp_path; google_gmail is
    switched off in config.json:skills_off."""
    import codec_audit
    import codec_dispatch
    import codec_hooks
    import codec_license
    import codec_skill_switches
    from codec_skill_registry import SkillRegistry

    skills = tmp_path / "skills"
    skills.mkdir()
    for name, answer in FAKE_SKILLS.items():
        _write_skill(skills, name, f'''
            def run(task, app="", ctx=""):
                CALLS.append(task)
                return "{answer}"
        ''')
    _write_skill(skills, "shift_report", '''
        def _enabled():
            return True
        def already_fired_today():
            return False
        def _load_config():
            return {"daily_at_hour": 99, "daily_at_minute": 0, "idle_minutes": 1}
        def run(task, app="", ctx=""):
            CALLS.append(task)
            return "report posted"
    ''')
    registry = SkillRegistry(str(skills))
    monkeypatch.setattr(codec_dispatch, "registry", registry)

    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "p098_tracer.py").write_text(
        "SEEN = []\n"
        "def post_tool(ctx, result):\n"
        "    SEEN.append(ctx.tool_name)\n"
        "    return None\n", encoding="utf-8")
    hooks = codec_hooks.PluginRegistry(str(plugins))
    hooks.scan()
    monkeypatch.setattr(codec_hooks, "_registry", hooks)

    audit_log = tmp_path / "audit.log"
    audit_log.write_text("")
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", audit_log)
    # Both modules bind log_event at import: if an earlier test imported them while
    # codec_audit.log_event was patched, they would keep that stub.
    monkeypatch.setattr(codec_dispatch, "log_event", codec_audit.log_event)
    monkeypatch.setattr(codec_hooks, "_log_event", codec_audit.log_event)
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"skills_off": ["google_gmail"]}))
    monkeypatch.setattr(codec_skill_switches, "CONFIG_PATH", str(config))
    monkeypatch.setattr(codec_license, "feature_allowed", lambda *_a: True)

    home = tmp_path / "codec"
    home.mkdir()
    real = os.path.expanduser
    monkeypatch.setattr(os.path, "expanduser",
                        lambda p: str(home / p[len("~/.codec/"):]) if str(p).startswith("~/.codec/") else real(p))

    def records(event=None):
        recs = [json.loads(line) for line in audit_log.read_text().splitlines() if line.strip()]
        return [r for r in recs if event is None or r.get("event") == event]

    def calls(name):
        mod = registry._modules.get(name)
        return list(mod.CALLS) if mod else []

    return SimpleNamespace(registry=registry, config=config, home=home, records=records,
                           calls=calls, skills=skills)


def _tool_results(feat, feature):
    return {r["tool"]: r for r in feat.records("tool_result")
            if (r.get("extra") or {}).get("feature") == feature}


def test_seven_files_load_skills_only_through_dispatch():
    # The scanner itself: it sees each old way of loading a skill by hand.
    old = ('mod = __import__(f"skills.{skill_name}", fromlist=["run"])\n'
           'spec = importlib.util.spec_from_file_location("google_gmail", path)\n'
           'mod = importlib.import_module(name)\n'
           f'spec = importlib.util.spec_from_file_location("x", p)  # {EXEMPTION}\n')
    assert [no for no, _ in _hand_loads(old)] == [1, 2, 3]

    found = {f: _hand_loads((REPO / f).read_text(encoding="utf-8")) for f in SEVEN_FILES}
    assert {f: v for f, v in found.items() if v} == {}
    exempt = [line for line in (REPO / "routes/mcp.py").read_text().splitlines() if EXEMPTION in line]
    assert len(exempt) == 1 and "mcp_connect" in exempt[0]


def test_telegram_briefing_audits_hooks_and_respects_switch(feat, monkeypatch):
    import codec_bridges
    import codec_telegram
    monkeypatch.setattr(codec_bridges, "_dispatch_loaded", False)
    monkeypatch.setattr(codec_telegram, "_fetch_rss_headlines", lambda: [])
    monkeypatch.setattr(codec_telegram.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no network in tests")))

    text = codec_telegram._gather_briefing_data()

    assert "Sunny, 24 C" in text and "10:00 Standup" in text and "Pay the invoice" in text
    assert "[GOOGLE_GMAIL]" not in text and feat.calls("google_gmail") == []
    runs = _tool_results(feat, "briefing")
    assert set(runs) == {"weather", "google_calendar", "google_tasks"}
    hooks = feat.records("hook_fired")
    for name, rec in runs.items():
        assert rec["source"] == "codec-dispatch"
        cid = rec["extra"]["correlation_id"]
        assert cid and any(h["extra"]["tool_name"] == name and h["extra"]["correlation_id"] == cid
                           for h in hooks), name
    assert not any("google_gmail" in (r.get("tool"), (r.get("extra") or {}).get("tool_name"))
                   for r in feat.records())
    assert feat.records("wake_dispatch") == []
    assert not (feat.home / "overlay_events.jsonl").exists()


def test_imessage_briefing_runs_through_dispatch(feat, monkeypatch):
    import codec_bridges
    import codec_imessage
    monkeypatch.setattr(codec_bridges, "_dispatch_loaded", False)
    monkeypatch.setattr(codec_imessage, "_fetch_rss_headlines", lambda: [])
    monkeypatch.setattr(codec_imessage.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no network in tests")))

    text = codec_imessage._gather_briefing_data()

    assert "Sunny, 24 C" in text and feat.calls("google_gmail") == []
    assert set(_tool_results(feat, "briefing")) == {"weather", "google_calendar", "google_tasks"}


def test_heartbeat_mail_alert_runs_through_dispatch_and_skips_when_off(feat, monkeypatch):
    import codec_alerts
    import codec_heartbeat
    hb_config = feat.config.parent / "heartbeat_config.json"
    hb_config.write_text(json.dumps({"heartbeat_alerts": [{"type": "email_check", "name": "Mail"}]}))
    monkeypatch.setattr(codec_heartbeat, "CONFIG_PATH", str(hb_config))
    # P-083: check_alerts saves to codec_alerts' own alert_state.json path
    monkeypatch.setattr(codec_alerts, "ALERT_STATE_PATH", str(feat.home / "alert_state.json"))

    assert codec_heartbeat.check_alerts() == []          # switched off: skipped
    assert feat.calls("google_gmail") == [] and _tool_results(feat, "heartbeat") == {}

    feat.config.write_text(json.dumps({"skills_off": []}))
    assert codec_heartbeat.check_alerts() == []          # on: "Found 0 emails:" = inbox clear
    assert feat.calls("google_gmail") == ["check unread emails"]
    assert set(_tool_results(feat, "heartbeat")) == {"google_gmail"}
    assert json.loads((feat.home / "alert_state.json").read_text())["email_unread"] == 0


def test_daybreak_sources_go_through_dispatch(feat):
    import codec_daybreak
    assert codec_daybreak._run_source("weather", "weather") == "Sunny, 24 C"
    assert codec_daybreak._skill_module("weather") is feat.registry._modules["weather"]
    assert codec_daybreak._run_source("google_gmail", "unread emails") is None
    assert codec_daybreak._skill_module("google_gmail") is None
    assert set(_tool_results(feat, "daybreak")) == {"weather"}


def test_daybreak_source_refused_by_the_load_gate(feat):
    import codec_daybreak
    _write_skill(feat.skills, "weather", '''
        import subprocess
        def run(task, app="", ctx=""):
            subprocess.run(["true"])
            return "ran"
    ''')
    assert not codec_daybreak._run_source("weather", "weather")
    assert "weather" not in feat.registry._modules
    assert feat.records("skill_load_blocked")


def test_checkin_calendar_and_unread_follow_the_switch(feat, monkeypatch):
    import codec_checkin
    import codec_google_auth
    built = []
    monkeypatch.setattr(codec_google_auth, "build_service", lambda *a: built.append(a))
    assert codec_checkin._calendar_next_hours() == "10:00 Standup"
    assert set(_tool_results(feat, "checkin")) == {"google_calendar"}
    assert codec_checkin._important_unread() is None and built == []


def test_observer_shift_report_fires_through_dispatch(feat):
    import codec_observer
    codec_observer._maybe_fire_shift_report(idle_seconds=120.0)
    assert feat.calls("shift_report") == ["shift report [trigger=idle]"]
    assert set(_tool_results(feat, "shift_report")) == {"shift_report"}

    feat.config.write_text(json.dumps({"skills_off": ["shift_report"]}))
    codec_observer._maybe_fire_shift_report(idle_seconds=120.0)
    assert feat.calls("shift_report") == ["shift report [trigger=idle]"]


def test_auto_memorize_gets_fact_extract_through_dispatch(tmp_path, monkeypatch):
    # Daybreak no longer puts the skills folder on sys.path, so auto_memorize's
    # old bare `from fact_extract import run` failed in the dashboard.
    import codec_dispatch
    spec = importlib.util.spec_from_file_location("p098_auto_memorize", REPO / "skills" / "auto_memorize.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._MARKER = tmp_path / "auto_memorize_last.json"
    mod._pull_conversations = lambda: [{"role": "user", "content": "x" * 300, "session_id": "s"}]
    seen = []
    fake = SimpleNamespace(run=lambda blob, ctx: seen.append(blob) or "Saved 2/2 facts")
    monkeypatch.setattr(codec_dispatch, "feature_module", lambda name: fake if name == "fact_extract" else None)

    assert "2 fact(s) saved" in mod.run("force") and len(seen) == 1
    monkeypatch.setattr(codec_dispatch, "feature_module", lambda name: None)
    assert "switched off" in mod.run("force") and len(seen) == 1


def test_bridge_free_text_stays_with_the_model_unless_switched_on(monkeypatch):
    import codec_bridges
    import codec_config
    seen = []
    monkeypatch.setattr(codec_bridges, "_dispatch_loaded", True)
    monkeypatch.setattr(codec_bridges, "_check_skill", lambda text: seen.append(text) or {"name": "time"})
    monkeypatch.setattr(codec_bridges, "_run_skill", lambda skill, text: "10:00")
    monkeypatch.setitem(codec_config.cfg, "bridges", {})
    assert codec_bridges.try_skill("what time is it") == (None, None) and seen == []
    monkeypatch.setitem(codec_config.cfg, "bridges", {"free_text_skills": "yes"})
    assert codec_bridges.try_skill("what time is it") == (None, None) and seen == []
    monkeypatch.setitem(codec_config.cfg, "bridges", {"free_text_skills": True})
    assert codec_bridges.try_skill("what time is it") == ("time", "10:00") and seen == ["what time is it"]
