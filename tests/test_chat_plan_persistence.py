"""PR #39 — Verify the [CODEC_AGENT_PLAN:<id>] marker survives the
qchat save/load round-trip and is parseable by the same regex codec_chat.html
uses on the client.

Why this test exists:
The chat used to persist only "Project drafted: agent_xxx" as plain text
when the user dropped a project (codec_chat.html line 819 pre-PR-#39).
The plan card with approve/reject/view buttons lived only in the DOM, so
any reload (refresh, sidebar click, hard navigation) lost it.

PR #39 changes the persisted content to include a marker token:
    "Project drafted — agent_id <id>  [CODEC_AGENT_PLAN:<id>]"
On chat session load, JS scans assistant bubbles for the marker, fetches
the agent state from /api/agents/<id>, and re-renders the plan card.

This test verifies two invariants:
  1. SQLite (qchat_messages) preserves the marker byte-for-byte through
     INSERT then SELECT — no quoting, escaping, or sanitization mangles it.
  2. The Python equivalent of the JS regex (`AGENT_PLAN_MARKER_RE`)
     extracts the same agent_id the JS would, so the contract is locked.

The test does NOT import codec_dashboard (avoids the pynput import chain
that's environment-specific). It re-creates the qchat schema in a temp
sqlite and exercises the same INSERT/SELECT shape the production handler
uses (codec_dashboard.py:1453-1457 and 1440)."""
import re
import sqlite3
from pathlib import Path
from datetime import datetime


# Mirror of the JS regex in codec_chat.html:
#     var AGENT_PLAN_MARKER_RE=/\[CODEC_AGENT_PLAN:(agent_[a-z0-9]+)\]/;
PY_AGENT_PLAN_MARKER_RE = re.compile(r"\[CODEC_AGENT_PLAN:(agent_[a-z0-9]+)\]")


def _extract_agent_id(content: str) -> str:
    """Python equivalent of the JS extractAgentIdFromMessage(). Locked to
    the same regex so client + server tests agree."""
    if not content or not isinstance(content, str):
        return ""
    m = PY_AGENT_PLAN_MARKER_RE.search(content)
    return m.group(1) if m else ""


def _make_qchat_db(path: Path) -> sqlite3.Connection:
    """Create the qchat schema as it lives in codec_dashboard.qchat_db()."""
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute('''CREATE TABLE IF NOT EXISTS qchat_sessions (
        id TEXT PRIMARY KEY, title TEXT, created_at TEXT, updated_at TEXT,
        user_id TEXT DEFAULT 'default')''')
    conn.execute('''CREATE TABLE IF NOT EXISTS qchat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,
        content TEXT, timestamp TEXT, user_id TEXT DEFAULT 'default')''')
    conn.commit()
    return conn


def _save_message(conn, sid: str, role: str, content: str) -> None:
    """Mimic /api/qchat/save inserts (codec_dashboard.py:1456)."""
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO qchat_sessions (id, title, created_at, updated_at, user_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (sid, "test session", now, now, "default"),
    )
    conn.execute(
        "INSERT INTO qchat_messages (session_id, role, content, timestamp, user_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (sid, role, content, now, "default"),
    )
    conn.commit()


def _load_messages(conn, sid: str):
    """Mimic /api/qchat/session/{sid} (codec_dashboard.py:1440)."""
    rows = conn.execute(
        "SELECT role, content FROM qchat_messages WHERE session_id=? ORDER BY id ASC",
        (sid,),
    ).fetchall()
    return [{"role": r[0], "content": r[1]} for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_extract_agent_id_finds_marker():
    """Same shape the JS regex produces."""
    content = "Project drafted — agent_id agent_abc123def  [CODEC_AGENT_PLAN:agent_abc123def]"
    assert _extract_agent_id(content) == "agent_abc123def"


def test_extract_agent_id_returns_empty_when_no_marker():
    assert _extract_agent_id("Just a normal chat message") == ""
    assert _extract_agent_id("") == ""
    assert _extract_agent_id(None) == ""


def test_extract_agent_id_ignores_malformed_markers():
    # Wrong prefix — must be lowercase agent_
    assert _extract_agent_id("[CODEC_AGENT_PLAN:Agent_xyz]") == ""
    # Missing brackets
    assert _extract_agent_id("CODEC_AGENT_PLAN:agent_xyz") == ""
    # Missing agent_ prefix
    assert _extract_agent_id("[CODEC_AGENT_PLAN:xyz]") == ""


def test_marker_survives_qchat_save_then_load_roundtrip(tmp_path):
    """The end-to-end invariant PR #39 depends on: write a message with
    the marker, read it back, marker text is unchanged byte-for-byte."""
    conn = _make_qchat_db(tmp_path / "qchat.db")
    sid = "session_test_123"
    agent_id = "agent_1416ea3e1b02"
    written = (
        f"Project drafted — agent_id {agent_id}  [CODEC_AGENT_PLAN:{agent_id}]"
    )
    _save_message(conn, sid, "assistant", written)
    msgs = _load_messages(conn, sid)
    assert len(msgs) == 1
    assert msgs[0]["role"] == "assistant"
    # Byte-for-byte identical
    assert msgs[0]["content"] == written
    # And extractor finds the same id
    assert _extract_agent_id(msgs[0]["content"]) == agent_id


def test_multiple_messages_with_markers_in_one_session(tmp_path):
    """A session can have many project drops; each marker must round-trip
    independently and ordering is preserved."""
    conn = _make_qchat_db(tmp_path / "qchat.db")
    sid = "session_multi"
    ids = ["agent_aaa111", "agent_bbb222", "agent_ccc333"]
    for aid in ids:
        _save_message(conn, sid, "user", f"build me thing {aid[:6]}")
        _save_message(
            conn, sid, "assistant",
            f"Project drafted — agent_id {aid}  [CODEC_AGENT_PLAN:{aid}]",
        )
    msgs = _load_messages(conn, sid)
    # 3 user + 3 assistant
    assert len(msgs) == 6
    assistant_ids = [
        _extract_agent_id(m["content"])
        for m in msgs if m["role"] == "assistant"
    ]
    assert assistant_ids == ids   # ordering preserved


def test_marker_extracts_real_world_agent_id_format(tmp_path):
    """Production agent_ids are 12 hex chars (e.g. agent_1416ea3e1b02
    seen in the 2026-05-03 forex audit). The regex `[a-z0-9]+` matches
    that exactly. Underscores in the id portion correctly DO NOT match —
    pins the contract that ids are hex-only after the agent_ prefix."""
    conn = _make_qchat_db(tmp_path / "qchat.db")
    sid = "session_edge"
    real_id = "agent_1416ea3e1b02"
    _save_message(
        conn, sid, "assistant",
        f"Project drafted — agent_id {real_id}  [CODEC_AGENT_PLAN:{real_id}]",
    )
    msgs = _load_messages(conn, sid)
    assert _extract_agent_id(msgs[0]["content"]) == real_id

    # Underscored "ids" are correctly rejected — locks the format
    assert _extract_agent_id("[CODEC_AGENT_PLAN:agent_with_underscore]") == ""
