"""Voice call inside a chat (docs/VOICE-IN-CHAT-AND-VIBE-PANES-DESIGN.md, part 1).

A call started from a chat reads the chat's newest lines into its system prompt, and adds its own
turns to that chat (source 'voice') when the socket closes. Every database here is a temp file.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import codec_voice
from routes import qchat

REPO = Path(__file__).resolve().parent.parent
CHAT = (REPO / "codec_chat.html").read_text(encoding="utf-8")
VOICE = (REPO / "codec_voice.html").read_text(encoding="utf-8")


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "qchat.db"
    monkeypatch.setattr(qchat, "QCHAT_DB", str(path))
    monkeypatch.setattr(qchat, "_qchat_conn", None)
    yield path
    if qchat._qchat_conn is not None:
        qchat._qchat_conn.close()
    monkeypatch.setattr(qchat, "_qchat_conn", None)


def _add(sid, rows):
    conn = qchat.qchat_db()
    conn.execute("INSERT OR IGNORE INTO qchat_sessions (id, title, created_at, updated_at) VALUES (?, 't', 'x', 'x')", (sid,))
    for role, text in rows:
        conn.execute("INSERT INTO qchat_messages (session_id, role, content, timestamp) VALUES (?, ?, ?, 'x')", (sid, role, text))
    conn.commit()


def _pipeline(monkeypatch, chat_id=None, link=True, resume=None):
    monkeypatch.setattr(codec_voice, "_chat_link_enabled", lambda: link)
    ws = AsyncMock()
    with patch("codec_voice.VoicePipeline._load_skills"), patch("codec_voice._build_system_prompt", return_value="sys"):
        p = codec_voice.VoicePipeline(ws, resume_session_id=resume, chat_id=chat_id)
    p._disconnect_reason = "user"
    return p


# ── the chat database ──────────────────────────────────────────────────

def test_context_takes_the_newest_lines_cut_to_size_oldest_dropped_first(db):
    _add("c1", [("user", f"line {i}") for i in range(20)] + [("system", "never"), ("assistant", "x" * 2000)])
    rows = qchat.voice_context_rows("c1")
    assert len(rows) == 12 and rows[-1][0] == "assistant"
    assert rows[-1][1].startswith("x" * 800) and rows[-1][1].endswith(" ...") and len(rows[-1][1]) == 804
    assert rows[0] == ("user", "line 9"), "oldest first, the 12 newest"
    assert all(r != "system" for r, _ in rows)
    _add("c2", [("user", "y" * 790)] * 8)
    assert sum(len(t) for _, t in qchat.voice_context_rows("c2")) <= 4000


def test_context_is_empty_for_a_bad_id_an_unknown_chat_or_superseded_rows(db):
    assert qchat.voice_context_rows("../etc") == [] and qchat.voice_context_rows("") == []
    assert qchat.voice_context_rows("nobody") == []
    _add("c3", [("user", "old")])
    qchat.qchat_db().execute("UPDATE qchat_messages SET superseded_at='now' WHERE session_id='c3'")
    assert qchat.voice_context_rows("c3") == []


def test_the_source_column_is_added_once_after_a_backup(db):
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE qchat_sessions (id TEXT PRIMARY KEY, title TEXT, created_at TEXT, updated_at TEXT)")
    conn.execute("CREATE TABLE qchat_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT, timestamp TEXT)")
    conn.execute("INSERT INTO qchat_messages (session_id, role, content, timestamp) VALUES ('s', 'user', 'hi', 'x')")
    conn.commit()
    conn.close()
    c = qchat.qchat_db()
    assert "source" in qchat._columns(c, "qchat_messages")
    assert Path(str(db) + ".bak-voice").exists(), "copy before the change"
    assert c.execute("SELECT content, source FROM qchat_messages").fetchall() == [("hi", None)]


def test_a_call_is_added_to_its_chat_marked_voice_and_the_session_shows_it(db):
    _add("c4", [("user", "typed")])
    assert qchat.append_voice_turns("c4", [("user", "said"), ("assistant", "answered"), ("system", "no"), ("user", "  ")]) == 2
    app = FastAPI()
    app.include_router(qchat.router)
    out = TestClient(app).get("/api/qchat/session/c4").json()
    assert [(m["role"], m["content"], m.get("source")) for m in out] == [
        ("user", "typed", None), ("user", "said", "voice"), ("assistant", "answered", "voice")]


def test_a_call_from_a_new_chat_creates_it_titled_with_the_first_thing_said(db):
    assert qchat.append_voice_turns("fresh_1", [("assistant", "hello"), ("user", "plan my week")]) == 2
    assert qchat.qchat_db().execute("SELECT title FROM qchat_sessions WHERE id='fresh_1'").fetchone()[0] == "plan my week"
    assert qchat.append_voice_turns("bad id!", [("user", "x")]) == 0
    assert qchat.append_voice_turns("c5", []) == 0


# ── the call ───────────────────────────────────────────────────────────

def test_a_linked_call_has_the_chat_in_its_system_prompt_fenced_as_data(db, monkeypatch):
    _add("c6", [("user", "We said blue"), ("assistant", "Blue it is")])
    p = _pipeline(monkeypatch, "c6")
    sysmsg = p.messages[0]["content"]
    assert sysmsg.startswith("sys") and "EARLIER IN THIS CHAT" in sysmsg
    assert "User: We said blue" in sysmsg and "CODEC: Blue it is" in sysmsg
    assert re.search(r"[0-9a-f]{12}", sysmsg), "inside a codec_fence marker"
    with patch("codec_voice._build_system_prompt", return_value="sys"):
        assert p._system_base() == sysmsg, "kept when the prompt is rebuilt each turn"


def test_no_chat_block_with_the_switch_off_a_bad_id_or_no_id(db, monkeypatch):
    _add("c7", [("user", "hi")])
    for p in (_pipeline(monkeypatch, "c7", link=False), _pipeline(monkeypatch, "../x"), _pipeline(monkeypatch, None)):
        assert p.messages[0]["content"] == "sys" and p.chat_id is None
        assert p.write_back_to_chat() == 0


def test_only_the_calls_own_turns_go_back_never_the_greeting_or_a_line_twice(db, monkeypatch):
    _add("c8", [("user", "typed first")])
    p = _pipeline(monkeypatch, "c8")
    p._chat_skip.add(len(p.messages))
    p.messages.append({"role": "assistant", "content": "Greetings. CODEC is online."})
    p.messages += [{"role": "user", "content": "what did we say"},
                   {"role": "assistant", "content": "<think>hmm</think>Blue. [SKILL:time:now]"},
                   {"role": "system", "content": "tool note"}]
    assert p.write_back_to_chat() == 2
    assert p.write_back_to_chat() == 0, "nothing new"
    rows = qchat.qchat_db().execute("SELECT role, content, source FROM qchat_messages WHERE session_id='c8' ORDER BY id").fetchall()
    assert rows == [("user", "typed first", None), ("user", "what did we say", "voice"), ("assistant", "Blue.", "voice")]


def test_a_reconnect_does_not_write_a_line_twice(db, monkeypatch):
    _add("c9", [("user", "typed")])
    p = _pipeline(monkeypatch, "c9")
    p.messages += [{"role": "user", "content": "one"}, {"role": "assistant", "content": "two"}]
    p._disconnect_reason = "unexpected"
    p._save_for_resume()
    assert p.write_back_to_chat() == 2
    q = _pipeline(monkeypatch, "c9", resume=p.session_id)
    assert q._is_resumed
    q._chat_skip.add(len(q.messages))
    q.messages.append({"role": "assistant", "content": "Reconnected. I'm still here. Go ahead."})
    q.messages += [{"role": "user", "content": "three"}]
    assert q.write_back_to_chat() == 1
    got = [r[0] for r in qchat.qchat_db().execute("SELECT content FROM qchat_messages WHERE session_id='c9' ORDER BY id")]
    assert got == ["typed", "one", "two", "three"]
    assert p.session_id not in codec_voice.VoicePipeline._chat_progress, "hung up: the count is dropped"


def test_the_route_passes_the_chat_id_and_the_page_sends_no_chat_text(monkeypatch):
    from routes import websocket as wsmod
    seen = []

    class FakePipeline:
        def __init__(self, ws, resume_session_id=None, client_pcm=False, chat_id=None):
            seen.append(chat_id)

        async def run(self):
            return None

        def save_to_memory(self):
            pass

        async def close(self):
            pass
    monkeypatch.setattr(codec_voice, "VoicePipeline", FakePipeline)
    monkeypatch.setattr(wsmod, "_ws_authorized", lambda ws: True)
    app = FastAPI()
    app.include_router(wsmod.router)
    client = TestClient(app)
    for url in ("/ws/voice?pcm=1", "/ws/voice?pcm=1&chat=abc_1"):
        with client.websocket_connect(url, headers={"origin": "http://testserver"}):
            pass
    assert seen == [None, "abc_1"]


# ── the pages ──────────────────────────────────────────────────────────

def test_chat_has_a_phone_button_that_links_the_call_by_id_only():
    btn = re.search(r'<button[^>]*id="voiceCallBtn"[^>]*>', CHAT).group(0)
    assert 'onclick="startVoiceCall()"' in btn and "aria-label=" in btn
    fn = CHAT[CHAT.index("function startVoiceCall()"):CHAT.index("function _markVoiceRow(")]
    assert "if(temporaryChat||!_CHAT_ID_RE.test(String(sessionId||''))){location.href='/voice?auto=1';return}" in fn
    assert "location.href='/voice?auto=1&chat='+encodeURIComponent(sessionId);" in fn


def test_chat_marks_voice_rows_with_text_never_html():
    fn = CHAT[CHAT.index("function _markVoiceRow("):CHAT.index("function startNewSession()")]
    assert "data[i].source!=='voice'" in fn and "t.textContent='Voice'" in fn and "d.textContent='Voice call'" in fn
    assert "innerHTML" not in fn
    assert CHAT.count("_markVoiceRow(data,i,addMessage(") == 2, "both ways a chat is drawn"


def test_voice_page_passes_the_chat_on_and_goes_back_to_it_after_hanging_up():
    assert "return /^[A-Za-z0-9_-]{1,80}$/.test(c) ? c : '';" in VOICE
    assert "if (LINKED_CHAT) url += '&chat=' + encodeURIComponent(LINKED_CHAT);" in VOICE
    assert "location.href = '/chat#session=' + encodeURIComponent(LINKED_CHAT);" in VOICE
    end = VOICE[VOICE.index("function endCall()"):VOICE.index("function endCall()") + 2200]
    assert "setTimeout(function () { try { w.close(); } catch (e) {} backToChat(); }, 4000);" in end
    assert 'id="chatLink" hidden' in VOICE
