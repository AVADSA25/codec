"""tools/codec_restore.py (docs/BACKUP-RETENTION-DESIGN.md; P-018).

Round trips into a temp folder with equal checksums, strict consent with the literal verb 'restore'
for a live restore (codec_ask_user.ask is patched), and plugins.allowlist never restored.
Nothing here touches ~/.codec: codec_backup.CODEC_HOME is tmp_path.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import codec_alerts
import codec_ask_user
import codec_backup

REPO = Path(__file__).resolve().parent.parent
TODAY = datetime.now().strftime("%Y-%m-%d")
FILES = {
    "agents/a1/plan.json": b'{"goal": "x"}', "config.json": b'{"llm_model": "m"}',
    "notes/0123456789ab.json": b'{"title": "n"}', "standing_rules.json": b"[]", "schedules.json": b"[]",
    "voices/codec-02/reference.wav": bytes(range(256)) * 8, "voices/codec-02/reference.txt": b"hello",
    "plugins.allowlist": b'{"good.py": {"sha256": "aa"}}',
}


def _tool():
    spec = importlib.util.spec_from_file_location("codec_restore_under_test", REPO / "tools" / "codec_restore.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture
def rs(tmp_path, monkeypatch):
    home = tmp_path / "codec"
    home.mkdir()
    monkeypatch.setattr(codec_backup, "CODEC_HOME", str(home))
    monkeypatch.setattr(codec_alerts, "alert_once", lambda *a, **k: True)
    monkeypatch.setattr(codec_alerts, "alert_resolved", lambda *a, **k: False)
    asked = []

    def answer_with(text):
        def fake_ask(question, **kw):
            asked.append({"question": question, **kw})
            return text
        monkeypatch.setattr(codec_ask_user, "ask", fake_ask)

    from codec_memory import CodecMemory
    mem = CodecMemory(db_path=str(home / "memory.db"))
    for i in range(5):
        mem.save("s", "user", f"message {i}")
    mem.close()
    for rel, data in FILES.items():
        (home / rel).parent.mkdir(parents=True, exist_ok=True)
        (home / rel).write_bytes(data)
    assert codec_backup.backup_memory()["ok"] and codec_backup.backup_state()["ok"]
    return SimpleNamespace(home=home, bk=home / "backups", out=tmp_path / "restored", tool=_tool(),
                           asked=asked, answer_with=answer_with)


def _rows(path: Path) -> list:
    with sqlite3.connect(path) as c:
        return [r[0] for r in c.execute("SELECT content FROM conversations ORDER BY id")]


def test_database_round_trip_into_a_folder_has_equal_checksums(rs, capsys):
    backup = rs.bk / f"memory_{TODAY}.db"
    assert rs.tool.main(["restore", backup.name, "--to", str(rs.out)]) == 0
    restored = rs.out / "memory.db"
    assert _sha(restored) == _sha(backup)
    assert codec_backup.quick_check(restored) == "ok"
    assert codec_backup.content_checksum(restored) == codec_backup.content_checksum(rs.home / "memory.db")
    assert _rows(restored) == [f"message {i}" for i in range(5)]
    assert "checksums equal" in capsys.readouterr().out


def test_state_round_trip_into_a_folder_has_equal_checksums_and_leaves_out_the_allowlist(rs, capsys):
    assert rs.tool.main(["restore", f"state_{TODAY}.tar.gz", "--to", str(rs.out)]) == 0
    for rel, data in FILES.items():
        if rel == "plugins.allowlist":
            assert not (rs.out / rel).exists()
        else:
            assert hashlib.sha256((rs.out / rel).read_bytes()).hexdigest() == hashlib.sha256(data).hexdigest()
    out = capsys.readouterr().out
    assert "plugins.allowlist left out" in out and "checksums equal" in out


def test_restore_into_codec_home_or_a_full_folder_is_refused(rs):
    assert rs.tool.main(["restore", f"memory_{TODAY}.db", "--to", str(rs.home / "x")]) == 1
    assert not (rs.home / "x").exists()
    rs.out.mkdir()
    (rs.out / "something").write_text("x")
    assert rs.tool.main(["restore", f"memory_{TODAY}.db", "--to", str(rs.out)]) == 1


@pytest.mark.parametrize("answer", ["yes", "ok", "Cancel", "don't restore", codec_ask_user.TIMEOUT_SENTINEL,
                                    codec_ask_user.DISABLED_SENTINEL])
def test_a_live_restore_needs_the_literal_word_restore(rs, answer):
    rs.answer_with(answer)
    live = rs.home / "memory.db"
    with sqlite3.connect(live) as c:
        c.execute("DELETE FROM conversations")
    assert rs.tool.main(["restore", f"memory_{TODAY}.db", "--live"]) == 1
    assert _rows(live) == []  # untouched
    assert rs.asked and rs.asked[0]["destructive"] is True and rs.asked[0]["destructive_verb"] == "restore"
    assert "'restore'" in rs.asked[0]["question"]


def test_a_live_database_restore_keeps_a_checked_copy_of_the_current_one(rs):
    rs.answer_with("Restore")
    live = rs.home / "memory.db"
    with sqlite3.connect(live) as c:
        c.execute("INSERT INTO conversations (session_id, timestamp, role, content) VALUES ('s', ?, 'user', 'later')",
                  (datetime.now().isoformat(),))
    assert rs.tool.main(["restore", f"memory_{TODAY}.db", "--live"]) == 0
    assert _rows(live) == [f"message {i}" for i in range(5)]
    kept = rs.bk / f"memory_before-restore_{TODAY}.db"
    assert codec_backup.quick_check(kept) == "ok" and _rows(kept)[-1] == "later"
    assert oct(os.stat(live).st_mode & 0o777) == "0o600"


def test_a_live_state_restore_moves_the_current_files_aside_and_never_restores_the_allowlist(rs):
    rs.answer_with("restore")
    (rs.home / "config.json").write_bytes(b'{"changed": true}')
    (rs.home / "plugins.allowlist").write_bytes(b'{"evil.py": {"sha256": "bb"}}')
    (rs.home / "voices" / "codec-02" / "reference.txt").write_bytes(b"other voice")
    assert rs.tool.main(["restore", f"state_{TODAY}.tar.gz", "--live"]) == 0
    assert (rs.home / "config.json").read_bytes() == FILES["config.json"]
    assert (rs.home / "voices" / "codec-02" / "reference.txt").read_bytes() == b"hello"
    assert (rs.home / "plugins.allowlist").read_bytes() == b'{"evil.py": {"sha256": "bb"}}'
    aside = [p for p in rs.bk.iterdir() if p.name.startswith("before-restore_")]
    assert len(aside) == 1 and (aside[0] / "config.json").read_bytes() == b'{"changed": true}'
    assert not any(p.name.startswith(".restore-staging") for p in rs.bk.iterdir())
    assert "plugins.allowlist" not in rs.asked[0]["question"]


def test_only_the_allowlist_cannot_be_asked_for(rs):
    with pytest.raises(SystemExit):
        rs.tool.main(["restore", f"state_{TODAY}.tar.gz", "--live", "--only", "plugins.allowlist"])


def test_a_live_restore_reads_only_from_the_backups_folder(rs, tmp_path):
    rs.answer_with("restore")
    stray = tmp_path / "memory_2026-01-01.db"
    stray.write_bytes((rs.bk / f"memory_{TODAY}.db").read_bytes())
    assert rs.tool.main(["restore", str(stray), "--live"]) == 1
    assert rs.asked == []


def test_restore_is_a_tool_on_the_mac_not_a_skill_or_an_mcp_tool():
    assert not (REPO / "skills" / "codec_restore.py").exists()
    src = (REPO / "tools" / "codec_restore.py").read_text()
    assert "SKILL_NAME" not in src and "SKILL_MCP_EXPOSE" not in src
    skill = (REPO / "skills" / "backup_status.py").read_text()
    assert "codec_restore" not in skill.replace("tools/codec_restore.py", "")
