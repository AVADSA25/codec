"""Tests for A-20 — inline-sqlite UPDATE replaced by codec_core helper.

codec.py's voice handler opened a bare `sqlite3.connect(DB_PATH)` for one
`UPDATE sessions SET response=? WHERE id=?` with no WAL/busy_timeout —
risking `database is locked` under concurrent agent-runner + voice writes.
Now it routes through `codec_core.update_session_response`, and all
codec_core session helpers share a `_db_connect()` with WAL + busy_timeout.

Reference: docs/audits/PHASE-1-CODE-QUALITY.md finding A-20.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import codec_core  # noqa: E402


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Point codec_core at a throwaway DB + init the schema."""
    db = tmp_path / "memory.db"
    monkeypatch.setattr(codec_core, "DB_PATH", str(db))
    codec_core.init_db()
    return db


# ── _db_connect pragmas ──────────────────────────────────────────────────────


def test_db_connect_sets_wal_and_busy_timeout(tmp_db):
    c = codec_core._db_connect()
    try:
        mode = c.execute("PRAGMA journal_mode").fetchone()[0]
        busy = c.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        c.close()
    assert mode.lower() == "wal", f"expected WAL, got {mode!r}"
    assert busy == 5000, f"expected busy_timeout 5000, got {busy}"


# ── update_session_response round-trip ───────────────────────────────────────


def test_update_session_response_round_trip(tmp_db):
    rid = codec_core.save_task("translate this", "CODEC")
    assert codec_core.update_session_response(rid, "voila — translated") is True
    # Read it back directly
    c = sqlite3.connect(str(tmp_db))
    row = c.execute("SELECT response FROM sessions WHERE id=?", (rid,)).fetchone()
    c.close()
    assert row[0] == "voila — translated"


def test_update_session_response_truncates_to_500(tmp_db):
    rid = codec_core.save_task("long one", "CODEC")
    codec_core.update_session_response(rid, "x" * 1000)
    c = sqlite3.connect(str(tmp_db))
    row = c.execute("SELECT response FROM sessions WHERE id=?", (rid,)).fetchone()
    c.close()
    assert len(row[0]) == 500


def test_update_session_response_none_rid_returns_false(tmp_db):
    assert codec_core.update_session_response(None, "anything") is False


def test_update_session_response_nonexistent_rid_no_crash(tmp_db):
    # UPDATE affecting 0 rows is not an error — must not raise, returns True.
    assert codec_core.update_session_response(999999, "ghost") is True


def test_update_session_response_coerces_non_str(tmp_db):
    rid = codec_core.save_task("num", "CODEC")
    assert codec_core.update_session_response(rid, 12345) is True
    c = sqlite3.connect(str(tmp_db))
    row = c.execute("SELECT response FROM sessions WHERE id=?", (rid,)).fetchone()
    c.close()
    assert row[0] == "12345"


def test_update_session_response_surfaces_in_get_memory(tmp_db):
    rid = codec_core.save_task("weather in Paris", "CODEC")
    codec_core.update_session_response(rid, "It's sunny, 22C")
    mem = codec_core.get_memory(n=5)
    assert "weather in Paris" in mem
    assert "sunny" in mem


# ── Source-level invariant ───────────────────────────────────────────────────


def test_codec_no_inline_sqlite_update():
    """codec.py must no longer open a raw sqlite3 connection for the session
    response UPDATE — it routes through codec_core.update_session_response."""
    src = (REPO / "codec.py").read_text()
    assert "UPDATE sessions SET response" not in src, (
        "inline UPDATE sessions must be gone from codec.py (A-20)"
    )
    assert "update_session_response(rid" in src, (
        "codec.py must call codec_core.update_session_response"
    )


def test_codec_core_session_helpers_use_db_connect():
    """The only bare `sqlite3.connect(DB_PATH)` left in codec_core is the one
    INSIDE `_db_connect` (the canonical helper). All session helpers
    (init_db/save_task/update_session_response/get_memory/get_recent_*) route
    through `_db_connect()`. String-template connects in the deprecated
    build_session_script (`L.append("...")`) don't count."""
    src = (REPO / "codec_core.py").read_text()
    bare = 0
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("L.append"):
            continue
        if stripped == "c = sqlite3.connect(DB_PATH)":
            bare += 1
    assert bare == 1, (
        f"expected exactly ONE bare connect (inside _db_connect); found {bare}"
    )
    # And the helpers call _db_connect
    assert src.count("_db_connect()") >= 4, "session helpers must use _db_connect()"
