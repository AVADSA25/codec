"""Fix #5 (C5 / H14 / M6): SQLite + JSON state locking.

Two race classes the audit flagged:

1. CodecMemory shares one sqlite3 connection across threads
   (`check_same_thread=False`) with no app-level lock — concurrent save()
   calls interleave on the same connection.
2. read-modify-write of ~/.codec JSON state without a cross-process
   file_lock — concurrent writers clobber each other (grant_permission on
   grants.json; _write_question_notification on notifications.json).

Each test below fails against the pre-fix code for the RIGHT reason (observed
overlap / lost write), and passes once the lock is added.
"""
import threading
import time

import codec_agent_plan
import codec_memory


# ── 1. SQLite connection serialization ──────────────────────────────────────
def test_concurrent_save_serializes_db_access(tmp_path):
    # NOTE: a fully MOCK connection is used deliberately. Letting real
    # concurrent execute() calls hit the same sqlite3 connection segfaults the
    # interpreter (that crash IS the C5 bug) — which would kill the test runner
    # rather than produce a clean assertion. The mock measures whether
    # CodecMemory.save serializes access to the connection object, which is
    # exactly what the RLock fix provides, without invoking the C layer.
    mem = codec_memory.CodecMemory(db_path=str(tmp_path / "mem.db"))

    class _OverlapProbe:
        """Stands in for the live connection. Flags any concurrent re-entry of
        execute(); a widened window (sleep) makes overlap observable when the
        caller is NOT serializing access."""

        def __init__(self):
            self._n = 0
            self.max_concurrent = 0
            self._rowid = 0
            self._cl = threading.Lock()

        def execute(self, *a, **k):
            with self._cl:
                self._n += 1
                self.max_concurrent = max(self.max_concurrent, self._n)
                self._rowid += 1
                rowid = self._rowid
            try:
                time.sleep(0.003)
            finally:
                with self._cl:
                    self._n -= 1
            return type("_Cur", (), {"lastrowid": rowid})()

        def commit(self):
            pass

    probe = _OverlapProbe()
    mem._conn = probe  # _get_conn() returns this since it's non-None

    def worker(i):
        for j in range(4):
            mem.save(f"s{i}", "user", f"m{i}-{j}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert probe.max_concurrent == 1, (
        f"CodecMemory.save overlapped on the shared connection "
        f"(max concurrent execute={probe.max_concurrent}); not serialized (C5)"
    )


# ── 2. grants.json read-modify-write under file_lock ─────────────────────────
def test_concurrent_grant_permission_no_clobber(tmp_path, monkeypatch):
    from routes.agents import GrantBody, grant_permission

    agents_dir = tmp_path / "agents"
    monkeypatch.setattr(codec_agent_plan, "_AGENTS_DIR", agents_dir)

    # Widen the read-modify-write window so an unlocked path reliably clobbers.
    real_save = codec_agent_plan.save_grants

    def slow_save(agent_id, grants):
        time.sleep(0.02)
        return real_save(agent_id, grants)

    monkeypatch.setattr(codec_agent_plan, "save_grants", slow_save)

    aid = "agent_test"
    codec_agent_plan.save_manifest(aid, {"id": aid, "status": "running", "title": "t"})
    codec_agent_plan.save_grants(
        aid,
        {"skills": ["web_search"], "network_domains": [], "read_paths": [], "write_paths": []},
    )

    n = 12
    barrier = threading.Barrier(n)
    errors = []

    def worker(i):
        try:
            barrier.wait()  # maximize contention on the load->modify->save window
            grant_permission(
                aid, GrantBody(kind="network_domains", value=f"d{i}.example.com"), request=None
            )
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"grant_permission raised: {errors}"
    final = codec_agent_plan.load_grants(aid)["network_domains"]
    assert sorted(final) == sorted(f"d{i}.example.com" for i in range(n)), (
        f"grants clobbered: expected {n} domains, got {len(final)}: {sorted(final)}"
    )


# ── 3. notifications.json write holds the cross-process file_lock ────────────
def test_question_notification_uses_cross_process_file_lock(tmp_path, monkeypatch):
    import codec_ask_user

    notifs_path = tmp_path / "notifications.json"
    monkeypatch.setattr(codec_ask_user, "NOTIFICATIONS_PATH", notifs_path)

    locked_paths = []
    real_file_lock = codec_ask_user.codec_jsonstore.file_lock

    def spy_file_lock(path):
        locked_paths.append(str(path))
        return real_file_lock(path)

    monkeypatch.setattr(codec_ask_user.codec_jsonstore, "file_lock", spy_file_lock)

    codec_ask_user._write_question_notification(
        {"id": "q_abc", "question": "proceed?", "agent": None, "options": None,
         "deadline": None, "consent_strict": False}
    )

    assert str(notifs_path) in locked_paths, (
        "_write_question_notification must hold codec_jsonstore.file_lock on "
        "notifications.json across its read-modify-write (cross-process safety)"
    )
