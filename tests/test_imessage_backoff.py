"""P-058 (docs/IMESSAGE-PERMISSION-BACKOFF-DESIGN.md): the iMessage bridge logs a refused
Messages-database read once, then at most every 5 minutes; after 10 failed reads in a row it polls
every 5 minutes instead of every 3 s; it posts ONE notification, through the dashboard's locked
notifications writer, naming the interpreter that needs Full Disk Access; and it keeps a small state
file that Settings > Connectors reads.

Nothing here opens the real chat.db or writes the real ~/.codec: a stub stands in for sqlite3 inside
codec_imessage (or passes through to a tmp database), a fake clock stands in for time, and every path
is a tmp path. main() runs with a fake sleep and stops with Ctrl-C when the test says so.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import codec_imessage as im

REPO = Path(__file__).resolve().parent.parent
PROGRAM = os.path.realpath(sys.executable)
HANDLE = "+15550000001"  # made up


class FakeSqlite:
    """Stands in for the sqlite3 module inside codec_imessage only: refuses like macOS while
    `denied`, otherwise opens the tmp database the test points MESSAGES_DB at."""
    Row = sqlite3.Row

    def __init__(self):
        self.denied = True
        self.calls = 0

    def connect(self, *a, **kw):
        self.calls += 1
        if self.denied:
            raise sqlite3.DatabaseError("authorization denied")
        return sqlite3.connect(*a, **kw)


@pytest.fixture
def bridge(tmp_path, monkeypatch, caplog):
    import codec_lifecycle
    import routes._shared as shared
    b = SimpleNamespace(clock=SimpleNamespace(t=1_800_000_000.0), fake=FakeSqlite(), sleeps=[], lines=[],
                        handled=[], cleanups=[], audits=[], on_sleep=lambda b: None, stop=lambda b: True,
                        db=tmp_path / "chat.db", notifs=tmp_path / "notifications.json")
    b.t0 = b.clock.t
    b.db.write_bytes(b"")  # exists; while refused it is never opened
    b.notifs.write_text("[]")
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"imessage": {"allowed_senders": [HANDLE]}}))
    for name, path in (("MESSAGES_DB", b.db), ("CONFIG_PATH", cfg), ("STATE_FILE", tmp_path / "imessage_state.json"),
                       ("HEALTH_FILE", tmp_path / "imessage_health.json")):
        monkeypatch.setattr(im, name, str(path))
    monkeypatch.setattr(shared, "NOTIFICATIONS_PATH", str(b.notifs))
    monkeypatch.setattr(im, "sqlite3", b.fake)
    monkeypatch.setattr(im, "_db_health", im.DbReadHealth(clock=lambda: b.clock.t))

    def sleep(seconds):
        b.sleeps.append((b.fake.calls, seconds))
        b.clock.t += seconds
        b.on_sleep(b)
        if b.stop(b):
            raise KeyboardInterrupt

    monkeypatch.setattr(im, "time", SimpleNamespace(sleep=sleep, time=lambda: b.clock.t))
    monkeypatch.setattr(im, "get_llm_config", lambda cfg: {"model": "test-model"})
    monkeypatch.setattr(im, "audit", lambda event, **kw: b.audits.append(event))
    monkeypatch.setattr(im, "process_message", lambda msg, im_cfg, llm_cfg: b.handled.append(msg["text"]))
    monkeypatch.setattr(codec_lifecycle, "install_handlers", lambda fn, name="": b.cleanups.append(fn) or fn)

    class AtClock(logging.Handler):
        def emit(self, record):
            b.lines.append((b.clock.t, record.levelno, record.getMessage()))

    caplog.set_level(logging.INFO, logger="codec-imessage")
    handler = AtClock()
    im.log.addHandler(handler)
    yield b
    im.log.removeHandler(handler)


def _errors(b):
    return [(t, msg) for t, _, msg in b.lines if msg.startswith("DB read error")]


def _notifications(b):
    return json.loads(b.notifs.read_text())


def _health():
    return json.loads(Path(im.HEALTH_FILE).read_text())


def _make_db(path, *texts):
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE IF NOT EXISTS message (ROWID INTEGER PRIMARY KEY, text TEXT, date INTEGER, is_from_me INTEGER,
                                            cache_has_attachments INTEGER, handle_id INTEGER, attributedBody BLOB);
        INSERT OR IGNORE INTO handle VALUES (1, '%s');""" % HANDLE)
    for text in texts:
        con.execute("INSERT INTO message (text, date, is_from_me, cache_has_attachments, handle_id) "
                    "VALUES (?, 0, 0, 0, 1)", (text,))
    con.commit()
    con.close()


# ── Done-when 1: logged once, then at most every 5 minutes; a 5-minute poll after 10 ─────────

def test_a_refused_read_is_logged_once_then_at_most_every_five_minutes(bridge):
    b = bridge
    b.stop = lambda b: b.clock.t - b.t0 >= 24 * 3600
    im.main()
    lines = _errors(b)
    times = [t for t, _ in lines]
    assert times[0] == b.t0 and "authorization denied" in lines[0][1], "the first refusal is logged at once"
    assert PROGRAM in lines[0][1] and "Full Disk Access" in lines[0][1], "and says which program needs access"
    assert [t for t in times if t - b.t0 < 300] == [b.t0], "nothing more in the first 5 minutes"
    assert all(later - earlier >= im.FAIL_LOG_EVERY for earlier, later in zip(times, times[1:]))
    last_hour = [t for t in times if t >= b.clock.t - 3600]
    assert 11 <= len(last_hour) <= 13, f"about 12 lines an hour, got {len(last_hour)}"
    assert len(times) <= 24 * 12 + 2, f"{len(times)} lines a day (it was 28,800 at one every 3 s)"
    assert b.sleeps and all(seconds == (3 if calls < 10 else 300) for calls, seconds in b.sleeps), \
        "3 s until 10 refusals in a row, then 5 minutes"
    assert b.fake.calls <= 10 + 24 * 12, f"{b.fake.calls} reads a day (it was 28,800)"


# ── Done-when 2: exactly one notification, through the locked writer, naming the program ─────

def test_fifty_refusals_make_exactly_one_notification_naming_the_program(bridge, monkeypatch):
    import routes._shared as shared
    b = bridge
    writes = []
    real = shared._save_notification
    monkeypatch.setattr(shared, "_save_notification", lambda *a, **k: writes.append((a, k)) or real(*a, **k))
    b.stop = lambda b: b.fake.calls >= 50
    im.main()
    assert b.fake.calls == 50
    (n,) = _notifications(b)
    assert len(writes) == 1, "written through routes._shared._save_notification (its lock and file lock)"
    assert PROGRAM in n["body"] and "Full Disk Access" in n["title"] and im.FDA_WHERE in n["body"]
    assert n["status"] == "warning" and n["schedule_id"] == "imessage" and n["read"] is False
    assert "stop codec-imessage" in n["body"], "or stop the bridge on purpose (owner decision 14)"
    assert _health()["notified_at"] is not None
    # PM2 stops it (the cleanup runs), then starts it again: 50 more refusals, still exactly one.
    b.cleanups[-1]()
    assert _health()["state"] == "stopped" and _health()["notified_at"] is not None
    monkeypatch.setattr(im, "_db_health", im.DbReadHealth(clock=lambda: b.clock.t))
    b.fake.calls = 0
    im.main()
    assert len(_notifications(b)) == 1 and len(writes) == 1, "a restart does not tell the owner again"
    # Killed without a cleanup, started again: still one.
    monkeypatch.setattr(im, "_db_health", im.DbReadHealth(clock=lambda: b.clock.t))
    b.fake.calls = 0
    im.main()
    assert len(_notifications(b)) == 1


def test_the_bridge_writes_no_notifications_file_of_its_own():
    src = (REPO / "codec_imessage.py").read_text(encoding="utf-8")
    assert "notifications.json" not in src and "from routes._shared import _save_notification" in src


# ── Done-when 3: the state file ──────────────────────────────────────────────────────────────

def test_the_state_file_is_private_and_follows_the_bridge(bridge, monkeypatch):
    import codec_jsonstore
    b = bridge
    written = []
    real = codec_jsonstore.atomic_write_json
    monkeypatch.setattr(codec_jsonstore, "atomic_write_json", lambda p, d, **k: written.append(str(p)) or real(p, d, **k))
    b.stop = lambda b: b.fake.calls >= 3
    im.main()
    h = _health()
    assert set(h) == {"state", "since", "checked", "failures", "program", "denied_since", "notified_at"}
    assert h["state"] == "needs_permission" and h["program"] == PROGRAM and h["checked"] == b.t0
    assert h["failures"] == 1, "written when the state changes and with each logged line, not every poll"
    assert h["denied_since"] == b.t0 and h["notified_at"] is None, "10 refusals before the notification"
    assert stat.S_IMODE(os.stat(im.HEALTH_FILE).st_mode) == 0o600
    assert im.HEALTH_FILE in written, "through codec_jsonstore.atomic_write_json"
    b.cleanups[-1]()
    h = _health()
    assert h["state"] == "stopped" and h["failures"] == 3 and h["denied_since"] == b.t0, \
        "a stop keeps what a restart needs"


def test_reading_again_starts_from_the_newest_message_not_the_backlog(bridge):
    b = bridge
    _make_db(b.db, "hey codec old one", "gm")  # before the bridge started; its start position is unread

    def on_sleep(b):
        if b.fake.calls == 4:  # refused 4 times, meanwhile two more messages came in, then access is granted
            _make_db(b.db, "hey codec while refused", "good morning")
            b.fake.denied = False
        elif not b.fake.denied and not b.handled and len(b.sleeps) == 5:
            _make_db(b.db, "hey codec new")

    b.on_sleep = on_sleep
    b.stop = lambda b: len(b.sleeps) >= 8
    im.main()
    assert b.handled == ["hey codec new"], "the backlog is not answered, the next message is"
    assert _health()["state"] == "ok" and _health()["denied_since"] is None and _health()["failures"] == 0
    assert json.loads(Path(im.STATE_FILE).read_text())["last_rowid"] == 5
    assert any("readable again after 4 failed reads" in m for _, _, m in b.lines)
    assert any(m.startswith("Starting from ROWID 4") for _, _, m in b.lines)


def test_a_restart_after_access_was_granted_skips_the_backlog_too(bridge):
    b = bridge
    _make_db(b.db, "hey codec answered before", "hey codec came in while refused", "gm")
    Path(im.STATE_FILE).write_text(json.dumps({"last_rowid": 1}))
    Path(im.HEALTH_FILE).write_text(json.dumps({"state": "stopped", "since": b.t0 - 60, "checked": b.t0 - 60,
                                                "failures": 400, "program": PROGRAM, "denied_since": b.t0 - 86400,
                                                "notified_at": b.t0 - 86000}))
    b.fake.denied = False
    b.on_sleep = lambda b: _make_db(b.db, "hey codec new") if len(b.sleeps) == 1 else None
    b.stop = lambda b: len(b.sleeps) >= 3
    im.main()
    assert b.handled == ["hey codec new"] and _health()["state"] == "ok"
    assert _notifications(b) == []


# ── What counts as a refusal ─────────────────────────────────────────────────────────────────

def test_a_locked_database_slows_the_poll_but_is_no_permission_problem(tmp_path, monkeypatch):
    clock = SimpleNamespace(t=1_800_000_000.0)
    db = tmp_path / "chat.db"
    db.write_bytes(b"")
    monkeypatch.setattr(im, "MESSAGES_DB", str(db))
    monkeypatch.setattr(im, "HEALTH_FILE", str(tmp_path / "imessage_health.json"))
    told = []
    monkeypatch.setattr(im, "_notify_needs_access", lambda program, failures: told.append(failures) or True)
    h = im.DbReadHealth(clock=lambda: clock.t)
    for _ in range(12):
        h.failed(sqlite3.OperationalError("database is locked"))
        clock.t += h.next_sleep(3)
    assert told == [] and h.next_sleep(3) == 300 and h.next_sleep(600) == 600 and _health()["state"] == "error"
    h.succeeded()
    assert h.next_sleep(3) == 3 and _health()["state"] == "ok" and not h.wants_fresh_start(), \
        "a busy database is caught up, not skipped"
    assert im._is_denied(sqlite3.DatabaseError("authorization denied"))
    assert im._is_denied(PermissionError(1, "Operation not permitted"))
    assert not im._is_denied(sqlite3.OperationalError("unable to open database file")), "the file itself opens"
    if os.geteuid() != 0:
        db.chmod(0)
        try:
            assert im._is_denied(sqlite3.OperationalError("unable to open database file"))
        finally:
            db.chmod(0o600)
