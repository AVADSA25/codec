"""Chat and wake word: a trigger phrase in the middle of a long statement fires no
skill (S-08, docs/CHAT-WAKE-SKILL-MATCH-DESIGN.md).

The rule is the voice call's (#598, codec_voice_filters.command_parts): a trigger
counts anywhere in a sentence of at most 15 words, or when it opens a longer one.
Matching runs on the real built-in skill triggers, but no skill code is imported or
run: codec_dispatch's registry is one built over skills/ with custom triggers in
tmp_path, and run_skill is a fake. Every sentence here is made up.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import codec_consent
import codec_dispatch
import codec_skill_registry

REPO = Path(__file__).resolve().parent.parent

# A trigger phrase in the middle of a long statement (more than 15 words).
STATEMENTS = [
    ("I keep telling my brother that he should search for a better job instead of "
     "complaining about his boss all the time.", "web_search"),
    ("My grandmother used to remind me to drink water every single morning when I "
     "lived with her in the village.", "google_calendar"),
    ("I bought a kitchen timer for my brother because he always burns the toast when "
     "he cooks breakfast.", "timer"),
]

# The same phrases as commands: short, or opening a long message (also after lead-ins).
COMMANDS = [
    ("search for flights to Lisbon", "web_search"),
    ("Search for the best Italian restaurants near the old town that are open late on "
     "Sunday evenings with a terrace.", "web_search"),
    ("remind me to call the bank tomorrow", "google_calendar"),
    ("Hey codec, could you remind me to call the bank tomorrow morning about the "
     "mortgage papers they sent last week.", "google_calendar"),
    ("set a timer for 5 minutes", "timer"),
    ("Set a timer for twenty minutes because the bread needs to come out of the oven "
     "before the guests arrive.", "timer"),
]

QUESTIONS = ["what was I doing?", "what was I doing 20 minutes ago?",
             "hey, what was I working on this morning?",
             "What was I doing an hour ago, before the call with the bank about the mortgage papers?"]


@pytest.fixture
def reg(tmp_path, monkeypatch):
    monkeypatch.setattr(codec_skill_registry, "CUSTOM_TRIGGERS_PATH", str(tmp_path / "custom_triggers.json"))
    r = codec_skill_registry.SkillRegistry(str(REPO / "skills"))
    r.scan()
    r.load = lambda name: pytest.fail(f"skill code must not load: {name}")
    monkeypatch.setattr(codec_dispatch, "registry", r)
    monkeypatch.setattr(codec_dispatch, "_skill_off", lambda name: False)
    return r


def _first(text, **kw):
    m = codec_dispatch.check_skill(text, **kw)
    return m and m["name"]


# ── The matcher (codec_dispatch.check_skill) ─────────────────────────────────────

@pytest.mark.parametrize("text,skill", STATEMENTS)
def test_a_statement_with_a_trigger_phrase_in_the_middle_matches_nothing(reg, text, skill):
    assert len(text.split()) > 15
    assert _first(text, commands_only=True) is None
    assert _first(text) == skill, "control: the default whole-text match still finds it"


@pytest.mark.parametrize("text,skill", COMMANDS)
def test_the_same_phrase_as_a_command_still_matches(reg, text, skill):
    assert _first(text, commands_only=True) == skill


@pytest.mark.parametrize("text", QUESTIONS)
def test_what_was_i_doing_still_matches(reg, text):
    assert _first(text, commands_only=True) == "observer_recall"


def test_a_long_question_that_ends_with_the_phrase_goes_to_the_model(reg):
    text = "I was in the middle of something earlier this afternoon and now I forgot, what was I doing?"
    assert len(text.split()) > 15 and _first(text) == "observer_recall"
    assert _first(text, commands_only=True) is None


def test_a_short_command_after_a_long_sentence_still_matches(reg):
    text = ("I have been planning the dinner for the whole week and I am still worried about "
            "the cake. Set a timer for 40 minutes.")
    assert _first(text, commands_only=True) == "timer"


def test_a_switched_off_skill_never_matches(reg, monkeypatch):
    monkeypatch.setattr(codec_dispatch, "_skill_off", lambda name: name == "timer")
    assert _first("set a timer for 5 minutes", commands_only=True) is None


# ── The chat's pre-LLM match (routes/chat._try_skill) ────────────────────────────

@pytest.fixture
def ran(reg, monkeypatch):
    """run_skill records (names, task) and answers; no skill runs."""
    calls = []

    def run_skill(skill, task, app="", **kw):
        calls.append((list(skill.get("_all_matches") or [skill["name"]]), task))
        return f"RAN {skill['name']}"

    monkeypatch.setattr(codec_dispatch, "run_skill", run_skill)
    monkeypatch.setattr(codec_consent, "chat_consent_ok", lambda name, task: True)
    return calls


@pytest.mark.parametrize("text,skill", STATEMENTS)
def test_chat_a_long_statement_runs_no_skill(ran, text, skill):
    import routes.chat as chat
    assert chat._try_skill(text, search=True) == (None, None)
    assert ran == []


@pytest.mark.parametrize("text,skill", COMMANDS)
def test_chat_a_command_still_runs(ran, text, skill):
    import routes.chat as chat
    assert chat._try_skill(text, search=True) == (skill, f"RAN {skill}")
    assert ran and ran[0][1] in (text, chat._web_search_task(text)), "the skill gets the message"


@pytest.mark.parametrize("text", QUESTIONS)
def test_chat_what_was_i_doing_still_runs_observer_recall(ran, text):
    import routes.chat as chat
    assert chat._try_skill(text) == ("observer_recall", "RAN observer_recall")


# ── The wake word (codec._dispatch_inner) ────────────────────────────────────────

@pytest.fixture(scope="module")
def codec_mod():
    """This checkout's codec.py, loaded by path (as tests/test_voice_wake_consent.py does)."""
    spec = importlib.util.spec_from_file_location("_t_codec_s08", REPO / "codec.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_t_codec_s08"] = mod
    spec.loader.exec_module(mod)
    return mod


class _ToModel(Exception):
    """Raised where _dispatch_inner leaves the skill block for the model."""


@pytest.fixture
def wake(reg, monkeypatch, codec_mod):
    import codec_memory
    codec = codec_mod
    st = SimpleNamespace(runs=[], spoken=[])

    def run_skill(skill, task, app="", consent=None, **kw):
        st.runs.append((skill["name"], task, consent))
        return f"RAN {skill['name']}"

    def to_model(task):
        raise _ToModel(task)

    monkeypatch.setattr(codec, "run_skill", run_skill)
    monkeypatch.setattr(codec, "is_draft", to_model)
    monkeypatch.setattr(codec, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(codec, "focused_app", lambda: "Finder")
    monkeypatch.setattr(codec, "push", lambda fn: None)
    monkeypatch.setattr(codec, "speak_text", st.spoken.append)
    monkeypatch.setattr(codec, "subprocess", SimpleNamespace(Popen=lambda *a, **k: None, DEVNULL=subprocess.DEVNULL))
    monkeypatch.setattr(codec, "threading", SimpleNamespace(Thread=lambda **k: SimpleNamespace(start=lambda: None)))
    monkeypatch.setattr(codec, "voice_session", {"messages": [], "turn_count": 0, "started": None})
    monkeypatch.setattr(codec_memory, "CodecMemory", lambda: SimpleNamespace(save=lambda *a: None))
    st.codec = codec
    return st


@pytest.mark.parametrize("text,skill", STATEMENTS)
def test_wake_a_long_statement_runs_no_skill_and_goes_to_the_model(wake, text, skill):
    with pytest.raises(_ToModel):
        wake.codec._dispatch_inner(text)
    assert wake.runs == [] and wake.spoken == []


@pytest.mark.parametrize("text,skill", COMMANDS + [(q, "observer_recall") for q in QUESTIONS])
def test_wake_a_command_still_runs_through_the_consent_gate(wake, text, skill):
    wake.codec._dispatch_inner(text)
    assert wake.runs == [(skill, text, wake.codec._wake_consent)]
    assert wake.spoken == [f"RAN {skill}"]
