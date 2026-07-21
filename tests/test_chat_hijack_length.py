"""Long pastes must not auto-fire a skill.

Reproduces the 2026-07-21 "CODEC just stopped replying" incident.

A 6,305-char paste of standing rules — prose, containing the phrase "write it
with CODEC file_write to <path>" — matched the `file_ops` trigger. file_ops is
allowlisted for chat, so the match bypassed the conversational filter; file_ops
is destructive, so the consent gate called codec_ask_user.ask(), which BLOCKS
the request thread until answered. Nobody answered a panel they never saw, so
the HTTP request hung until the 600s timeout. The user saw the chat simply never
respond, and nothing was saved.

Evidence at the time: pending_questions.json held
  2026-07-21T10:08:17 status=timed_out "CODEC wants to run the 'file_ops' skill"

A paste is a document, not a command.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from routes import chat as rc  # noqa: E402


def test_cap_is_generous_enough_for_real_commands():
    """Real skill commands are short imperatives — the cap must not clip them."""
    real_commands = [
        "what was I doing 20 minutes ago?",
        "feed these prompts into Google Flow: 1. a drone shot over Marbella at "
        "golden hour 2. the same shot at night 3. a close up of the marina",
        "create a new skill that tells me the current moon phase",
        "what's the weather in Marbella",
    ]
    for c in real_commands:
        assert len(c) < rc.MAX_SKILL_HIJACK_CHARS, f"cap would clip a real command: {c[:40]}"


def test_long_paste_does_not_hijack(monkeypatch):
    """The incident: a long paste must fall through to the LLM untouched."""
    fired = []
    monkeypatch.setattr(
        "codec_dispatch.check_skill",
        lambda t: fired.append(t) or {"name": "file_ops"},
    )

    paste = (
        "Point 6 — Obsidian capture via CODEC:\n"
        "When a conversation has accumulated decisions worth keeping, offer once "
        "to save a summary. Only on my explicit yes, write it with CODEC "
        "file_write to [VAULT_PATH]/ChatLogs/YYYY-MM-DD-topic.md and confirm the "
        "written path back to me.\n"
    ) * 20                                   # ~6k chars, like the real paste
    assert len(paste) > rc.MAX_SKILL_HIJACK_CHARS

    name, result = rc._try_skill(paste)
    assert name is None and result is None, "a long paste must not fire a skill"
    assert not fired, "check_skill must not even be consulted for a long paste"


def test_short_command_still_reaches_dispatch(monkeypatch):
    """The cap must not disable the feature for normal-length messages."""
    seen = []

    def fake_check(t):
        seen.append(t)
        return {"name": "weather"}

    monkeypatch.setattr("codec_dispatch.check_skill", fake_check)
    monkeypatch.setattr("codec_dispatch.run_skill", lambda s, t, app="": "Sunny, 28C")
    monkeypatch.setattr("codec_consent.chat_consent_ok", lambda n, t: True)

    name, result = rc._try_skill("what's the weather in Marbella")
    assert seen, "short messages must still reach check_skill"
    assert name == "weather" and "Sunny" in result
