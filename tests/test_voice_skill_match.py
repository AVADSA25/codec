"""Voice call skill matching and hang-up (docs/VOICE-SKILL-MATCH-DESIGN.md).

A spoken statement that only contains a trigger phrase goes to the model; real
spoken commands still fire their skill; a turn still running when the socket
closes sends nothing more. Matching runs on the real built-in skill triggers,
but no skill code is imported (`load` is faked). Every sentence here is made up.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import codec_skill_registry  # noqa: E402
import codec_skill_switches  # noqa: E402
from codec_voice import VoicePipeline  # noqa: E402


@pytest.fixture
def reg(tmp_path, monkeypatch):
    monkeypatch.setattr(codec_skill_registry, "CUSTOM_TRIGGERS_PATH", str(tmp_path / "custom_triggers.json"))
    monkeypatch.setattr(codec_skill_switches, "is_off", lambda name: False)
    r = codec_skill_registry.SkillRegistry(str(REPO / "skills"))
    r.scan()
    r.load = lambda name: SimpleNamespace(run=lambda *a, **k: name)
    return r


def _voice(registry, skip=VoicePipeline._VOICE_SKIP_SKILLS):
    return SimpleNamespace(_skill_registry=registry, _VOICE_SKIP_SKILLS=skip)


def _fired(registry, text):
    m = VoicePipeline._match_skill(_voice(registry), text)
    return m and m["name"]


OPINIONS = [
    # "to do" in the middle of a long statement (the 11 Nov 2026 shape), and a short one
    "Robots decide what to do for us now and people stop thinking for themselves, which honestly worries me.",
    "I have so much to do this week that I barely sleep.",
    "My grandmother used to remind me to drink water every single morning when I lived with her in the village.",
    "I bought a kitchen timer for my brother because he always burns the toast when he cooks breakfast.",
    "It is crazy how hot it gets on the island in August, everyone just hides inside until the evening.",
    "Honestly the weather has been strange all week and it makes everyone in the office tired.",
    "People search for meaning on their phones instead of talking to each other at dinner, which is sad.",
    "The thing about voice assistants is they always want to look up something instead of just listening.",
]


@pytest.mark.parametrize("text", OPINIONS)
def test_an_opinion_that_contains_a_trigger_phrase_goes_to_the_model(reg, text):
    assert _fired(reg, text) is None


COMMANDS = [
    ("What's on my to do list?", "google_tasks"),
    ("What's on my to-do list?", "google_tasks"),
    ("Add a task buy milk.", "google_tasks"),
    ("Set a timer for five minutes.", "timer"),
    ("What's the weather?", "weather"),
    ("What's the weather like in Lisbon today?", "weather"),
    ("Remind me to call the bank tomorrow.", "google_calendar"),
    ("Search for the best pizza in Naples.", "web_search"),
    # long, but the sentence opens with the command (after the lead-ins)
    ("Add a task to call the plumber about the leaking kitchen sink before the weekend starts please.",
     "google_tasks"),
    ("Hey Codec, could you set a timer for twenty minutes because the bread needs to come out of the oven soon.",
     "timer"),
    # a short command, then a long sentence of context
    ("What's on my to do list? I want to plan the rest of my week before the meeting with the bank tomorrow.",
     "google_tasks"),
]


@pytest.mark.parametrize("text,skill", COMMANDS)
def test_a_spoken_command_still_fires_its_skill(reg, text, skill):
    assert _fired(reg, text) == skill


def test_the_chat_and_wake_word_matcher_no_longer_reads_to_do_as_the_task_list(reg):
    """Both use match_all_triggers with its defaults; the bare "to do" trigger is gone."""
    assert "google_tasks" not in reg.match_all_triggers(OPINIONS[0])
    assert "google_tasks" not in reg.match_all_triggers("Nothing to do today, so I will read.")
    assert reg.match_all_triggers("What's on my to do list?")[0] == "google_tasks"
    assert reg.match_all_triggers("show my todo list")[0] == "google_tasks"


def test_skip_list_switches_and_custom_triggers_are_honoured(reg, tmp_path, monkeypatch):
    calc = SimpleNamespace(match_all_triggers=lambda part, **kw: ["calculator", "timer"],
                           load=lambda name: SimpleNamespace(run=lambda *a, **k: name))
    assert VoicePipeline._match_skill(_voice(calc), "what is five times five")["name"] == "timer", "skip list"
    monkeypatch.setattr(codec_skill_switches, "is_off", lambda name: name == "timer")
    assert _fired(reg, "Set a timer for five minutes.") is None, "a skill switched off never fires"
    monkeypatch.setattr(codec_skill_switches, "is_off", lambda name: False)
    (tmp_path / "custom_triggers.json").write_text(json.dumps({"timer": {"triggers": ["Start The Egg Clock"]}}))
    reg.scan()
    assert _fired(reg, "Start the egg clock for the soup.") == "timer", "custom trigger, any case"
    assert _fired(reg, "Set a timer for five minutes.") is None, "the custom list replaces the built-in one"
    assert VoicePipeline._match_skill(_voice(None), "Set a timer for five minutes.") is None, "no registry, no skill"


# ── Hang-up: nothing is sent once the socket is closed ──────────────────────

class ClosingSocket:
    """A socket that raises like Starlette once the page has closed it."""

    def __init__(self):
        self.closed = False
        self.after_close = 0
        self.receive = AsyncMock()

    async def _send(self, _):
        if self.closed:
            self.after_close += 1
            raise RuntimeError('Cannot call "send" once a close message has been sent.')

    async def send_json(self, obj):
        await self._send(obj)

    async def send_bytes(self, data):
        await self._send(data)


def _pipeline():
    ws = ClosingSocket()
    with patch("codec_voice.VoicePipeline._load_skills"), \
            patch("codec_voice._build_system_prompt", return_value="sys"):
        p = VoicePipeline(ws)
    p.mode, p._awaiting_ask_user = "default", None
    p._poll_pending_question_for_voice = AsyncMock(return_value=None)
    p.dispatch_crew_from_voice = AsyncMock(return_value=None)
    p._match_skill = MagicMock(return_value=None)
    p.synthesize = AsyncMock(return_value=b"\x00" * 800)
    return p, ws


def _run_turns(p, *turns):
    async def go():
        for t in turns:
            p.utterance_queue.put_nowait(t)
        await p._pipeline()
    asyncio.run(go())


def test_end_call_during_a_reply_sends_nothing_after_it(caplog):
    p, ws = _pipeline()
    heard = []

    async def reply(text):
        heard.append(text)
        yield "Here is the first part. "
        # The owner hangs up: the page sends end_call, then closes the socket.
        ws.receive.side_effect = [{"type": "websocket.receive", "text": json.dumps({"type": "end_call"})}]
        await p._audio_receiver()
        ws.closed = True
        for token in ("And here ", "is more. ", "The end."):
            yield token

    p.generate_response = reply
    with caplog.at_level(logging.INFO, logger="codec_voice"):
        _run_turns(p, "tell me a story", "a turn queued behind it")
    assert ws.after_close == 0, "nothing is sent after end_call"
    assert heard == ["tell me a story"], "the queued turn is not run after the call ended"
    assert "Pipeline error" not in caplog.text


def test_a_socket_that_dies_mid_turn_stops_the_pipeline(caplog):
    p, ws = _pipeline()
    heard = []

    async def reply(text):
        heard.append(text)
        yield "Here is the first part. "
        ws.closed = True  # the page went away without end_call
        for token in ("And here ", "is more. ", "The end."):
            yield token

    p.generate_response = reply
    with caplog.at_level(logging.INFO, logger="codec_voice"):
        _run_turns(p, "tell me a story", "a turn queued behind it")
    assert ws.after_close == 1, "one send finds the socket closed, then nothing more is tried"
    assert heard == ["tell me a story"]
    assert "Pipeline error" not in caplog.text
