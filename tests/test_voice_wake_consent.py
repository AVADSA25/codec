"""P-080 / P-030: the wake word and the voice call ask before high-power skills;
chrome_fill, chrome_click_cdp and mouse_control ask locally but stay MCP-allowed.

codec_ask_user.ask is always patched: no PWA, no real pending-questions file.
"""
import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

import codec_ask_user
import codec_consent
import codec_dispatch

_REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def codec_mod():
    """This checkout's codec.py, loaded by path: other tests put ~/codec-repo
    first on sys.path or leave a stub in sys.modules."""
    spec = importlib.util.spec_from_file_location("_t_codec_wake", _REPO / "codec.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_t_codec_wake"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def licensed(monkeypatch):
    try:
        import codec_license
        monkeypatch.setattr(codec_license, "feature_allowed", lambda *_a: True)
    except ImportError:
        pass


@pytest.fixture
def asks(monkeypatch):
    """Record ask() calls and answer with whatever the test sets."""
    state = {"answer": "confirm", "calls": []}

    def fake_ask(question, **kw):
        state["calls"].append(kw)
        return state["answer"]

    monkeypatch.setattr(codec_ask_user, "ask", fake_ask)
    monkeypatch.setattr(codec_consent, "_audit_local", lambda *a, **k: None)
    return state


@pytest.mark.parametrize("skill,task", [
    ("terminal", "run ls"),
    ("pm2_control", "restart codec-telegram"),
    ("process_manager", "kill chrome"),
    ("imessage_send", "text mum hello"),
    ("file_ops", "write to file ~/Documents/a.txt hello"),
    ("file_ops", "append to file ~/Documents/a.txt hello"),
    ("chrome_fill", "fill the form"),
    ("chrome_click_cdp", "click submit"),
    ("mouse_control", "click the button"),
])
def test_high_power_skills_ask(skill, task):
    assert codec_consent.local_consent_needed(skill, task) is True


@pytest.mark.parametrize("skill,task", [
    ("file_ops", "list files in ~/Documents"),
    ("file_ops", "read file ~/Documents/a.txt"),
    ("weather", "weather in Marbella"),
    ("time", "what time is it"),
    ("music", "play music"),
    ("timer", "set a timer for 5 minutes"),
])
def test_ordinary_skills_never_ask(skill, task):
    assert codec_consent.local_consent_needed(skill, task) is False


@pytest.mark.parametrize("asked_from", ["wake", "voice"])
def test_local_consent_asks_with_60s_and_strict_mode(asks, asked_from):
    assert codec_consent.local_consent_ok("terminal", "run ls", asked_from) is True
    kw = asks["calls"][-1]
    assert kw["destructive"] is True and kw["asked_from"] == asked_from and kw["timeout"] == 60


@pytest.mark.parametrize("answer", [codec_ask_user.TIMEOUT_SENTINEL, codec_ask_user.DISABLED_SENTINEL])
def test_timeout_or_disabled_refuses(asks, answer):
    asks["answer"] = answer
    assert codec_consent.local_consent_ok("imessage_send", "text x", "wake") is False


def test_no_ask_for_ordinary_skill(asks):
    assert codec_consent.local_consent_ok("weather", "weather", "wake") is True
    assert asks["calls"] == []


def test_run_skill_refused_never_runs_the_skill(monkeypatch, licensed):
    ran = []
    monkeypatch.setattr(codec_dispatch.registry, "run", lambda name, t, c: ran.append(name) or "done")
    monkeypatch.setattr(codec_dispatch, "_skill_off", lambda n: False)
    out = codec_dispatch.run_skill({"name": "terminal", "_all_matches": ["terminal"]}, "run ls",
                                   consent=lambda n, t: False)
    assert out == codec_consent.LOCAL_CANCELLED
    assert ran == []


def test_run_skill_allowed_runs(monkeypatch, licensed):
    ran = []
    monkeypatch.setattr(codec_dispatch.registry, "run", lambda name, t, c: ran.append(name) or "done")
    monkeypatch.setattr(codec_dispatch, "_skill_off", lambda n: False)
    out = codec_dispatch.run_skill({"name": "terminal", "_all_matches": ["terminal"]}, "run ls",
                                   consent=lambda n, t: True)
    assert out == "done" and ran == ["terminal"]


# --- wake word (codec.py) ------------------------------------------------------

def test_wake_consent_asks_for_high_power(monkeypatch, codec_mod):
    codec = codec_mod
    spoken, decided = [], []
    monkeypatch.setattr(codec, "speak_text", lambda t: spoken.append(t))
    monkeypatch.setattr(codec_consent, "local_consent_ok",
                        lambda n, t, src, **k: decided.append((n, src)) or False)
    assert codec._wake_consent("pm2_control", "restart codec-telegram") is False
    assert decided == [("pm2_control", "wake")]
    assert spoken and "OK" in spoken[0]
    assert not codec._waiting_for_consent.is_set()


def test_wake_consent_skips_ordinary_skill(monkeypatch, codec_mod):
    codec = codec_mod
    monkeypatch.setattr(codec, "speak_text", lambda t: None)
    monkeypatch.setattr(codec_consent, "local_consent_ok",
                        lambda *a, **k: pytest.fail("must not ask"))
    assert codec._wake_consent("weather", "weather today") is True


def test_second_wake_command_while_waiting_is_refused_aloud(monkeypatch, codec_mod):
    codec = codec_mod
    spoken = []
    monkeypatch.setattr(codec, "speak_text", lambda t: spoken.append(t))
    monkeypatch.setattr(codec, "_dispatch_inner", lambda t: pytest.fail("must not dispatch"))
    assert codec._dispatch_lock.acquire(blocking=False)
    codec._waiting_for_consent.set()
    try:
        codec.dispatch("restart the dashboard")
    finally:
        codec._waiting_for_consent.clear()
        codec._dispatch_lock.release()
    assert spoken and "waiting" in spoken[0]


# --- voice call (codec_voice.py) -----------------------------------------------

class _FakePipeline:
    _cid = "cid-test"

    def __init__(self):
        self.spoken = []

    async def _speak(self, text):
        self.spoken.append(text)
        return True


def _voice_dispatch(monkeypatch, skill_name, allowed):
    import codec_voice
    ran = []
    monkeypatch.setattr(codec_consent, "local_consent_ok", lambda n, t, src, **k: allowed)
    monkeypatch.setattr(codec_voice, "_voice_run_with_hooks",
                        lambda **kw: kw["invoke"](kw["task"], ""))
    skill = {"name": skill_name, "run": lambda t: ran.append(t) or "ran it"}
    p = _FakePipeline()
    out = asyncio.run(codec_voice.VoicePipeline.dispatch_skill(p, skill, "do the thing"))
    return out, ran, p


def test_voice_call_declined_never_runs(monkeypatch):
    out, ran, p = _voice_dispatch(monkeypatch, "imessage_send", allowed=False)
    assert out == codec_consent.LOCAL_CANCELLED and ran == []
    assert p.spoken and "OK" in p.spoken[0]


def test_voice_call_allowed_runs(monkeypatch):
    out, ran, _ = _voice_dispatch(monkeypatch, "terminal", allowed=True)
    assert out == "ran it" and ran == ["do the thing"]


def test_voice_call_ordinary_skill_does_not_ask(monkeypatch):
    out, ran, p = _voice_dispatch(monkeypatch, "weather", allowed=False)
    assert out == "ran it" and p.spoken == []


# --- chat and MCP --------------------------------------------------------------

@pytest.mark.parametrize("skill", ["chrome_fill", "chrome_click_cdp", "mouse_control"])
def test_chat_asks_for_browser_and_screen_skills(asks, skill):
    asks["answer"] = codec_ask_user.TIMEOUT_SENTINEL
    assert codec_consent.chat_consent_ok(skill, "do it") is False
    assert asks["calls"]


@pytest.mark.parametrize("skill,allowed", [
    ("chrome_fill", True), ("chrome_click_cdp", True), ("mouse_control", True),
    ("file_ops", False), ("imessage_send", False), ("terminal", False),
])
def test_mcp_policy_unchanged(skill, allowed):
    assert codec_consent.mcp_allowed(skill) is allowed
