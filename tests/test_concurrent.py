"""Concurrent access tests — verify CODEC handles simultaneous operations safely."""
import asyncio
import os
import sqlite3
import sys
import threading
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestMemoryDBConcurrent:
    """SQLite concurrent write safety."""

    def test_10_simultaneous_writes(self, tmp_path):
        """10 threads writing to memory DB simultaneously — no 'database is locked'."""
        db_path = str(tmp_path / "test_concurrent.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, timestamp TEXT, role TEXT, content TEXT)")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.commit()
        conn.close()

        errors = []

        def writer(thread_id):
            try:
                c = sqlite3.connect(db_path, timeout=10)
                c.execute("PRAGMA journal_mode=WAL")
                for i in range(10):
                    c.execute(
                        "INSERT INTO conversations (session_id, timestamp, role, content) VALUES (?, ?, ?, ?)",
                        (f"session_{thread_id}", f"2026-04-06T{thread_id:02d}:{i:02d}:00", "user", f"msg_{thread_id}_{i}"),
                    )
                    c.commit()
                c.close()
            except Exception as e:
                errors.append(f"Thread {thread_id}: {e}")

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"Concurrent writes failed: {errors}"

        # Verify all rows written
        c = sqlite3.connect(db_path)
        count = c.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        c.close()
        assert count == 100, f"Expected 100 rows, got {count}"

    def test_read_during_writes(self, tmp_path):
        """Reads succeed while writes are in progress (WAL mode)."""
        db_path = str(tmp_path / "test_rw.db")
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE conversations (id INTEGER PRIMARY KEY, content TEXT)")
        for i in range(100):
            conn.execute("INSERT INTO conversations (content) VALUES (?)", (f"row_{i}",))
        conn.commit()
        conn.close()

        read_results = []
        write_errors = []

        def reader():
            c = sqlite3.connect(db_path, timeout=10)
            for _ in range(5):
                rows = c.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
                read_results.append(rows)
            c.close()

        def writer():
            try:
                c = sqlite3.connect(db_path, timeout=10)
                for i in range(50):
                    c.execute("INSERT INTO conversations (content) VALUES (?)", (f"new_{i}",))
                    c.commit()
                c.close()
            except Exception as e:
                write_errors.append(str(e))

        t1 = threading.Thread(target=reader)
        t2 = threading.Thread(target=writer)
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        assert not write_errors, f"Write errors: {write_errors}"
        assert len(read_results) == 5
        assert all(r >= 100 for r in read_results)


class TestLLMPriorityQueue:
    """Verify priority queue ordering."""

    def test_medium_yields_to_critical(self):
        """MEDIUM requests yield when CRITICAL is waiting."""
        from codec_llm_proxy import LLMQueue, Priority

        async def run_test():
            queue = LLMQueue(max_concurrent=1)

            # Verify medium checks for waiting critical/high before acquiring
            async with queue._lock:
                queue._waiting[Priority.CRITICAL] = 1

            # Medium should loop-wait while critical is "waiting"
            acquired = False
            async def try_medium():
                nonlocal acquired
                # Give it 0.3s max — it should NOT acquire because critical is waiting
                try:
                    await asyncio.wait_for(queue.acquire(Priority.MEDIUM), timeout=0.3)
                    acquired = True
                except asyncio.TimeoutError:
                    pass

            await try_medium()
            assert not acquired, "MEDIUM should yield when CRITICAL is waiting"

            # Clear the fake critical waiter
            async with queue._lock:
                queue._waiting[Priority.CRITICAL] = 0

        asyncio.run(run_test())

    def test_stats_tracking(self):
        """Queue tracks request counts and wait times."""
        from codec_llm_proxy import LLMQueue, Priority

        async def run_test():
            queue = LLMQueue(max_concurrent=2)
            await queue.acquire(Priority.CRITICAL)
            await queue.release(Priority.CRITICAL)
            await queue.acquire(Priority.MEDIUM)
            await queue.release(Priority.MEDIUM)

            stats = queue.stats
            assert stats["total_requests"]["CRITICAL"] == 1
            assert stats["total_requests"]["MEDIUM"] == 1

        asyncio.run(run_test())

    def test_context_manager(self):
        """Async context manager acquires and releases correctly."""
        from codec_llm_proxy import LLMQueue, Priority

        async def run_test():
            queue = LLMQueue(max_concurrent=2)
            async with queue.slot(Priority.HIGH):
                assert queue.stats["active"]["HIGH"] == 1
            assert queue.stats["active"]["HIGH"] == 0

        asyncio.run(run_test())


class TestSyncLLMQueue:
    """Test synchronous queue wrapper for subprocess callers."""

    def test_sync_slot(self):
        from codec_llm_proxy import llm_queue_sync, Priority

        with llm_queue_sync(Priority.MEDIUM):
            pass  # Should not raise

    def test_sync_concurrent(self):
        from codec_llm_proxy import _SyncLLMSlot, Priority

        slot = _SyncLLMSlot(max_concurrent=2)
        results = []

        def worker(i):
            with slot(Priority.MEDIUM):
                results.append(i)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert len(results) == 5
