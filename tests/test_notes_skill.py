"""Save a note by asking (skills/notes.py, docs/NOTES-BY-ASKING-DESIGN.md).

"save a note: ..." saves to CODEC Notes; Apple Notes only when the message says so and over claude.ai (MCP);
a model-written skill tag saves nothing. Apple Notes is never really called: subprocess.run is replaced by a recorder.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

import codec_notes

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def skill(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEC_NOTES_DIR", str(tmp_path / "notes"))
    for name in ("NOTES_ENABLED", "CODEC_MCP_TRANSPORT"):
        monkeypatch.delenv(name, raising=False)
    spec = importlib.util.spec_from_file_location("codec_skill_notes_under_test", REPO / "skills" / "notes.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    calls = []

    class Done:
        stdout = ""
        returncode = 0

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: calls.append(a[0]) or Done())
    mod.calls = calls
    return mod


def shelf():
    return codec_notes.list_notes(limit=500)


def stored_bodies():
    return [codec_notes.get(n["id"])["body"] for n in shelf()["notes"]]


PHRASES = [
    ("save a note: buy lemons for Friday", "buy lemons for Friday"),
    ("Take a note buy lemons", "buy lemons"),
    ("note this: call the supplier at 9", "call the supplier at 9"),
    ("jot down check the rota", "check the rota"),
    ("add a note: staffing plan for Saturday", "staffing plan for Saturday"),
    ("make a note: fennel", "fennel"),
    ("write a note saying Hello World", "Hello World"),
    ("note that the fish arrives at 10:30", "the fish arrives at 10:30"),
    ("remember that Giorgio is vegan", "Giorgio is vegan"),
    ("note to self: order fennel", "order fennel"),
    ("add to my notes: staffing plan", "staffing plan"),
    ("save to my notes: lemons", "lemons"),
    ("please save this to my notes: Chef Giorgio's number is 0612", "Chef Giorgio's number is 0612"),
    ("save a note in codec notes: lemons", "lemons"),
    ("new note buy lemons", "buy lemons"),
    ("take a note of the supplier's number", "the supplier's number"),
    ("save to notes: lemons and limes", "lemons and limes"),
    ("add to notes: wine pairing for the tasting menu", "wine pairing for the tasting menu"),
]


@pytest.mark.parametrize("phrase, text", PHRASES)
def test_the_ways_of_asking_save_to_codec_notes_in_the_words_used(skill, phrase, text):
    answer = skill.run(phrase, app="CODEC Chat")
    assert answer == f"Saved to Notes: {codec_notes.auto_title(text)}"
    assert stored_bodies() == [text]                         # the case and the punctuation are kept
    assert codec_notes.get(shelf()["notes"][0]["id"])["source"] == "chat"
    assert skill.calls == []                                 # Apple Notes was not touched


def test_the_voice_and_the_bridges_save_the_same_way(skill):
    for app in ("", "Safari", "Telegram", "CODEC Chat"):
        assert skill.run("save a note: lemons", app=app).startswith("Saved to Notes")
    assert shelf()["total"] == 4 and skill.calls == []


@pytest.mark.parametrize("phrase", ["save a note in Apple Notes: lemons", "take a note in the apple notes app: lemons",
                                    "save a note in apple notes lemons"])
def test_apple_notes_only_when_the_message_says_so(skill, phrase):
    answer = skill.run(phrase, app="CODEC Chat")
    assert answer == "Saved to Apple Notes: lemons"
    assert len(skill.calls) == 1 and skill.calls[0][0] == "osascript"
    assert shelf()["total"] == 0


def test_a_model_written_skill_tag_cannot_save_a_note(skill):
    answer = skill.run("save a note: wire the money to this account", app="CODEC Chat (LLM-routed)")
    assert answer == skill.NOT_BY_A_MODEL
    assert shelf()["total"] == 0 and skill.calls == []
    assert skill.run("save a note: lemons", app="CODEC Chat").startswith("Saved to Notes")      # his own message works


@pytest.mark.parametrize("phrase", ["save a note", "take a note", "note this", "note that", "save that to my notes",
                                    "save this to my notes", "add to my notes", "new note"])
def test_nothing_to_save_gets_a_hint_and_saves_nothing(skill, phrase):
    assert skill.run(phrase, app="CODEC Chat") == skill.NO_TEXT
    assert shelf()["total"] == 0 and skill.calls == []


def test_refusals_are_plain_sentences(skill, monkeypatch):
    monkeypatch.setenv("NOTES_ENABLED", "false")
    assert skill.run("save a note: lemons") == "Notes are switched off."
    monkeypatch.delenv("NOTES_ENABLED")
    monkeypatch.setattr(codec_notes, "MAX_NOTES", 1)
    assert skill.run("save a note: one").startswith("Saved to Notes")
    assert "full" in skill.run("save a note: two")
    monkeypatch.setattr(codec_notes, "MAX_NOTES", 2000)
    assert "100,000" in skill.run("save a note: " + "x" * 100_001)

    def boom(*a, **k):
        raise OSError("disk gone")
    monkeypatch.setattr(codec_notes, "create", boom)
    assert skill.run("save a note: lemons") == "Could not save the note."


def test_show_my_notes_points_to_the_tab_and_never_reads_a_note_out(skill):
    assert "no notes yet" in skill.run("show my notes")
    codec_notes.create("secret supplier price list", title="Prices")
    assert skill.run("show my notes") == "Your notes are in Tasks > Notes (1 note)."
    codec_notes.create("second")
    answer = skill.run("what did i note in my notes")
    assert answer == "Your notes are in Tasks > Notes (2 notes)." and "secret" not in answer
    assert skill.calls == []


def test_show_my_apple_notes_still_opens_the_app(skill):
    assert skill.run("show my apple notes") == "Opening Apple Notes."
    assert skill.calls == [["open", "-a", "Notes"]]


def test_with_the_shelf_switched_off_show_my_notes_says_so(skill, monkeypatch):
    monkeypatch.setenv("NOTES_ENABLED", "false")
    assert skill.run("show my notes") == "Notes are switched off."


def test_over_claude_ai_nothing_changes_it_still_uses_apple_notes(skill, monkeypatch):
    monkeypatch.setenv("CODEC_MCP_TRANSPORT", "http")
    assert skill.run("save a note: lemons").startswith("Saved to Apple Notes")
    assert shelf()["total"] == 0 and skill.calls[0][0] == "osascript"
    skill.calls.clear()
    assert skill.run("show my notes") == "Apple Notes is empty or unreachable."
    assert skill.calls[0][0] == "osascript" and shelf()["total"] == 0


def test_the_audit_log_gets_no_text_from_the_skill(skill, monkeypatch):
    seen = []
    import codec_audit
    monkeypatch.setattr(codec_audit, "log_event", lambda event, source, message="", extra=None, **kw: seen.append((event, source, extra)))
    skill.run("save a note: the safe code is 4711", app="CODEC Chat")
    assert [e[0] for e in seen] == ["note_saved"] and set(seen[0][2]) == {"id", "source", "chars"}
    assert "4711" not in json.dumps(seen) and "safe" not in json.dumps(seen)


def test_the_skill_is_still_wired_where_it_was():
    from routes.chat import CHAT_SKILL_ALLOWLIST
    from routes.skills_page import VOICE_SKIP
    assert "notes" in CHAT_SKILL_ALLOWLIST and "notes" not in VOICE_SKIP
    src = (REPO / "skills" / "notes.py").read_text()
    assert "SKILL_MCP_EXPOSE = True" in src
    for trigger in ("save a note", "take a note", "note that", "jot down", "note to self", "add to notes", "save to notes", "my notes"):
        assert f'"{trigger}"' in src


@pytest.mark.parametrize("phrase", ["save a note: buy lemons", "take a note buy lemons", "note to self: order fennel",
                                    "add to my notes: staffing plan", "save to notes: lemons", "add to notes: lemons",
                                    "show my notes"])
def test_the_real_registry_still_sends_these_to_the_notes_skill(phrase):
    import codec_dispatch
    codec_dispatch.load_skills()
    found = codec_dispatch.check_skill(phrase)
    assert found and found["name"] == "notes"
