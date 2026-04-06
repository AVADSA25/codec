"""CODEC Memory — SQLite FTS5 full-text search over all conversations."""
import logging, os, re, sqlite3
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2

from codec_config import DB_PATH  # single source of truth

_FTS5_MAX_QUERY_LEN = 200
_FTS5_OPERATORS = re.compile(r'\b(NEAR|AND|OR|NOT)\b', re.IGNORECASE)
_FTS5_SPECIAL = re.compile(r'[*"()\^]')


def _sanitize_fts_query(raw: str) -> str:
    """Strip FTS5 special operators/chars to prevent injection.

    Removes: *, ", NEAR, AND, OR, NOT, (, ), ^
    Truncates to 200 chars. Returns empty string if nothing remains.
    """
    q = _FTS5_OPERATORS.sub(' ', raw)
    q = _FTS5_SPECIAL.sub('', q)
    q = ' '.join(q.split())          # collapse whitespace
    return q[:_FTS5_MAX_QUERY_LEN].strip()


class CodecMemory:
    """Wraps ~/.codec/memory.db with an FTS5 virtual table for instant search."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._conn = None
        self._init_fts()

    # ── Connection ────────────────────────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        """Return a reusable connection (created once, kept open)."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
        return self._conn

    def close(self):
        """Close the persistent connection. Safe to call multiple times."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception as e:
                log.debug("Memory DB connection close failed: %s", e)
            self._conn = None

    # ── Init ─────────────────────────────────────────────────────────────────

    def _init_fts(self):
        conn = self._get_conn()
        try:
            # Ensure conversations table exists
            conn.execute("""CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT, timestamp TEXT, role TEXT, content TEXT,
                user_id TEXT DEFAULT 'default'
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_session ON conversations(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_ts ON conversations(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id)")

            # Standalone FTS5 table — stores its own copies of all searchable columns.
            # src_id and user_id link back for deduplication/filtering.
            conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS conversations_fts
                USING fts5(content, session_id, timestamp, role, src_id UNINDEXED, user_id UNINDEXED)
            """)

            # Triggers to keep FTS in sync with the main table
            conn.execute("""CREATE TRIGGER IF NOT EXISTS conversations_ai
                AFTER INSERT ON conversations BEGIN
                    INSERT INTO conversations_fts(content, session_id, timestamp, role, src_id, user_id)
                    VALUES (new.content, new.session_id, new.timestamp, new.role, new.id, new.user_id);
                END
            """)
            conn.execute("""CREATE TRIGGER IF NOT EXISTS conversations_ad
                AFTER DELETE ON conversations BEGIN
                    DELETE FROM conversations_fts WHERE src_id = old.id;
                END
            """)
            conn.execute("""CREATE TRIGGER IF NOT EXISTS conversations_au
                AFTER UPDATE ON conversations BEGIN
                    DELETE FROM conversations_fts WHERE src_id = old.id;
                    INSERT INTO conversations_fts(content, session_id, timestamp, role, src_id, user_id)
                    VALUES (new.content, new.session_id, new.timestamp, new.role, new.id, new.user_id);
                END
            """)

            conn.commit()

            # Schema versioning — run migrations if needed
            current_version = conn.execute("PRAGMA user_version").fetchone()[0]
            if current_version < SCHEMA_VERSION:
                self._migrate(conn, current_version)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                conn.commit()

            # Backfill FTS from existing rows not yet indexed
            count = conn.execute("SELECT COUNT(*) FROM conversations_fts").fetchone()[0]
            total = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            if count < total:
                conn.execute("""INSERT INTO conversations_fts(content, session_id, timestamp, role, src_id, user_id)
                    SELECT content, session_id, timestamp, role, id, user_id
                    FROM conversations
                    WHERE id NOT IN (SELECT src_id FROM conversations_fts)
                """)
                conn.commit()
        except Exception:
            raise

    # ── Migrations ───────────────────────────────────────────────────────────

    def _migrate(self, conn, from_version):
        """Run schema migrations from from_version to SCHEMA_VERSION."""
        if from_version < 1:
            # v1: ensure all baseline tables and indexes exist
            # (already handled by _init_fts, this is a placeholder for future migrations)
            pass
        if from_version < 2:
            # v2: add user_id for multi-tenancy support
            try:
                conn.execute("ALTER TABLE conversations ADD COLUMN user_id TEXT DEFAULT 'default'")
            except sqlite3.OperationalError:
                pass  # column already exists
            conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id)")
            # Rebuild FTS to include user_id — drop old triggers/table and recreate
            # (handled by _init_fts CREATE IF NOT EXISTS; for existing DBs we rebuild)
            try:
                conn.execute("DROP TRIGGER IF EXISTS conversations_ai")
                conn.execute("DROP TRIGGER IF EXISTS conversations_ad")
                conn.execute("DROP TRIGGER IF EXISTS conversations_au")
                conn.execute("DROP TABLE IF EXISTS conversations_fts")
                conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS conversations_fts
                    USING fts5(content, session_id, timestamp, role, src_id UNINDEXED, user_id UNINDEXED)
                """)
                conn.execute("""CREATE TRIGGER IF NOT EXISTS conversations_ai
                    AFTER INSERT ON conversations BEGIN
                        INSERT INTO conversations_fts(content, session_id, timestamp, role, src_id, user_id)
                        VALUES (new.content, new.session_id, new.timestamp, new.role, new.id, new.user_id);
                    END
                """)
                conn.execute("""CREATE TRIGGER IF NOT EXISTS conversations_ad
                    AFTER DELETE ON conversations BEGIN
                        DELETE FROM conversations_fts WHERE src_id = old.id;
                    END
                """)
                conn.execute("""CREATE TRIGGER IF NOT EXISTS conversations_au
                    AFTER UPDATE ON conversations BEGIN
                        DELETE FROM conversations_fts WHERE src_id = old.id;
                        INSERT INTO conversations_fts(content, session_id, timestamp, role, src_id, user_id)
                        VALUES (new.content, new.session_id, new.timestamp, new.role, new.id, new.user_id);
                    END
                """)
                # Backfill FTS from all existing rows
                conn.execute("""INSERT INTO conversations_fts(content, session_id, timestamp, role, src_id, user_id)
                    SELECT content, session_id, timestamp, role, id, user_id
                    FROM conversations
                """)
            except Exception as e:
                log.warning("FTS migration to v2 partially failed: %s", e)

    # ── CRUD ─────────────────────────────────────────────────────────────────

    def save(self, session_id: str, role: str, content: str, user_id: str = "default") -> int:
        """Insert one message. Triggers keep FTS in sync automatically."""
        conn = self._get_conn()
        cur = conn.execute(
            "INSERT INTO conversations (session_id, timestamp, role, content, user_id) VALUES (?,?,?,?,?)",
            (session_id, datetime.now().isoformat(), role, content[:4000], user_id),
        )
        conn.commit()
        return cur.lastrowid

    # ── Search ───────────────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 10, user_id: str = None) -> list[dict]:
        """Full-text search ranked by BM25. Returns list of row dicts.
        If user_id is provided, only return results for that user."""
        sanitized = _sanitize_fts_query(query)
        if not sanitized:
            return []
        conn = self._get_conn()
        try:
            return self._fts_query(conn, sanitized, limit, user_id=user_id)
        except sqlite3.OperationalError:
            return []

    def _fts_query(self, conn, query: str, limit: int, user_id: str = None) -> list[dict]:
        if user_id is not None:
            rows = conn.execute("""
                SELECT src_id, session_id, timestamp, role, content,
                       bm25(conversations_fts) AS score
                FROM conversations_fts
                WHERE conversations_fts MATCH ? AND user_id = ?
                ORDER BY score
                LIMIT ?
            """, (query, user_id, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT src_id, session_id, timestamp, role, content,
                       bm25(conversations_fts) AS score
                FROM conversations_fts
                WHERE conversations_fts MATCH ?
                ORDER BY score
                LIMIT ?
            """, (query, limit)).fetchall()
        return [
            {"id": r[0], "session_id": r[1], "timestamp": r[2],
             "role": r[3], "content": r[4], "score": round(r[5], 4)}
            for r in rows
        ]

    def search_recent(self, days: int = 7, limit: int = 50, user_id: str = None) -> list[dict]:
        """Return recent conversations from the past N days.
        If user_id is provided, only return results for that user."""
        since = (datetime.now() - timedelta(days=days)).isoformat()
        conn = self._get_conn()
        if user_id is not None:
            rows = conn.execute("""
                SELECT id, session_id, timestamp, role, content
                FROM conversations
                WHERE timestamp >= ? AND user_id = ?
                ORDER BY id DESC
                LIMIT ?
            """, (since, user_id, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT id, session_id, timestamp, role, content
                FROM conversations
                WHERE timestamp >= ?
                ORDER BY id DESC
                LIMIT ?
            """, (since, limit)).fetchall()
        return [
            {"id": r[0], "session_id": r[1], "timestamp": r[2],
             "role": r[3], "content": r[4]}
            for r in rows
        ]

    def get_context(self, query: str, n: int = 5, user_id: str = None) -> str:
        """Return a formatted string of top-N matching snippets for LLM injection."""
        hits = self.search(query, limit=n, user_id=user_id)
        if not hits:
            return ""
        lines = ["[Memory context]"]
        for h in hits:
            ts = h["timestamp"][:16].replace("T", " ")
            snippet = h["content"][:300].replace("\n", " ")
            lines.append(f"  [{ts}] {h['role'].upper()}: {snippet}")
        return "\n".join(lines)

    def get_sessions(self, limit: int = 20, user_id: str = None) -> list[dict]:
        """Return distinct sessions with message count and last timestamp.
        If user_id is provided, only return sessions for that user."""
        conn = self._get_conn()
        if user_id is not None:
            rows = conn.execute("""
                SELECT session_id,
                       COUNT(*) AS msg_count,
                       MIN(timestamp) AS started,
                       MAX(timestamp) AS last_msg,
                       MAX(CASE WHEN role='user' THEN content ELSE '' END) AS last_user_msg
                FROM conversations
                WHERE user_id = ?
                GROUP BY session_id
                ORDER BY last_msg DESC
                LIMIT ?
            """, (user_id, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT session_id,
                       COUNT(*) AS msg_count,
                       MIN(timestamp) AS started,
                       MAX(timestamp) AS last_msg,
                       MAX(CASE WHEN role='user' THEN content ELSE '' END) AS last_user_msg
                FROM conversations
                GROUP BY session_id
                ORDER BY last_msg DESC
                LIMIT ?
            """, (limit,)).fetchall()
        return [
            {"session_id": r[0], "msg_count": r[1],
             "started": r[2], "last_msg": r[3],
             "preview": (r[4] or "")[:100]}
            for r in rows
        ]

    def cleanup(self, retention_days: int = 90) -> dict:
        """Delete conversations older than retention_days and VACUUM the database.
        Returns dict with deleted count and final size."""
        cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat()
        conn = self._get_conn()
        before = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        conn.execute("DELETE FROM conversations WHERE timestamp < ?", (cutoff,))
        conn.commit()
        after = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        deleted = before - after
        # Rebuild FTS after bulk delete
        if deleted > 0:
            conn.execute("INSERT INTO conversations_fts(conversations_fts) VALUES('rebuild')")
            conn.commit()
        # VACUUM requires closing and reopening (cannot run inside a transaction on reused conn)
        self.close()
        tmp = sqlite3.connect(self.db_path)
        tmp.execute("VACUUM")
        tmp.close()
        size = os.path.getsize(self.db_path)
        return {"deleted": deleted, "remaining": after, "size_bytes": size}

    def rebuild_fts(self) -> int:
        """Full FTS rebuild — use after bulk imports. Returns row count."""
        conn = self._get_conn()
        conn.execute("INSERT INTO conversations_fts(conversations_fts) VALUES('rebuild')")
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM conversations_fts").fetchone()[0]
        return count


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    mem = CodecMemory()
    if len(sys.argv) < 2:
        print("Usage: python codec_memory.py search <query>")
        print("       python codec_memory.py recent [days]")
        print("       python codec_memory.py sessions")
        print("       python codec_memory.py rebuild")
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd == "search" and len(sys.argv) > 2:
        q = " ".join(sys.argv[2:])
        results = mem.search(q)
        if not results:
            print("No matches.")
        for r in results:
            print(f"[{r['timestamp'][:16]}] {r['role'].upper()} (score {r['score']}): {r['content'][:200]}")
    elif cmd == "recent":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 7
        results = mem.search_recent(days)
        for r in results:
            print(f"[{r['timestamp'][:16]}] {r['role'].upper()}: {r['content'][:150]}")
    elif cmd == "sessions":
        for s in mem.get_sessions():
            print(f"  {s['session_id']} | {s['msg_count']} msgs | last: {s['last_msg'][:16]} | {s['preview']}")
    elif cmd == "rebuild":
        n = mem.rebuild_fts()
        print(f"FTS rebuilt — {n} rows indexed.")
    elif cmd == "cleanup":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 90
        result = mem.cleanup(retention_days=days)
        print(f"Cleanup: deleted {result['deleted']} old messages, {result['remaining']} remaining, DB size: {result['size_bytes'] / 1024:.0f} KB")
    else:
        print("Unknown command.")
