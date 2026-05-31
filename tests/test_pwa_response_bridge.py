"""Tests for PR-4B (C-2) — PWA response bridge moved off the racy
`~/.codec/pwa_response.json` file onto a DB-only path correlated on the
`conversations.id` autoincrement (server-authoritative).

The endpoint (`/api/response`) is auth-gated and TestClient-skipped in CI, so
the unit under test is the extracted pure helper
`codec_dashboard._latest_response_for_session(db, session_id, after_id,
after_ts)`. We drive it against a throwaway sqlite DB whose `conversations`
table mirrors the real schema (`codec_memory.py:61-65`), plus source-level
invariants proving the file path is gone.

Reference: docs/PR4B-PWA-RESPONSE-BRIDGE-DESIGN.md, docs/audits/PHASE-1-RELIABILITY.md C-2.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import codec_dashboard  # noqa: E402

_latest = codec_dashboard._latest_response_for_session


# ── DB harness ───────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    """In-memory conversations table matching the production schema + Row factory
    (so `row[0]` works exactly like routes/_shared.get_db())."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE conversations ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " session_id TEXT, timestamp TEXT, role TEXT, content TEXT,"
        " user_id TEXT DEFAULT 'default')"
    )
    conn.commit()
    return conn


def _add(conn, session_id, role, content, ts="2026-05-23T10:00:00"):
    cur = conn.execute(
        "INSERT INTO conversations (session_id, timestamp, role, content) VALUES (?,?,?,?)",
        (session_id, ts, role, content),
    )
    conn.commit()
    return cur.lastrowid


# ── after_id correlation (the core fix) ──────────────────────────────────────


def test_after_id_returns_this_turn_reply(db):
    uid = _add(db, "s1", "user", "what time is it?")
    _add(db, "s1", "assistant", "It's 10am")
    assert _latest(db, "s1", after_id=uid) == "It's 10am"


def test_after_id_excludes_rows_at_or_below_marker(db):
    # An OLD assistant reply from earlier in the same session must not leak.
    _add(db, "s1", "assistant", "stale old reply")  # id=1
    uid = _add(db, "s1", "user", "new question")    # id=2
    _add(db, "s1", "assistant", "fresh reply")      # id=3
    got = _latest(db, "s1", after_id=uid)
    assert got == "fresh reply"
    assert got != "stale old reply"


def test_after_id_none_until_reply_written(db):
    uid = _add(db, "s1", "user", "pending question")
    # No assistant row yet → poll should see nothing (not the user's own row).
    assert _latest(db, "s1", after_id=uid) is None


def test_sequential_turns_each_get_own_reply(db):
    u1 = _add(db, "s1", "user", "q1")        # id=1
    _add(db, "s1", "assistant", "a1")        # id=2
    u2 = _add(db, "s1", "user", "q2")        # id=3
    _add(db, "s1", "assistant", "a2")        # id=4
    assert _latest(db, "s1", after_id=u1) == "a1"
    assert _latest(db, "s1", after_id=u2) == "a2"


def test_asc_tiebreak_returns_immediate_next_not_newest(db):
    # Locks the ASC decision: two assistant rows both above the marker →
    # the immediate-next (smallest id) wins, not the newest.
    uid = _add(db, "s1", "user", "q")          # id=1
    _add(db, "s1", "assistant", "first")       # id=2
    _add(db, "s1", "assistant", "later")       # id=3
    assert _latest(db, "s1", after_id=uid) == "first"


def test_after_id_scoped_to_session(db):
    _add(db, "other", "user", "x")             # id=1
    _add(db, "other", "assistant", "leak?")    # id=2
    uid = _add(db, "s1", "user", "q")          # id=3
    _add(db, "s1", "assistant", "mine")        # id=4
    assert _latest(db, "s1", after_id=uid) == "mine"


# ── legacy after_ts fallback (un-refreshed PWA tab) ──────────────────────────


def test_legacy_after_ts_returns_newest_after_timestamp(db):
    _add(db, "s1", "assistant", "old", ts="2026-05-23T09:00:00")
    _add(db, "s1", "assistant", "new", ts="2026-05-23T11:00:00")
    assert _latest(db, "s1", after_id="", after_ts="2026-05-23T10:00:00") == "new"


def test_legacy_after_ts_none_when_nothing_newer(db):
    _add(db, "s1", "assistant", "old", ts="2026-05-23T09:00:00")
    assert _latest(db, "s1", after_id="", after_ts="2026-05-23T10:00:00") is None


def test_after_id_preferred_over_after_ts(db):
    # When both markers are present, the server-authoritative id wins.
    uid = _add(db, "s1", "user", "q")                       # id=1
    _add(db, "s1", "assistant", "by_id", ts="2026-05-23T08:00:00")  # id=2, older ts
    assert _latest(db, "s1", after_id=uid, after_ts="2026-05-23T23:59:00") == "by_id"


# ── defensive contract ───────────────────────────────────────────────────────


def test_empty_session_returns_none(db):
    _add(db, "s1", "assistant", "x")
    assert _latest(db, "", after_id="1") is None


def test_no_markers_returns_none(db):
    _add(db, "s1", "assistant", "x")
    assert _latest(db, "s1", after_id="", after_ts="") is None


def test_closed_db_never_raises(db):
    db.close()
    assert _latest(db, "s1", after_id="1") is None


def test_zero_after_id_is_treated_as_absent(db):
    # "0" / 0 must not select id>0 (every row) — it means "no id marker".
    _add(db, "s1", "assistant", "x")
    assert _latest(db, "s1", after_id="0") is None
    assert _latest(db, "s1", after_id=0) is None


# ── source-level invariants ──────────────────────────────────────────────────


def test_pwa_response_file_path_removed():
    # Target the actual code usage (the path construction + the variable),
    # not prose — comments may still reference the name historically
    # ("replaces the racy pwa_response.json"), which is good documentation.
    src = (REPO / "codec_dashboard.py").read_text()
    assert 'expanduser("~/.codec/pwa_response.json")' not in src, (
        "C-2: the ~/.codec/pwa_response.json path must no longer be opened/expanded"
    )
    assert "resp_file" not in src, (
        "C-2: the resp_file writer/reader must be gone from codec_dashboard.py"
    )


def test_command_returns_request_id():
    src = (REPO / "codec_dashboard.py").read_text()
    assert '"request_id"' in src, (
        "/api/command must return a server-authoritative request_id (conversations.id)"
    )


def test_response_uses_correlation_helper():
    # F3 / SR-52: /api/response + the helper moved to routes/tts.py.
    # J2: the duplicate copy in codec_dashboard.py was removed (re-exported there).
    src = (REPO / "routes" / "tts.py").read_text()
    assert "_latest_response_for_session(" in src, (
        "/api/response must resolve via the _latest_response_for_session helper"
    )
    assert "after_id" in src, "/api/response must accept the after_id correlation param"


def test_frontend_sends_after_id():
    html = (REPO / "codec_dashboard.html").read_text()
    assert "after_id=" in html, (
        "the PWA poll must send &after_id= (server-authoritative correlation)"
    )
    assert "request_id" in html, (
        "the PWA must capture request_id from the /api/command response"
    )
