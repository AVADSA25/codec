"""Skill time limits for chat, voice and the wake word (P-087, docs/SKILL-TIMEOUTS-DESIGN.md).

A fake skill that waits 5 s stands in for osascript behind an unanswered macOS prompt, with the
limit patched to 1 s. Each surface answers a short "timed out" line in about a second, writes one
skill_timeout audit line and takes the next request; weather answers normally. Slow-by-design
skills, the agent runner, the scheduler and crews keep no new limit.

Nothing here touches ~/.codec: the audit log is a tmp file, the overlay events file is redirected,
memory, notifications and plugins are fakes, and osascript never runs.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import codec_applescript  # noqa: F401  (imported here so the chrome skills get this checkout's copy)
import codec_audit
import codec_browser_policy
import codec_cdp
import codec_consent
import codec_dispatch
import codec_hooks

REPO = Path(__file__).resolve().parent.parent
SLOW, FAST = "chrome_scroll", "weather"
SUNNY = "Sunny, 21 C"
TIMED_OUT = "chrome scroll timed out after 1 second"


@pytest.fixture
def world(tmp_path, monkeypatch):
    try:
        import codec_license
        monkeypatch.setattr(codec_license, "feature_allowed", lambda *_a: True)
    except ImportError:
        pass
    audit = tmp_path / "audit.log"
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", audit)
    overlay = tmp_path / "overlay_events.jsonl"
    real_expand = os.path.expanduser
    monkeypatch.setattr(os.path, "expanduser",
                        lambda p: str(overlay) if str(p).endswith("overlay_events.jsonl") else real_expand(p))
    monkeypatch.setattr(codec_hooks, "_registry", SimpleNamespace(for_hook=lambda hook, tool: []))
    monkeypatch.setattr(codec_dispatch, "SKILL_TIMEOUT_S", 1.0)
    monkeypatch.setattr(codec_dispatch, "_configured_timeouts", lambda: {})
    monkeypatch.setattr(codec_dispatch, "_skill_off", lambda n: False)
    monkeypatch.setattr(codec_consent, "chat_consent_ok", lambda n, t: True)
    monkeypatch.setattr(codec_consent, "local_consent_needed", lambda n, t: False)
    release = threading.Event()

    def slow(task, *a):
        release.wait(5)  # an unanswered macOS prompt
        return "Scrolled down"

    def taking(seconds, answer):
        def run(task, *a):
            time.sleep(seconds)
            return answer
        return run

    runs = {SLOW: slow, FAST: lambda task, *a: SUNNY}
    monkeypatch.setattr(codec_dispatch.registry, "run", lambda name, task, app="": runs[name](task))

    def timeouts():
        if not audit.exists():
            return []
        rows = [json.loads(line) for line in audit.read_text().splitlines() if line.strip()]
        return [r for r in rows if r.get("event") == "skill_timeout"]

    yield SimpleNamespace(runs=runs, slow=slow, fast=runs[FAST], taking=taking, timeouts=timeouts, overlay=overlay)
    release.set()


def one_timeout(world, transport, via):
    rows = world.timeouts()
    assert len(rows) == 1, rows
    row = rows[0]
    assert (row["tool"], row["transport"], row["outcome"], row["level"]) == (SLOW, transport, "timeout", "warning")
    assert row["error_type"] == "SkillTimeout" and row["extra"]["limit_s"] == 1.0 and row["extra"]["via"] == via
    assert 900 <= row["duration_ms"] < 3000
    assert "scroll down" not in json.dumps(row)  # never the request text


def timed(fn, *args):
    t0 = time.monotonic()
    out = fn(*args)
    return out, time.monotonic() - t0


# --- chat ----------------------------------------------------------------------

def test_chat_route_answers_a_hung_skill_then_weather(world, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import codec_models
    import routes.chat as chat
    monkeypatch.setattr(codec_models, "model_extras", lambda *a, **k: {})
    monkeypatch.setattr(codec_dispatch, "check_skill",
                        lambda text: {"name": SLOW if "scroll" in text else FAST,
                                      "_all_matches": [SLOW if "scroll" in text else FAST]})
    app = FastAPI()
    app.include_router(chat.router)
    client = TestClient(app)

    def ask(text):
        return client.post("/api/chat", json={"messages": [{"role": "user", "content": text}], "stream": False})

    r, took = timed(ask, "scroll down")
    assert r.status_code == 200 and r.json()["skill"] == SLOW
    assert TIMED_OUT in r.json()["response"] and "permission prompt" in r.json()["response"]
    assert took < 4
    r = ask("weather in Paris")
    assert r.json() == {"response": f"**weather**: {SUNNY}", "skill": FAST}
    one_timeout(world, "chat", "chat")


def test_the_other_chat_paths_are_limited_too(world, monkeypatch):
    import routes.chat as chat
    import routes.palette as palette
    monkeypatch.setattr(palette, "pickable_skills", lambda: {SLOW, FAST})
    (name, out), took = timed(chat._try_explicit_skill, SLOW, "scroll down")
    assert name == SLOW and TIMED_OUT in out and took < 4
    (name, out), took = timed(chat._try_skill_by_name, SLOW, "scroll down")
    assert name == SLOW and TIMED_OUT in out and took < 4
    assert chat._try_skill_by_name(FAST, "weather in Paris") == (FAST, SUNNY)
    assert len(world.timeouts()) == 2


# --- voice call ----------------------------------------------------------------

def voice_pipeline():
    ws = AsyncMock()
    sent = []

    async def send_json(obj):
        sent.append(obj)
    ws.send_json = send_json
    with patch("codec_voice.VoicePipeline._load_skills"), patch("codec_voice._build_system_prompt", return_value="sys"):
        from codec_voice import VoicePipeline
        p = VoicePipeline(ws, client_pcm=False)
    p.skills, p.mode, p._awaiting_ask_user, p._disconnect_reason = {}, "default", None, "user"
    p._poll_pending_question_for_voice = AsyncMock(return_value=None)
    p.synthesize = AsyncMock(return_value=b"ID3-kokoro")
    return p, sent


def test_voice_dispatch_skill_answers_after_the_limit(world):
    p, _ = voice_pipeline()
    out, took = timed(asyncio.run, p.dispatch_skill({"name": SLOW, "run": world.slow}, "scroll down"))
    assert TIMED_OUT in out and took < 4
    assert asyncio.run(p.dispatch_skill({"name": FAST, "run": world.fast}, "weather in Paris")) == SUNNY
    one_timeout(world, "voice", "voice")


def test_voice_call_answers_the_next_utterance(world):
    p, sent = voice_pipeline()
    p.dispatch_crew_from_voice = AsyncMock(return_value=None)
    p._match_skill = lambda text: ({"name": SLOW, "run": world.slow} if "scroll" in text
                                   else {"name": FAST, "run": world.fast})

    async def call():
        for text in ("scroll down", "weather in Paris", None):
            await p.utterance_queue.put(text)
        await asyncio.wait_for(p._pipeline(), 10)

    _, took = timed(asyncio.run, call())
    said = [m["text"] for m in sent if m.get("type") == "transcript" and m.get("role") == "assistant"]
    assert len(said) == 2 and TIMED_OUT in said[0] and said[1] == SUNNY
    assert took < 5
    one_timeout(world, "voice", "voice")


# --- wake word (codec.py) ------------------------------------------------------

@pytest.fixture(scope="module")
def codec_mod():
    """This checkout's codec.py, loaded by path (as tests/test_voice_wake_consent.py does)."""
    spec = importlib.util.spec_from_file_location("_t_codec_p087", REPO / "codec.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_t_codec_p087"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_wake_word_answers_a_hung_skill_aloud(world, monkeypatch, codec_mod):
    import codec_memory
    codec = codec_mod
    spoken, saved = [], []

    class FakeMemory:
        def save(self, *a):
            saved.append(a)

    monkeypatch.setattr(codec_memory, "CodecMemory", FakeMemory)
    monkeypatch.setattr(codec, "focused_app", lambda: "Finder")
    monkeypatch.setattr(codec, "push", lambda fn: None)
    monkeypatch.setattr(codec, "speak_text", spoken.append)
    monkeypatch.setattr(codec, "subprocess", SimpleNamespace(Popen=lambda *a, **k: None, DEVNULL=subprocess.DEVNULL))
    monkeypatch.setattr(codec, "threading", SimpleNamespace(Thread=lambda **k: SimpleNamespace(start=lambda: None)))
    monkeypatch.setattr(codec, "voice_session", {"messages": [], "turn_count": 0, "started": None})
    monkeypatch.setattr(codec, "check_skill",
                        lambda task: {"name": SLOW if "scroll" in task else FAST,
                                      "_all_matches": [SLOW if "scroll" in task else FAST]})
    _, took = timed(codec._dispatch_inner, "scroll down")
    assert len(spoken) == 1 and TIMED_OUT in spoken[0] and took < 4
    codec._dispatch_inner("weather in Paris")
    assert spoken[1] == SUNNY
    one_timeout(world, "dispatch", "wake")


# --- what keeps its time -----------------------------------------------------

def test_slow_by_design_skills_keep_their_wait(world):
    assert codec_dispatch.skill_timeout(SLOW) == 1.0
    assert codec_dispatch.skill_timeout("delegate") == 330
    assert codec_dispatch.skill_timeout("prompt_feeder") == 720
    assert codec_dispatch.skill_timeout("cookbook_serve") == 150  # pm2 start + 90 s health check
    for name in ("ask_user", "stuck", "google_gmail", "model_switch"):
        assert codec_dispatch.skill_timeout(name) is None
    world.runs["delegate"] = world.taking(1.5, "handed over")
    with codec_dispatch.time_limited("chat"):
        out = codec_dispatch.run_skill({"name": "delegate", "_all_matches": ["delegate"]}, "delegate this")
    assert out == "handed over" and world.timeouts() == []


def test_config_sets_and_switches_off_the_limits(monkeypatch):
    import codec_config
    monkeypatch.setattr(codec_dispatch, "SKILL_TIMEOUT_S", 60.0)
    monkeypatch.setattr(codec_config, "cfg", {"skill_timeouts": {"default": 20, "delegate": 30, "weather": 0,
                                                                 "time": None, "music": "fast", "timer": -1,
                                                                 "notes": True, "calculator": 5}})
    t = codec_dispatch.skill_timeout
    assert (t("chrome_scroll"), t("delegate"), t("calculator"), t("compare")) == (20, 30, 5, 150)
    assert t("weather") is None and t("time") is None
    assert t("music") == t("timer") == t("notes") == 20  # not a usable number: the general limit applies
    monkeypatch.setattr(codec_config, "cfg", {"skill_timeouts": {"default": 0}})
    assert t("chrome_scroll") is None and t("delegate") is None  # the rollback switch
    monkeypatch.setattr(codec_config, "cfg", {"skill_timeouts": "nonsense"})
    assert t("chrome_scroll") == 60.0


def test_a_skills_own_timeout_error_is_not_the_limit(world):
    def own(task):
        raise TimeoutError("its own socket")
    with pytest.raises(TimeoutError, match="its own socket") as err:
        codec_dispatch.run_bounded(FAST, own, "x")
    assert not isinstance(err.value, codec_dispatch.SkillTimedOut)


def test_runner_scheduler_and_crews_get_no_new_limit(world, monkeypatch):
    import codec_agent_runner
    import codec_agents
    import codec_scheduler
    monkeypatch.setattr(codec_dispatch, "SKILL_TIMEOUT_S", 0.2)
    world.runs["slowish"] = world.taking(0.6, "slowish done")
    skill = {"name": "slowish", "_all_matches": ["slowish"]}
    with codec_dispatch.time_limited("chat"):  # positive control: the limit is live
        assert "timed out after 0.2 seconds" in codec_dispatch.run_skill(skill, "x")
    assert codec_dispatch.run_skill(skill, "x") == "slowish done"
    assert codec_agent_runner._run_skill("slowish", "x", "agent-1") == "slowish done"
    monkeypatch.setattr(codec_scheduler, "schedulable_skill", lambda name: True)
    assert codec_scheduler._run_skill_job({"skill": "slowish", "task": "x"}) == (True, "slowish done")
    lazy = SimpleNamespace(load=lambda name: SimpleNamespace(run=world.taking(0.6, "crew tool done")))
    assert codec_agents._make_lazy_fn(lazy, "slowish")("x") == "crew tool done"

    async def run_crew(name, callback=None, **kw):
        await asyncio.sleep(0.6)
        return {"status": "complete", "result": "research done", "elapsed_seconds": 1}
    monkeypatch.setattr(codec_agents, "run_crew", run_crew)
    p, _ = voice_pipeline()
    assert asyncio.run(p.dispatch_crew_from_voice("deep research on tides")) == "research done"
    assert len(world.timeouts()) == 1  # the control only


# --- the chrome osascript calls ------------------------------------------------

def load_skill(name):
    saved = list(sys.path)  # the skill files put ~/codec-repo first
    try:
        spec = importlib.util.spec_from_file_location(f"_t_p087_{name}", REPO / "skills" / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path[:] = saved
    return mod


@pytest.fixture
def osascript(monkeypatch):
    calls = []
    state = {"hang_at": None}

    def fake_run(cmd, **kw):
        calls.append(kw)
        if state["hang_at"] is not None and len(calls) >= state["hang_at"]:
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(codec_cdp, "is_cdp_available", lambda: False)
    monkeypatch.setattr(codec_browser_policy, "check_url", lambda url, skill: (True, ""))
    return SimpleNamespace(calls=calls, state=state)


def test_chrome_osascript_calls_carry_a_10s_timeout(osascript):
    scroll, automate = load_skill("chrome_scroll"), load_skill("chrome_automate")
    assert scroll.run("scroll down") == "Scrolled down (AppleScript fallback)"
    assert automate.run("close everything") == "Closed all Chrome tabs"
    assert automate.run("morning tabs").startswith("Opened 4 morning work tabs")
    assert len(osascript.calls) == 7
    assert all(kw.get("timeout") == 10 for kw in osascript.calls)


@pytest.mark.parametrize("skill,task,hang_at,expect", [
    ("chrome_scroll", "scroll down", 1, "Could not scroll"),
    ("chrome_scroll", "scroll up", 2, "Could not scroll"),
    ("chrome_automate", "close everything", 1, "Could not close the tabs"),
    ("chrome_automate", "morning tabs", 2, "Stopped after 1 of 4 tabs"),
])
def test_chrome_osascript_timeout_answers_short(osascript, skill, task, hang_at, expect):
    osascript.state["hang_at"] = hang_at
    out = load_skill(skill).run(task)
    assert out.startswith(expect) and "10 seconds" in out and "permission prompt" in out
    assert len(osascript.calls) == hang_at
