"""Temporary chat (UI phase 2, P2.11; docs/P2.11-DESIGN.md).

The page never saves a temporary chat and sends temporary:true; the server then
skips memory recall, open threads and the observer summary, and the
memory-writing skills do not run on any chat path.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import routes.chat as chat

REPO = Path(__file__).resolve().parent.parent
PAGE = (REPO / "codec_chat.html").read_text(encoding="utf-8")
SRC = (REPO / "routes" / "chat.py").read_text(encoding="utf-8")


@pytest.fixture
def ran(monkeypatch):
    """Stub the skill machinery: record what would run, never run anything."""
    import codec_consent
    import codec_dispatch
    calls = []
    monkeypatch.setattr(codec_consent, "chat_consent_ok", lambda name, text: True)
    monkeypatch.setattr(codec_dispatch, "run_skill", lambda skill, task, app="": calls.append(skill["name"]) or "done")
    monkeypatch.setattr(codec_dispatch, "check_skill", lambda text: {"name": text.split()[0]})
    monkeypatch.setattr(chat, "CHAT_SKILL_ALLOWLIST", set(chat.CHAT_SKILL_ALLOWLIST) | set(chat.TEMPORARY_NO_MEMORY_SKILLS) | {"weather"})
    return calls


def test_the_memory_writing_skills():
    assert chat.TEMPORARY_NO_MEMORY_SKILLS == {"auto_memorize", "fact_extract", "thread_note", "standing_rules", "memory_save"}
    assert "temporary chat" in chat.TEMPORARY_REFUSAL and "no trace" not in chat.TEMPORARY_REFUSAL


@pytest.mark.parametrize("skill", sorted(chat.TEMPORARY_NO_MEMORY_SKILLS))
def test_a_trigger_match_and_a_tag_do_not_write_memory(ran, skill):
    assert chat._try_skill(f"{skill} I like tea", temporary=True) == (skill, chat.TEMPORARY_REFUSAL)
    assert chat._try_skill_by_name(skill, "I like tea", True) == (skill, chat.TEMPORARY_REFUSAL)
    assert ran == []
    assert chat._try_skill(f"{skill} I like tea") == (skill, "done"), "a normal chat still saves"
    assert chat._try_skill_by_name(skill, "I like tea") == (skill, "done")
    assert ran == [skill, skill]


def test_other_skills_still_run_in_a_temporary_chat(ran):
    assert chat._try_skill("weather in Lisbon", temporary=True) == ("weather", "done")
    assert chat._try_skill_by_name("weather", "Lisbon", True) == ("weather", "done")


def test_an_at_pick_does_not_write_memory(ran, monkeypatch):
    import routes.palette as palette
    monkeypatch.setattr(palette, "pickable_skills", lambda: {"fact_extract", "weather"})
    assert chat._try_explicit_skill("fact_extract", "I like tea", True) == ("fact_extract", chat.TEMPORARY_REFUSAL)
    assert ran == []
    assert chat._try_explicit_skill("weather", "Lisbon", True)[0] == "weather" and ran == ["weather"]


def test_no_memory_recall_in_a_temporary_chat(monkeypatch):
    import codec_memory
    import routes.qchat as qchat

    asked = []

    class FakeMemory:
        def get_context(self, text, n=8):
            asked.append("context")
            return "line one"

        def search_recent(self, days=3, limit=5):
            asked.append("recent")
            return [{"timestamp": "2026-09-28T10:00:00", "role": "user", "content": "the garden needs water"}]

    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.execute("CREATE TABLE qchat_messages (id INTEGER PRIMARY KEY, role TEXT, content TEXT, timestamp TEXT)")
    monkeypatch.setattr(codec_memory, "CodecMemory", FakeMemory)
    monkeypatch.setattr(qchat, "qchat_db", lambda: db)
    monkeypatch.setattr(chat, "_fetch_url_content", lambda url, max_chars=8000: "page text")
    meta: dict = {}
    msgs = [{"role": "user", "content": "do you remember the plumber"}]
    assert chat._enrich_messages(msgs, {}, meta=meta, temporary=True) == msgs
    assert asked == [] and meta == {}
    out = chat._enrich_messages([{"role": "user", "content": "read https://example.org/a"}], {}, temporary=True)
    assert len(out) == 2 and "page text" in out[0]["content"] and "[RECENT MEMORY" not in out[0]["content"].split(")", 1)[1]
    chat._enrich_messages(msgs, {}, meta=meta)
    assert asked, "a normal chat still recalls"


class _Budget:
    def warn_now(self):
        return False

    def consume(self, kind):
        return True

    def at_limit(self):
        return False


def test_no_observer_summary_or_open_threads_but_standing_rules_stay(monkeypatch):
    import codec_daybreak
    import codec_observer
    import codec_standing_rules
    import routes.prompts as prompts
    monkeypatch.setattr(prompts, "_load_prompt_overrides", lambda: {"chat": "BASE {date}"})
    monkeypatch.setattr(codec_standing_rules, "prompt_block", lambda record=False: "RULES: answer in French")
    monkeypatch.setattr(codec_standing_rules, "list_rules", lambda: ["answer in French"])
    monkeypatch.setattr(codec_daybreak, "get_working_context", lambda: "OPEN THREADS:\n- fix the gate")
    monkeypatch.setattr(codec_observer, "maybe_inject_observation_summary",
                        lambda **kw: ("OBSERVED: editing budget.xlsx", "ok"))
    temp = chat._build_chat_system_prompt({}, _Budget(), False, "continue", temporary=True)
    assert "RULES: answer in French" in temp
    assert "OPEN THREADS" not in temp and "OBSERVED" not in temp
    normal = chat._build_chat_system_prompt({}, _Budget(), False, "continue")
    assert "OPEN THREADS" in normal and "OBSERVED" in normal


def test_the_route_passes_the_flag_to_every_path():
    for needle in ('_temporary = bool(body.get("temporary"))',
                   "asyncio.to_thread(_try_explicit_skill, _explicit, last_user_text, _temporary)",
                   "asyncio.to_thread(_try_skill, last_user_text, _temporary)",
                   "temporary=_temporary)", "meta=_mem_meta, temporary=_temporary",
                   "_try_skill_by_name(s_name, s_query, _temporary)",
                   "asyncio.to_thread(_try_skill_by_name, s_name, s_query, _temporary)"):
        assert needle in SRC, needle


def test_the_page_toggle_label_and_save_skip():
    assert 'id="tempChatItem" role="menuitemcheckbox"' in PAGE and "Not saved to history or memory" in PAGE
    assert 'id="tempChip"' in PAGE and ".composer.temp{border-style:dashed" in PAGE
    assert "The audit log still records what CODEC does." in PAGE
    assert "no trace" not in PAGE[PAGE.index('id="tempChatItem"'):][:900].lower(), "an honest label"
    save = PAGE[PAGE.index("async function saveMessages("):]
    assert save.index("if(temporaryChat)return;") < save.index("fetch('/api/qchat/save'")
    assert PAGE.count("temporary:temporaryChat||undefined") == 2, "send and regenerate"
    assert PAGE.count("fetch('/api/chat'") == 2
    load = PAGE[PAGE.index("async function loadSession(sid){"):][:200]
    assert "if(temporaryChat)_setTemporary(false);" in load
    tog = PAGE[PAGE.index("function toggleTemporary(){"):PAGE.index("function toggleWebSearch(){")]
    assert "_setTemporary(!temporaryChat);\n  startNewSession();" in tog
    assert ".temp-chat .msg-up,.temp-chat .msg-down{display:none}" in PAGE, "no ratings without a saved chat"
