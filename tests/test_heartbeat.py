import json
import logging
import os
import sqlite3
from datetime import datetime


def test_heartbeat_import():
    from codec_heartbeat import heartbeat
    assert heartbeat is not None

def test_heartbeat_runs(monkeypatch):
    # Backups and the retention clean-up never touch the owner's files from a test: codec_backup
    # works in a temp folder (tests/conftest.py), and these stubs also cover an older codec_heartbeat
    # loaded from ~/codec-repo, whose clean-up opened the real memory.db.
    import codec_heartbeat
    import codec_memory

    class _NoMemory:
        def __init__(self, *a, **k):
            pass

        def cleanup(self, retention_days=90):
            return {"deleted": 0, "remaining": 0, "size_bytes": 0}

        def close(self):
            pass

    monkeypatch.setattr(codec_memory, "CodecMemory", _NoMemory)
    monkeypatch.setattr(codec_heartbeat, "backup_memory_db", lambda: None)
    tasks = codec_heartbeat.heartbeat()
    assert isinstance(tasks, list)


# ── P-032: heartbeat log lines carry lengths, never message text ──────────────

def _chat_db(tmp_path, *contents):
    db = tmp_path / "memory.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, "
                 "timestamp TEXT, role TEXT, content TEXT)")
    for text in contents:
        conn.execute("INSERT INTO conversations (session_id, timestamp, role, content) VALUES (?, ?, ?, ?)",
                     ("s1", datetime.now().isoformat(), "assistant", text))
    conn.commit()
    conn.close()
    return str(db)


def _home_in(tmp_path, monkeypatch):
    """`~` inside the functions under test is tmp_path: executed_tasks.json and notifications.json land there."""
    (tmp_path / ".codec").mkdir(exist_ok=True)
    real = os.path.expanduser
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path) + p[1:] if p.startswith("~") else real(p))


def test_pending_task_lines_log_lengths_not_the_chat_text(tmp_path, monkeypatch, caplog):
    import codec_heartbeat
    text = "Remind me later about the SECRET-PLAN-42 meeting"
    monkeypatch.setattr(codec_heartbeat, "DB_PATH", _chat_db(tmp_path, text))
    caplog.set_level(logging.INFO, logger="heartbeat")
    assert len(codec_heartbeat.check_pending_tasks()) == 1
    assert f"{len(text)} characters" in caplog.text and "SECRET-PLAN-42" not in caplog.text


def test_auto_task_lines_log_lengths_not_the_task(tmp_path, monkeypatch, caplog):
    import codec_heartbeat
    safe = "I logged the task to open the SECRET-DOOR-7 report."
    risky = "I logged the task to rm -rf ~/SECRET-DIR-9 now."
    monkeypatch.setattr(codec_heartbeat, "DB_PATH", _chat_db(tmp_path, safe, risky))
    _home_in(tmp_path, monkeypatch)
    posted = []

    class Ok:
        status_code = 200
    monkeypatch.setattr(codec_heartbeat.requests, "post", lambda url, **k: posted.append(k["json"]) or Ok())
    caplog.set_level(logging.INFO, logger="heartbeat")
    codec_heartbeat.execute_pending_tasks()
    assert posted == [{"command": "open the SECRET-DOOR-7 report", "source": "heartbeat"}], "the task still runs"
    assert "BLOCKED dangerous auto-task (" in caplog.text and "Auto-executing a task (29 characters)" in caplog.text
    assert "SECRET-DOOR-7" not in caplog.text and "SECRET-DIR-9" not in caplog.text
    assert len(json.loads((tmp_path / ".codec" / "executed_tasks.json").read_text())) == 2


def test_mail_alert_line_logs_the_count_not_the_preview(tmp_path, monkeypatch, caplog):
    import codec_alerts
    import codec_dispatch
    import codec_heartbeat
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"heartbeat_alerts": [{"type": "email_check", "name": "Mail"}]}))
    monkeypatch.setattr(codec_heartbeat, "CONFIG_PATH", str(cfg))
    monkeypatch.setattr(codec_alerts, "ALERT_STATE_PATH", str(tmp_path / "alert_state.json"))
    monkeypatch.setattr(codec_dispatch, "run_feature",
                        lambda name, task, source: "Found 2 emails:\n* Alice: SECRET-SUBJECT-3\n")
    banners = []
    monkeypatch.setattr(codec_heartbeat.subprocess, "run", lambda args, **k: banners.append(args))
    _home_in(tmp_path, monkeypatch)
    caplog.set_level(logging.INFO, logger="heartbeat")
    assert codec_heartbeat.check_alerts() == ["Mail: 2 unread emails — Alice: SECRET-SUBJECT-3"], \
        "the owner's own alert keeps its preview"
    assert "Mail: 2 unread emails" in caplog.text and "SECRET-SUBJECT-3" not in caplog.text
    assert len(banners) == 1
