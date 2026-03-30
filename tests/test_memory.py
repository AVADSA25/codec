"""Test FTS5 memory system — CodecMemory and Session conversation persistence.

Uses temporary SQLite databases so tests are fully isolated and self-contained.
"""
import pytest
import sys
import os
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from codec_memory import CodecMemory


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path):
    """Return a path to a fresh temporary database file."""
    return str(tmp_path / "test_memory.db")


@pytest.fixture
def mem(tmp_db):
    """Return a CodecMemory wired to a temp database."""
    return CodecMemory(db_path=tmp_db)


# ── Import / Init ───────────────────────────────────────────────────────────


def test_memory_import():
    assert CodecMemory is not None


def test_memory_creates_tables(tmp_db):
    mem = CodecMemory(db_path=tmp_db)
    conn = sqlite3.connect(tmp_db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','trigger')"
    ).fetchall()}
    conn.close()
    assert "conversations" in tables
    assert "conversations_fts" in tables
    assert "conversations_ai" in tables  # insert trigger
    assert "conversations_ad" in tables  # delete trigger
    assert "conversations_au" in tables  # update trigger


# ── Save & Retrieve ─────────────────────────────────────────────────────────


def test_save_returns_rowid(mem):
    rid = mem.save("sess-1", "user", "hello world")
    assert isinstance(rid, int) and rid > 0


def test_save_and_search_roundtrip(mem):
    mem.save("sess-1", "user", "remember the capital of France is Paris")
    results = mem.search("Paris")
    assert len(results) >= 1
    assert any("Paris" in r["content"] for r in results)


def test_save_multiple_and_search(mem):
    mem.save("sess-1", "user", "apples are red")
    mem.save("sess-1", "assistant", "bananas are yellow")
    mem.save("sess-2", "user", "the sky is blue")

    red = mem.search("red")
    assert len(red) == 1
    assert "apples" in red[0]["content"]

    blue = mem.search("blue")
    assert len(blue) == 1
    assert "sky" in blue[0]["content"]


def test_search_returns_dict_fields(mem):
    mem.save("sess-1", "user", "important meeting notes")
    results = mem.search("meeting")
    assert len(results) == 1
    r = results[0]
    for key in ("id", "session_id", "timestamp", "role", "content", "score"):
        assert key in r, f"Missing key: {key}"
    assert r["session_id"] == "sess-1"
    assert r["role"] == "user"


def test_search_limit(mem):
    for i in range(20):
        mem.save("sess-1", "user", f"item number {i} with searchable token xyzzy")
    results = mem.search("xyzzy", limit=5)
    assert len(results) == 5


# ── Empty / No Results ──────────────────────────────────────────────────────


def test_search_empty_query(mem):
    mem.save("sess-1", "user", "some data")
    assert mem.search("") == []
    assert mem.search("   ") == []


def test_search_no_match(mem):
    mem.save("sess-1", "user", "hello world")
    results = mem.search("zzzznonexistent999")
    assert results == []


def test_search_on_empty_db(mem):
    results = mem.search("anything")
    assert results == []


# ── FTS Ranking ─────────────────────────────────────────────────────────────


def test_fts_bm25_ranking(mem):
    # The message that mentions "python" more should rank higher (lower BM25 score)
    mem.save("sess-1", "user", "I like python, python is great, python forever")
    mem.save("sess-1", "user", "java is also a language")
    results = mem.search("python")
    assert len(results) >= 1
    assert "python" in results[0]["content"].lower()


# ── Special Characters & Edge Cases ─────────────────────────────────────────


def test_special_characters_in_content(mem):
    special = "Hello! @#$%^&*() <script>alert('xss')</script> SELECT * FROM; DROP TABLE; --"
    mem.save("sess-1", "user", special)
    # Should not crash; content stored intact
    recent = mem.search_recent(days=1)
    assert len(recent) == 1
    assert recent[0]["content"] == special


def test_unicode_content(mem):
    text = "Bonjour le monde! Cafe, resume, naive. Chinese: \u4e2d\u6587"
    mem.save("sess-1", "user", text)
    results = mem.search("Bonjour")
    assert len(results) == 1


def test_newlines_and_whitespace(mem):
    text = "line one\nline two\n\ttabbed\n\n\nmultiple blanks"
    mem.save("sess-1", "user", text)
    results = mem.search("tabbed")
    assert len(results) == 1
    assert "\t" in results[0]["content"]


def test_very_long_text(mem):
    long_text = "word " * 5000  # ~25k chars; save() truncates to 4000
    mem.save("sess-1", "user", long_text)
    recent = mem.search_recent(days=1)
    assert len(recent) == 1
    assert len(recent[0]["content"]) <= 4000


def test_empty_content(mem):
    rid = mem.save("sess-1", "user", "")
    assert rid > 0
    # Empty content won't match FTS but should be in conversations table
    conn = sqlite3.connect(mem.db_path)
    row = conn.execute("SELECT content FROM conversations WHERE id=?", (rid,)).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == ""


def test_fts_special_query_characters(mem):
    """FTS5 operators like AND, OR, NOT, quotes should not crash search."""
    mem.save("sess-1", "user", "simple text for testing")
    # These could cause FTS syntax errors if not handled
    for q in ['"quoted phrase"', "AND", "OR NOT", "test*", "(parenthesized)"]:
        results = mem.search(q)
        assert isinstance(results, list)  # Should not raise


# ── search_recent ────────────────────────────────────────────────────────────


def test_search_recent(mem):
    mem.save("sess-1", "user", "recent message today")
    results = mem.search_recent(days=1)
    assert len(results) == 1
    assert results[0]["content"] == "recent message today"


def test_search_recent_empty_db(mem):
    assert mem.search_recent(days=7) == []


# ── get_context ──────────────────────────────────────────────────────────────


def test_get_context_returns_formatted_string(mem):
    mem.save("sess-1", "user", "context test message about rockets")
    ctx = mem.get_context("rockets", n=5)
    assert isinstance(ctx, str)
    assert "[Memory context]" in ctx
    assert "rockets" in ctx
    assert "USER" in ctx


def test_get_context_empty(mem):
    ctx = mem.get_context("nonexistent_query_xyz", n=5)
    assert ctx == ""


# ── get_sessions ─────────────────────────────────────────────────────────────


def test_get_sessions(mem):
    mem.save("sess-A", "user", "hello from A")
    mem.save("sess-A", "assistant", "hi back from A")
    mem.save("sess-B", "user", "hello from B")
    sessions = mem.get_sessions(limit=10)
    assert len(sessions) == 2
    ids = {s["session_id"] for s in sessions}
    assert ids == {"sess-A", "sess-B"}
    # sess-A should have 2 messages
    sess_a = next(s for s in sessions if s["session_id"] == "sess-A")
    assert sess_a["msg_count"] == 2


def test_get_sessions_limit(mem):
    for i in range(10):
        mem.save(f"sess-{i}", "user", f"msg {i}")
    sessions = mem.get_sessions(limit=3)
    assert len(sessions) == 3


def test_get_sessions_empty_db(mem):
    assert mem.get_sessions() == []


# ── cleanup ──────────────────────────────────────────────────────────────────


def test_cleanup_removes_old(mem):
    # Insert a message with an old timestamp directly
    conn = sqlite3.connect(mem.db_path)
    old_ts = (datetime.now() - timedelta(days=200)).isoformat()
    conn.execute(
        "INSERT INTO conversations (session_id, timestamp, role, content) VALUES (?,?,?,?)",
        ("old-sess", old_ts, "user", "very old message"),
    )
    conn.commit()
    conn.close()

    mem.save("new-sess", "user", "recent message")

    result = mem.cleanup(retention_days=90)
    assert result["deleted"] == 1
    assert result["remaining"] == 1
    assert "size_bytes" in result


def test_cleanup_keeps_recent(mem):
    mem.save("sess-1", "user", "fresh message")
    result = mem.cleanup(retention_days=1)
    assert result["deleted"] == 0
    assert result["remaining"] == 1


# ── rebuild_fts ──────────────────────────────────────────────────────────────


def test_rebuild_fts(mem):
    mem.save("sess-1", "user", "rebuild test message")
    mem.save("sess-1", "assistant", "another message")
    count = mem.rebuild_fts()
    assert count == 2


# ── FTS Sync via Triggers ───────────────────────────────────────────────────


def test_fts_trigger_on_insert(mem):
    """FTS should be in sync after insert (trigger)."""
    mem.save("sess-1", "user", "trigger test alpha beta gamma")
    results = mem.search("alpha beta gamma")
    assert len(results) == 1


def test_fts_trigger_on_delete(mem):
    """Deleting from conversations should remove from FTS (trigger)."""
    mem.save("sess-1", "user", "deleteme unique phrase qwerty")
    assert len(mem.search("deleteme unique phrase qwerty")) == 1

    conn = sqlite3.connect(mem.db_path)
    conn.execute("DELETE FROM conversations WHERE content LIKE '%deleteme%'")
    conn.commit()
    conn.close()

    assert len(mem.search("deleteme unique phrase qwerty")) == 0


def test_fts_trigger_on_update(mem):
    """Updating content should update FTS index (trigger)."""
    rid = mem.save("sess-1", "user", "original content xyzzy123")
    assert len(mem.search("xyzzy123")) == 1

    conn = sqlite3.connect(mem.db_path)
    conn.execute("UPDATE conversations SET content='updated content abcde789' WHERE id=?", (rid,))
    conn.commit()
    conn.close()

    assert len(mem.search("xyzzy123")) == 0
    assert len(mem.search("abcde789")) == 1


# ── Session Conversation Persistence ────────────────────────────────────────
# Tests for the Session.cleanup() method that saves conversation history


def test_session_cleanup_saves_conversations(tmp_db):
    """Session.cleanup() should persist self.h to the conversations table."""
    from codec_session import Session

    alive_file = tmp_db + ".alive"
    with open(alive_file, "w") as f:
        f.write("0")

    # Create conversations table (Session.run() normally does this)
    conn = sqlite3.connect(tmp_db)
    conn.execute("""CREATE TABLE IF NOT EXISTS conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT, timestamp TEXT, role TEXT, content TEXT
    )""")
    conn.commit()
    conn.close()

    sess = Session(
        sys_msg="You are a test assistant.",
        session_id="test-sess-001",
        qwen_base_url="http://localhost:1234/v1",
        qwen_model="test",
        qwen_vision_url="http://localhost:1234/v1",
        qwen_vision_model="test",
        tts_voice="alloy",
        llm_api_key="",
        llm_kwargs={},
        llm_provider="openai",
        tts_engine="disabled",
        kokoro_url="",
        kokoro_model="",
        db_path=tmp_db,
        task_queue=tmp_db + ".queue",
        session_alive=alive_file,
        streaming=False,
        agent_name="Test",
    )

    # Simulate a conversation
    sess.h = [
        {"role": "system", "content": "You are a test assistant."},
        {"role": "user", "content": "What is 2+2?"},
        {"role": "assistant", "content": "4"},
        {"role": "user", "content": "Thanks!"},
        {"role": "assistant", "content": "You're welcome."},
    ]

    sess.cleanup()

    # Verify: system messages should NOT be saved, others should
    conn = sqlite3.connect(tmp_db)
    rows = conn.execute("SELECT role, content FROM conversations ORDER BY id").fetchall()
    conn.close()

    assert len(rows) == 4  # 2 user + 2 assistant, no system
    roles = [r[0] for r in rows]
    assert "system" not in roles
    assert roles.count("user") == 2
    assert roles.count("assistant") == 2


def test_session_cleanup_truncates_long_content(tmp_db):
    """Session.cleanup() truncates content to 500 chars."""
    from codec_session import Session

    alive_file = tmp_db + ".alive"
    with open(alive_file, "w") as f:
        f.write("0")

    conn = sqlite3.connect(tmp_db)
    conn.execute("""CREATE TABLE IF NOT EXISTS conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT, timestamp TEXT, role TEXT, content TEXT
    )""")
    conn.commit()
    conn.close()

    sess = Session(
        sys_msg="test", session_id="test-sess-002",
        qwen_base_url="http://localhost:1234/v1", qwen_model="test",
        qwen_vision_url="http://localhost:1234/v1", qwen_vision_model="test",
        tts_voice="alloy", llm_api_key="", llm_kwargs={},
        llm_provider="openai", tts_engine="disabled",
        kokoro_url="", kokoro_model="",
        db_path=tmp_db, task_queue=tmp_db + ".queue",
        session_alive=alive_file, streaming=False, agent_name="Test",
    )

    long_content = "x" * 2000
    sess.h = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": long_content},
    ]

    sess.cleanup()

    conn = sqlite3.connect(tmp_db)
    row = conn.execute("SELECT content FROM conversations").fetchone()
    conn.close()

    assert len(row[0]) == 500


def test_session_corrections_roundtrip(tmp_db):
    """Session.detect_correction() saves and get_corrections() retrieves."""
    from codec_session import Session

    alive_file = tmp_db + ".alive"
    with open(alive_file, "w") as f:
        f.write("0")

    sess = Session(
        sys_msg="test", session_id="test-sess-003",
        qwen_base_url="http://localhost:1234/v1", qwen_model="test",
        qwen_vision_url="http://localhost:1234/v1", qwen_vision_model="test",
        tts_voice="alloy", llm_api_key="", llm_kwargs={},
        llm_provider="openai", tts_engine="disabled",
        kokoro_url="", kokoro_model="",
        db_path=tmp_db, task_queue=tmp_db + ".queue",
        session_alive=alive_file, streaming=False, agent_name="Test",
    )

    # Simulate prior conversation
    sess.h = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "open Safari"},
        {"role": "assistant", "content": "Opening Chrome for you"},
    ]

    # User corrects
    sess.detect_correction("no i meant Safari not Chrome")

    corr = sess.get_corrections()
    assert "CORRECTIONS" in corr
    assert "Safari" in corr or "open Safari" in corr


# ── Concurrent Access ────────────────────────────────────────────────────────


def test_concurrent_writes(tmp_db):
    """Multiple CodecMemory instances writing to the same DB should not corrupt it."""
    mem1 = CodecMemory(db_path=tmp_db)
    mem2 = CodecMemory(db_path=tmp_db)

    for i in range(10):
        mem1.save("sess-1", "user", f"mem1 message {i}")
        mem2.save("sess-2", "user", f"mem2 message {i}")

    results = mem1.search_recent(days=1, limit=100)
    assert len(results) == 20


# ── Backfill FTS ─────────────────────────────────────────────────────────────


def test_fts_backfill_on_init(tmp_db):
    """If conversations exist before FTS is created, init should backfill."""
    # Create conversations table and insert data BEFORE CodecMemory init
    conn = sqlite3.connect(tmp_db)
    conn.execute("""CREATE TABLE IF NOT EXISTS conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT, timestamp TEXT, role TEXT, content TEXT
    )""")
    conn.execute(
        "INSERT INTO conversations (session_id, timestamp, role, content) VALUES (?,?,?,?)",
        ("pre-sess", datetime.now().isoformat(), "user", "pre-existing message backfill test"),
    )
    conn.commit()
    conn.close()

    # Now init CodecMemory — it should create FTS and backfill
    mem = CodecMemory(db_path=tmp_db)
    results = mem.search("backfill")
    assert len(results) == 1
    assert "pre-existing" in results[0]["content"]
