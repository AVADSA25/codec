"""Backups that check themselves, the state tar, stated retention and backup status
(docs/BACKUP-RETENTION-DESIGN.md; P-018, D-38).

Nothing here touches ~/.codec: codec_backup.CODEC_HOME points at tmp_path, alerts are recorded,
and the audit log is the suite's temp file (tests/conftest.py).
"""
from __future__ import annotations

import importlib.util
import os
import sqlite3
import stat
import subprocess
import tarfile
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import codec_alerts
import codec_backup

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def cb(tmp_path, monkeypatch):
    home = tmp_path / "codec"
    home.mkdir()
    monkeypatch.setattr(codec_backup, "CODEC_HOME", str(home))
    alerts, resolved = [], []
    monkeypatch.setattr(codec_alerts, "alert_once", lambda key, level, msg, **kw: alerts.append((key, level, msg)) or True)
    monkeypatch.setattr(codec_alerts, "alert_resolved", lambda key, msg: resolved.append(key) or False)
    return SimpleNamespace(home=home, bk=home / "backups", alerts=alerts, resolved=resolved)


def _memory_db(path: Path, old_rows: int = 3, new_rows: int = 2) -> None:
    from codec_memory import CodecMemory
    mem = CodecMemory(db_path=str(path))
    for i in range(new_rows):
        mem.save("new", "user", f"recent message {i}")
    mem.close()
    old = (datetime.now() - timedelta(days=200)).isoformat()
    with sqlite3.connect(path) as c:
        for i in range(old_rows):
            c.execute("INSERT INTO conversations (session_id, timestamp, role, content) VALUES (?,?,?,?)",
                      ("old", old, "user", f"old message {i}"))


def _rows(path: Path) -> int:
    with sqlite3.connect(path) as c:
        return c.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]


def _corrupt(path: Path) -> None:
    """Overwrite one b-tree page in the middle of a closed database file."""
    data = bytearray(path.read_bytes())
    page = 4096
    assert len(data) > page * 4, "fixture too small to corrupt a middle page"
    for i in range(page * 2, page * 3):
        data[i] = (i * 7) % 256
    path.write_bytes(bytes(data))


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def _big_db(path: Path) -> None:
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, body TEXT)")
        c.executemany("INSERT INTO t (body) VALUES (?)", [("x" * 400 + str(i),) for i in range(200)])


# ── (1) the one writer ──────────────────────────────────────────────────────
def test_backup_sqlite_writes_a_checked_0600_copy_and_keeps_n(cb):
    db = cb.home / "memory.db"
    _memory_db(db)
    for day in range(1, 11):
        r = codec_backup.backup_sqlite(db, "memory", keep=7, today=f"2026-10-{day:02d}")
        assert r["ok"] and r["check"] == "ok"
    names = sorted(os.listdir(cb.bk))
    assert names == [f"memory_2026-10-{d:02d}.db" for d in range(4, 11)]
    assert _mode(cb.bk) == 0o700
    for n in names:
        assert _mode(cb.bk / n) == 0o600
    latest = cb.bk / "memory_2026-10-10.db"
    assert codec_backup.quick_check(latest) == "ok"
    assert codec_backup.content_checksum(latest) == codec_backup.content_checksum(db)
    with sqlite3.connect(latest) as c:  # a self-contained file: no -wal beside it
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    st = codec_backup.load_status()["databases"]["memory"]
    assert st["last_ok_file"] == "memory_2026-10-10.db" and st["last_check"] == "ok" and st["keep"] == 7
    assert _mode(codec_backup.status_path()) == 0o600


def test_backup_sqlite_rejects_a_corrupt_copy_keeps_older_ones_and_alerts(cb):
    db = cb.home / "memory.db"
    _big_db(db)
    good = codec_backup.backup_sqlite(db, "memory", keep=7, today="2026-10-01")
    assert good["ok"]
    before = codec_backup.sha256_file(cb.bk / "memory_2026-10-01.db")
    _corrupt(db)
    assert codec_backup.quick_check(db) != "ok"  # the fixture really is damaged

    bad = codec_backup.backup_sqlite(db, "memory", keep=7, today="2026-10-02")
    assert not bad["ok"] and bad["check"] != "ok"
    assert sorted(os.listdir(cb.bk)) == ["memory_2026-10-01.db"]  # nothing new, no temp file left
    assert codec_backup.sha256_file(cb.bk / "memory_2026-10-01.db") == before
    assert [a[0] for a in cb.alerts] == ["backup_check:memory"]
    st = codec_backup.load_status()["databases"]["memory"]
    assert st["last_ok_file"] == "memory_2026-10-01.db" and st["last_check"] != "ok"

    # A bad copy never replaces a good one of the same day, even when forced ("backup now").
    forced = codec_backup.backup_sqlite(db, "memory", keep=7, force=True, today="2026-10-01")
    assert not forced["ok"]
    assert codec_backup.sha256_file(cb.bk / "memory_2026-10-01.db") == before
    assert len(cb.alerts) == 2


def test_backup_sqlite_clears_the_alert_only_after_a_failure(cb):
    db = cb.home / "memory.db"
    _big_db(db)
    codec_backup.backup_sqlite(db, "memory", today="2026-10-01")
    assert cb.resolved == []  # a plain success does not touch the owner's alert state
    good = db.read_bytes()
    _corrupt(db)
    codec_backup.backup_sqlite(db, "memory", today="2026-10-02")
    db.write_bytes(good)
    # Within the hour the heartbeat does not try again (no alert or audit line every 30 minutes) ...
    wait = codec_backup.backup_sqlite(db, "memory", today="2026-10-02")
    assert wait["skipped"] and not wait["ok"] and len(cb.alerts) == 1
    # ... "backup now" does, and a passing copy clears the alert.
    assert codec_backup.backup_sqlite(db, "memory", force=True, today="2026-10-02")["ok"]
    assert cb.resolved == ["backup_check:memory"]


def test_the_launchd_script_makes_a_checked_copy_through_the_module(tmp_path):
    home = tmp_path / "codec"
    home.mkdir()
    _big_db(home / "memory.db")
    env = {**os.environ, "CODEC_BACKUP_HOME": str(home)}
    r = subprocess.run(["bash", str(REPO / "scripts" / "backup_memory.sh")], env=env, capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    copy = home / "backups" / f"memory_{datetime.now():%Y-%m-%d}.db"
    assert "written and checked" in r.stdout and _mode(copy) == 0o600 and codec_backup.quick_check(copy) == "ok"
    again = subprocess.run(["bash", str(REPO / "scripts" / "backup_memory.sh")], env=env, capture_output=True,
                           text=True, timeout=60)
    assert again.returncode == 0 and "already written and checked" in again.stdout


def test_backup_sqlite_skips_a_copy_already_checked_today_unless_forced(cb):
    db = cb.home / "memory.db"
    _memory_db(db)
    first = codec_backup.backup_sqlite(db, "memory", today="2026-10-01")
    again = codec_backup.backup_sqlite(db, "memory", today="2026-10-01")
    assert first["ok"] and not first["skipped"] and again["ok"] and again["skipped"]
    # A same-named file the old, unchecked writer made is not trusted: it is made again.
    (cb.bk / "memory_2026-10-02.db").write_bytes(b"")
    r = codec_backup.backup_sqlite(db, "memory", today="2026-10-02")
    assert r["ok"] and not r["skipped"] and codec_backup.quick_check(cb.bk / "memory_2026-10-02.db") == "ok"
    assert codec_backup.backup_sqlite(db, "memory", force=True, today="2026-10-02")["skipped"] is False


def test_backup_sqlite_rotates_the_old_script_names_and_tightens_their_mode(cb):
    db = cb.home / "memory.db"
    _memory_db(db)
    cb.bk.mkdir(mode=0o755)
    for d in ("2026-09-01", "2026-09-02", "2026-09-03"):
        (cb.bk / f"memory-{d}.db").write_bytes(b"old script copy")
        os.chmod(cb.bk / f"memory-{d}.db", 0o644)
    for keep_me in ("memory.db.bak-1776181333", "memory_pre-retention_2026-09-01.db", "qchat_2026-09-01.db"):
        (cb.bk / keep_me).write_bytes(b"other series")
    r = codec_backup.backup_sqlite(db, "memory", keep=3, today="2026-10-01")
    assert sorted(r["removed"]) == ["memory-2026-09-01.db"]
    left = set(os.listdir(cb.bk))
    assert {"memory-2026-09-02.db", "memory-2026-09-03.db", "memory_2026-10-01.db"} <= left
    assert {"memory.db.bak-1776181333", "memory_pre-retention_2026-09-01.db", "qchat_2026-09-01.db"} <= left
    assert _mode(cb.bk / "memory-2026-09-02.db") == 0o600


def test_backup_sqlite_without_a_database_does_nothing(cb):
    r = codec_backup.backup_sqlite(cb.home / "qchat.db", "qchat", today="2026-10-01")
    assert r["skipped"] and not r["ok"] and not cb.bk.exists() and not cb.alerts


# ── (2) the state tar ───────────────────────────────────────────────────────
def _state_fixture(home: Path) -> dict:
    files = {
        "agents/a1/plan.json": b'{"goal": "x"}', "agents/a1/state.json": b"{}",
        "config.json": b'{"retention": {}}', "notes/0123456789ab.json": b'{"title": "n"}',
        "notes/.trash/ba9876543210.json": b"{}", "standing_rules.json": b"[]", "schedules.json": b"[]",
        "voices/codec-02/reference.wav": os.urandom(2048), "voices/codec-02/reference.txt": b"hello",
        "plugins.allowlist": b'{"x.py": {"sha256": "00"}}',
    }
    for rel, data in files.items():
        p = home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    (home / "agents" / "a1" / "grants.json.lock").write_text("")
    os.symlink("/etc/hosts", home / "agents" / "a1" / "link")
    (home / "voices" / "other").mkdir()
    (home / "voices" / "other" / "x.wav").write_bytes(b"not saved")
    return files


def test_state_tar_holds_every_item_is_0600_and_keeps_7(cb):
    files = _state_fixture(cb.home)
    (cb.bk).mkdir()
    for d in ("2026-09-20", "2026-09-21"):
        (cb.bk / f"agents_{d}.tar.gz").write_bytes(b"old agents-only tar")
    for day in range(1, 10):
        r = codec_backup.backup_state(today=f"2026-10-{day:02d}")
        assert r["ok"] and r["check"] == "ok"
    tars = sorted(n for n in os.listdir(cb.bk) if n.endswith(".tar.gz"))
    assert tars == [f"state_2026-10-{d:02d}.tar.gz" for d in range(3, 10)]  # 7 dates; old agents_ tars rotated too
    latest = cb.bk / "state_2026-10-09.tar.gz"
    assert _mode(latest) == 0o600
    manifest = codec_backup.tar_manifest(latest)
    assert set(manifest) == set(files)  # no lock file, no link, no other voice
    with tarfile.open(latest) as tf:
        assert not any(m.issym() or m.islnk() for m in tf.getmembers())
    st = codec_backup.load_status()["state"]
    assert st["items"] == list(codec_backup.STATE_ITEMS) and st["keep"] == 7 and st["files"] == len(files)
    assert set(codec_backup.STATE_ITEMS) == {"agents", "config.json", "notes", "standing_rules.json",
                                             "schedules.json", "voices/codec-02", "plugins.allowlist"}


def test_state_tar_with_nothing_to_save_writes_nothing(cb):
    r = codec_backup.backup_state(today="2026-10-01")
    assert r["skipped"] and not cb.bk.exists()


# ── retention (D-38) ────────────────────────────────────────────────────────
def test_retention_defaults_are_the_owner_decision():
    assert codec_backup.RETENTION_DEFAULTS == {"chats_days": 90, "clipboard_days": 30, "photos_days": 30}
    assert codec_backup.retention_settings({}) == codec_backup.RETENTION_DEFAULTS
    s, errors = codec_backup.normalize_retention({"chats_days": 3, "clipboard_days": 0, "photos_days": "45"})
    assert s == {"chats_days": 90, "clipboard_days": 0, "photos_days": 45} and len(errors) == 1
    assert codec_backup.normalize_retention({"chats_days": True})[1]
    assert codec_backup.normalize_retention({"photos_days": 99999})[1]


def test_retention_backs_up_memory_before_the_first_cleanup_with_a_new_setting(cb):
    db = cb.home / "memory.db"
    _memory_db(db, old_rows=3, new_rows=2)
    now = datetime(2026, 10, 30, 3, 0)
    out = codec_backup.run_retention(cfg={}, now=now)
    assert out["chats"] == {"days": 90, "deleted": 3, "applied": True}
    assert _rows(db) == 2
    pre = cb.bk / "memory_pre-retention_2026-10-30.db"
    daily = cb.bk / "memory_2026-10-30.db"
    assert pre.is_file() and daily.is_file()
    assert _rows(pre) == 5  # the deleted rows are recoverable from the copy taken first
    st = codec_backup.load_status()
    assert st["retention"]["chats_days_applied"] == 90
    assert st["deletions"][0] == {"at": "2026-10-30T03:00:00", "what": "chats", "count": 3, "days": 90}

    # Same setting the next day: no new pre-retention copy.
    codec_backup.run_retention(cfg={}, now=now + timedelta(days=1))
    assert not (cb.bk / "memory_pre-retention_2026-10-31.db").exists()
    # A new number: a fresh copy first, then the clean-up with it.
    codec_backup.run_retention(cfg={"retention": {"chats_days": 30}}, now=now + timedelta(days=2))
    assert (cb.bk / "memory_pre-retention_2026-11-01.db").is_file()
    assert codec_backup.load_status()["retention"]["chats_days_applied"] == 30


def test_retention_keeps_the_count_when_vacuum_fails_after_the_delete(cb, monkeypatch):
    import codec_memory
    db = cb.home / "memory.db"
    _memory_db(db, old_rows=3, new_rows=2)
    real = codec_memory.CodecMemory.cleanup

    def delete_then_fail(self, retention_days=90):
        with sqlite3.connect(self.db_path) as c:
            c.execute("DELETE FROM conversations WHERE session_id = 'old'")
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(codec_memory.CodecMemory, "cleanup", delete_then_fail)
    out = codec_backup.run_retention(cfg={}, now=datetime(2026, 10, 30))
    monkeypatch.setattr(codec_memory.CodecMemory, "cleanup", real)
    assert out["chats"]["deleted"] == 3 and out["chats"]["error"] == "OperationalError"
    assert codec_backup.load_status()["deletions"][0]["count"] == 3


def test_retention_never_deletes_chats_without_a_checked_copy(cb, monkeypatch):
    db = cb.home / "memory.db"
    _memory_db(db, old_rows=3, new_rows=2)
    monkeypatch.setattr(codec_backup, "backup_memory", lambda **kw: {"ok": False, "check": "error: test"})
    out = codec_backup.run_retention(cfg={}, now=datetime(2026, 10, 30))
    assert out["chats"]["deleted"] == 0 and "no checked copy" in out["chats"]["skipped"]
    assert _rows(db) == 5


def test_retention_deletes_old_clipboard_entries_and_old_webcam_photos_only(cb):
    now = datetime(2026, 10, 30, 3, 0)
    with sqlite3.connect(cb.home / "clipboard.db") as c:
        c.execute("CREATE TABLE clips (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, content TEXT)")
        c.executemany("INSERT INTO clips (timestamp, content) VALUES (?,?)",
                      [((now - timedelta(days=d)).isoformat(), f"clip {d}") for d in (1, 29, 31, 200)])
    photos = cb.home / "photos"
    photos.mkdir()
    old, new = now - timedelta(days=40), now - timedelta(days=2)
    for t in (old, new):
        (photos / f"webcam_{t:%Y%m%d_%H%M%S}.jpg").write_bytes(b"jpg")
    (photos / "holiday.jpg").write_bytes(b"not a CODEC capture")
    os.utime(photos / "holiday.jpg", (old.timestamp(), old.timestamp()))
    target = cb.home / "elsewhere.jpg"
    target.write_bytes(b"x")
    os.symlink(target, photos / "webcam_20200101_000000.jpg")

    out = codec_backup.run_retention(cfg={}, now=now)
    assert out["clipboard"] == {"days": 30, "deleted": 2} and out["photos"] == {"days": 30, "deleted": 1}
    with sqlite3.connect(cb.home / "clipboard.db") as c:
        assert sorted(r[0] for r in c.execute("SELECT content FROM clips")) == ["clip 1", "clip 29"]
    assert sorted(os.listdir(photos)) == sorted([f"webcam_{new:%Y%m%d_%H%M%S}.jpg", "holiday.jpg",
                                                 "webcam_20200101_000000.jpg"])
    assert target.exists()
    whats = {d["what"]: d["count"] for d in codec_backup.load_status()["deletions"]}
    assert whats == {"clipboard": 2, "photos": 1}


def test_retention_zero_keeps_everything(cb):
    db = cb.home / "memory.db"
    _memory_db(db)
    (cb.home / "photos").mkdir()
    (cb.home / "photos" / "webcam_20200101_000000.jpg").write_bytes(b"x")
    out = codec_backup.run_retention(cfg={"retention": {"chats_days": 0, "clipboard_days": 0, "photos_days": 0}},
                                     now=datetime(2026, 10, 30))
    assert all(v["deleted"] == 0 and v["skipped"] == "kept forever" for v in out.values())
    assert _rows(db) == 5 and (cb.home / "photos" / "webcam_20200101_000000.jpg").exists()


# ── (3) backup status ───────────────────────────────────────────────────────
def test_status_report_shows_backup_check_state_tar_retention_and_deletions(cb):
    _memory_db(cb.home / "memory.db")
    _state_fixture(cb.home)
    (cb.home / "photos").mkdir()
    (cb.home / "photos" / "webcam_20200101_000000.jpg").write_bytes(b"x")
    codec_backup.daily_backups()
    codec_backup.run_retention(cfg={})
    text = codec_backup.status_report()
    today = datetime.now().strftime("%Y-%m-%d")
    assert f"memory.db: last copy memory_{today}.db" in text and "check ok, 30 days kept" in text
    assert f"Settings and agents: state_{today}.tar.gz" in text and "voices/codec-02" in text and "7 days kept" in text
    assert "Kept for: chats 90 days, clipboard history 30 days, webcam photos 30 days" in text
    assert "deleted chats 3, clipboard 0 (no history), photos 1" in text
    assert "chats: 3 older than 90 days" in text and "photos: 1 older than 30 days" in text
    assert "message" not in text  # counts, never content


def test_status_report_shows_old_copies_removed_by_the_keep_rule(cb):
    db = cb.home / "memory.db"
    _memory_db(db)
    for i in range(31):
        codec_backup.backup_sqlite(db, "memory", keep=30, today=f"{datetime(2026, 9, 1) + timedelta(days=i):%Y-%m-%d}")
    assert len(os.listdir(cb.bk)) == 30
    assert "removed 1 older copy" in codec_backup.status_report() and "memory_2026-09-01.db" in codec_backup.status_report()


def test_status_report_shows_a_failed_check(cb):
    db = cb.home / "memory.db"
    _big_db(db)
    codec_backup.backup_memory(today="2026-10-01")
    _corrupt(db)
    codec_backup.backup_memory(force=True)
    text = codec_backup.status_report()
    assert "check " in text and "the last attempt failed" in text


def _load_skill():
    spec = importlib.util.spec_from_file_location("backup_status_under_test", REPO / "skills" / "backup_status.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_backup_status_skill_backs_up_through_the_one_writer_and_shows_status(cb):
    _memory_db(cb.home / "memory.db")
    skill = _load_skill()
    assert skill.SKILL_MCP_EXPOSE is True
    msg = skill.run("backup now")
    assert "written and checked" in msg
    assert (cb.bk / f"memory_{datetime.now():%Y-%m-%d}.db").is_file()
    assert "Kept for: chats 90 days" in skill.run("backup status")
    assert "restore" not in skill.SKILL_TRIGGERS and "restore_live" not in (REPO / "skills" / "backup_status.py").read_text()


# ── no second writer, no real ~/.codec ──────────────────────────────────────
def test_the_suite_is_isolated_from_the_real_codec_home():
    assert "codec-test-backups-" in codec_backup.CODEC_HOME
    assert Path(codec_backup.CODEC_HOME).resolve() != Path("~/.codec").expanduser().resolve()


def test_heartbeat_backups_and_retention_go_through_codec_backup_isolated(monkeypatch):
    spec = importlib.util.spec_from_file_location("heartbeat_under_test", REPO / "codec_heartbeat.py")
    hb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hb)
    calls = []
    monkeypatch.setattr(codec_backup, "daily_backups", lambda: calls.append("daily") or [])
    hb.backup_memory_db()
    assert calls == ["daily"]
    src = (REPO / "codec_heartbeat.py").read_text()
    assert "codec_backup.run_retention()" in src and ".cleanup(" not in src and ".backup(" not in src
    assert "tarfile" not in src
