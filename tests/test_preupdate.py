"""The copy of CODEC's state taken before an update (codec_preupdate; docs/SAFE-UPDATE-DESIGN.md, P-027).

Source and destination are folders in tmp_path; nothing here reads or writes ~/.codec.
"""
from __future__ import annotations

import json
import os
import sqlite3
import stat
import subprocess
import sys
import tarfile
from datetime import datetime
from pathlib import Path

import codec_backup
import codec_preupdate

REPO = Path(__file__).resolve().parent.parent


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def _db(path: Path, rows: int = 50) -> sqlite3.Connection:
    """A WAL database left open with its newest rows only in the -wal file."""
    con = sqlite3.connect(str(path))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA wal_autocheckpoint=0")
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, body TEXT)")
    con.executemany("INSERT INTO t (body) VALUES (?)", [(f"row {i} " + "x" * 200,) for i in range(rows)])
    con.commit()
    return con


def _state(home: Path) -> sqlite3.Connection:
    home.mkdir(parents=True, exist_ok=True)
    con = _db(home / "memory.db")
    _db(home / "qchat.db", rows=3).close()
    (home / "config.json").write_text('{"llm_model": "x"}')
    (home / "agents" / "a1").mkdir(parents=True)
    (home / "agents" / "a1" / "plan.json").write_text("{}")
    (home / "notes").mkdir()
    (home / "notes" / "abc.json").write_text('{"title": "n"}')
    return con


def _rows(path: Path) -> int:
    con = sqlite3.connect(str(path))
    try:
        return con.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    finally:
        con.close()


def test_snapshot_copies_databases_and_state_private(tmp_path):
    home = tmp_path / "codec"
    live = _state(home)
    try:
        assert (home / "memory.db-wal").stat().st_size > 0  # the rows are still in the WAL
        res = codec_preupdate.snapshot(home, label="git abc123", now=datetime(2026, 11, 3, 9, 30, 0))
    finally:
        live.close()
    assert res["ok"], res
    snap = Path(res["path"])
    assert snap == home / "pre_update" / "20261103-093000"
    assert sorted(res["databases"]) == ["memory.db", "qchat.db"]
    assert _rows(snap / "memory.db") == 50 and _rows(snap / "qchat.db") == 3
    assert not (snap / "memory.db-wal").exists()  # one self-contained file
    with tarfile.open(snap / "state.tar.gz") as tf:
        names = set(tf.getnames())
    assert {"config.json", "agents/a1/plan.json", "notes/abc.json"} <= names
    manifest = json.loads((snap / "manifest.json").read_text())
    assert manifest["label"] == "git abc123" and manifest["databases"]["memory.db"]["check"] == "ok"
    assert manifest["databases"]["memory.db"]["sha256"] == codec_backup.sha256_file(snap / "memory.db")
    assert _mode(home / "pre_update") == 0o700 and _mode(snap) == 0o700
    assert {p.name: _mode(p) for p in snap.iterdir()} == {
        "memory.db": 0o600, "qchat.db": 0o600, "state.tar.gz": 0o600, "manifest.json": 0o600}
    assert [p.name for p in (home / "pre_update").iterdir()] == ["20261103-093000"]  # no .partial left


def test_a_second_snapshot_in_the_same_second_gets_its_own_folder(tmp_path):
    home = tmp_path / "codec"
    _state(home).close()
    now = datetime(2026, 11, 3, 9, 30, 0)
    first = codec_preupdate.snapshot(home, now=now)
    second = codec_preupdate.snapshot(home, now=now)
    assert first["ok"] and second["ok"] and first["path"] != second["path"]
    assert second["path"].endswith("20261103-093000-2")


def test_snapshot_skips_files_that_are_not_sqlite_and_links(tmp_path):
    home = tmp_path / "codec"
    _state(home).close()
    (home / "dashboard.db").write_bytes(b"")              # empty: a valid empty database
    (home / "notes.db").write_text("not a database")      # skipped, listed
    (home / "linked.db").symlink_to(home / "memory.db")   # never followed
    (home / "memory.db.bak-1").write_bytes(b"x")          # not *.db
    res = codec_preupdate.snapshot(home, tmp_path / "dest")
    assert res["ok"], res
    assert sorted(res["databases"]) == ["dashboard.db", "memory.db", "qchat.db"]
    assert res["skipped"] == ["notes.db: not a SQLite database"]
    assert not (home / "pre_update").exists()  # the destination given was used


def test_snapshot_without_a_state_folder_writes_nothing(tmp_path):
    res = codec_preupdate.snapshot(tmp_path / "missing", tmp_path / "dest")
    assert res["ok"] and res["path"] is None
    assert not (tmp_path / "dest").exists()


def test_a_damaged_database_fails_the_snapshot_and_leaves_no_folder(tmp_path):
    home = tmp_path / "codec"
    _state(home).close()
    bad = home / "vibe.db"
    con = sqlite3.connect(str(bad))
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, body TEXT)")
    con.executemany("INSERT INTO t (body) VALUES (?)", [("y" * 500,) for _ in range(200)])
    con.commit()
    con.close()
    data = bytearray(bad.read_bytes())
    for i in range(4096 * 2, 4096 * 3):  # one b-tree page in the middle
        data[i] = (i * 7) % 256
    bad.write_bytes(bytes(data))
    res = codec_preupdate.snapshot(home)
    assert not res["ok"] and res["error"].startswith("vibe.db: ") and res["path"] is None
    assert list((home / "pre_update").iterdir()) == []


def test_codec_backup_rotation_never_touches_snapshots(tmp_path, monkeypatch):
    home = tmp_path / "codec"
    _state(home).close()
    monkeypatch.setattr(codec_backup, "CODEC_HOME", str(home))
    res = codec_preupdate.snapshot(home)
    for day in range(1, 12):  # well past the 7 kept dates
        assert codec_backup.backup_sqlite(home / "qchat.db", "qchat", keep=7, today=f"2026-10-{day:02d}")["ok"]
    assert codec_backup.backup_state(today="2026-10-11")["ok"]
    assert sorted(p.name for p in Path(res["path"]).iterdir()) == [
        "manifest.json", "memory.db", "qchat.db", "state.tar.gz"]
    assert len([p for p in (home / "backups").iterdir() if p.name.startswith("qchat_")]) == 7


def test_the_default_source_is_codec_backups_home(tmp_path, monkeypatch):
    home = tmp_path / "codec"
    _state(home).close()
    monkeypatch.setattr(codec_backup, "CODEC_HOME", str(home))
    res = codec_preupdate.snapshot()
    assert res["ok"] and Path(res["path"]).parent == home / "pre_update"


def test_the_command_line_reports_the_folder_and_fails_with_exit_1(tmp_path):
    home = tmp_path / "codec"
    _state(home).close()
    run = lambda *a: subprocess.run([sys.executable, str(REPO / "codec_preupdate.py"), *a],  # noqa: E731
                                    capture_output=True, text=True, timeout=60, cwd=str(tmp_path))
    ok = run("--src", str(home), "--label", "git 123")
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "Saved a copy of CODEC's data before the update: " + str(home / "pre_update") in ok.stdout
    none = run("--src", str(tmp_path / "missing"))
    assert none.returncode == 0 and "nothing to copy" in none.stdout
    os.chmod(home / "pre_update", 0o500)  # cannot create the work folder
    try:
        bad = run("--src", str(home))
    finally:
        os.chmod(home / "pre_update", 0o700)
    assert bad.returncode == 1 and "Could not save a copy" in bad.stdout


def test_the_privacy_page_names_the_snapshot_folder():
    text = (REPO / "PRIVACY.md").read_text()
    assert codec_preupdate.DIRNAME == "pre_update"
    assert "| `~/.codec/pre_update/` |" in text and "`pre_update/`" in text
