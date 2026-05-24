"""Tests for PR-4J (M-5) — get_db() returns a per-thread SQLite connection
(threading.local) instead of one global connection shared across all threads.

Reference: docs/PR4J-PERTHREAD-DB-DESIGN.md, docs/audits/PHASE-1-RELIABILITY.md M-5.
"""
from __future__ import annotations

import sqlite3
import sys
import threading
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) in sys.path:
    sys.path.remove(str(_REPO))
sys.path.insert(0, str(_REPO))


@pytest.fixture
def shared(tmp_path, monkeypatch):
    import routes._shared as sh
    monkeypatch.setattr(sh, "DB_PATH", str(tmp_path / "memory.db"))
    # Reset connection state (green: _close_all_db_conns; red: legacy _db_conn).
    if hasattr(sh, "_close_all_db_conns"):
        sh._close_all_db_conns()
    if hasattr(sh, "_db_conn"):
        sh._db_conn = None
    yield sh
    if hasattr(sh, "_close_all_db_conns"):
        sh._close_all_db_conns()


# ── per-thread identity ───────────────────────────────────────────────────────


def test_same_connection_within_thread(shared):
    assert shared.get_db() is shared.get_db()


def test_different_connection_across_threads(shared):
    main_conn = shared.get_db()
    other: dict = {}

    def worker():
        other["conn"] = shared.get_db()
    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert other["conn"] is not main_conn, "M-5: each thread must get its own connection"


# ── pragmas applied per connection ────────────────────────────────────────────


def test_connection_has_wal_busy_timeout_and_row_factory(shared):
    c = shared.get_db()
    assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert c.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert c.row_factory is sqlite3.Row


# ── close-all clears the registry + reopens fresh ─────────────────────────────


def test_close_all_clears_registry_and_reopens(shared):
    c1 = shared.get_db()

    def worker():
        shared.get_db()  # create one on another thread too
    t = threading.Thread(target=worker)
    t.start()
    t.join()

    shared._close_all_db_conns()
    assert shared._db_conns == [], "registry must be cleared after close-all"
    c2 = shared.get_db()
    assert c2 is not c1, "a fresh connection must be created after close-all"


# ── source invariants ─────────────────────────────────────────────────────────


def test_get_db_uses_threading_local():
    src = (_REPO / "routes" / "_shared.py").read_text()
    assert "threading.local()" in src, "M-5: get_db must use threading.local"
    body = src[src.index("def get_db("):]
    body = body[:body.index("\ndef ", 1)]
    assert "_db_local" in body, "get_db must read/write the thread-local connection"


def test_dashboard_shutdown_closes_all_conns():
    src = (_REPO / "codec_dashboard.py").read_text()
    assert "_close_all_db_conns(" in src, (
        "M-5: the dashboard shutdown handler must close all per-thread connections"
    )
