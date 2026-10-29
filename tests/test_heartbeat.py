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
