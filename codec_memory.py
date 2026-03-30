"""CODEC Memory — SQLite FTS5 full-text search over all conversations."""
import os, re, sqlite3
from datetime import datetime, timedelta

DB_PATH = os.path.expanduser("~/.q_memory.db")

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
    """Wraps ~/.q_memory.db with an FTS5 virtual table for instant search."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_fts()

    # ── Init ─────────────────────────────────────────────────────────────────

    def _init_fts(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            # Ensure conversations table exists
            conn.execute("""CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT, timestamp TEXT, role TEXT, content TEXT
            )""")

            # Standalone FTS5 table — stores its own copies of all searchable columns.
            # src_id links back to conversations.id for deduplication.
            conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS conversations_fts
                USING fts5(content, session_id, timestamp, role, src_id UNINDEXED)
            """)

            # Triggers to keep FTS in sync with the main table
            conn.execute("""CREATE TRIGGER IF NOT EXISTS conversations_ai
                AFTER INSERT ON conversations BEGIN
                    INSERT INTO conversations_fts(content, session_id, timestamp, role, src_id)
                    VALUES (new.content, new.session_id, new.timestamp, new.role, new.id);
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
                    INSERT INTO conversations_fts(content, session_id, timestamp, role, src_id)
                    VALUES (new.content, new.session_id, new.timestamp, new.role, new.id);
                END
            """)

            conn.commit()

            # Backfill FTS from existing rows not yet indexed
            count = conn.execute("SELECT COUNT(*) FROM conversations_fts").fetchone()[0]
            total = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            if count < total:
                conn.execute("""INSERT INTO conversations_fts(content, session_id, timestamp, role, src_id)
                    SELECT content, session_id, timestamp, role, id
                    FROM conversations
                    WHERE id NOT IN (SELECT src_id FROM conversations_fts)
                """)
                conn.commit()
        finally:
            conn.close()

    # ── CRUD ─────────────────────────────────────────────────────────────────

    def save(self, session_id: str, role: str, content: str) -> int:
        """Insert one message. Triggers keep FTS in sync automatically."""
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.execute(
                "INSERT INTO conversations (session_id, timestamp, role, content) VALUES (?,?,?,?)",
                (session_id, datetime.now().isoformat(), role, content[:4000]),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    # ── Search ───────────────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """Full-text search ranked by BM25. Returns list of row dicts."""
        sanitized = _sanitize_fts_query(query)
        if not sanitized:
            return []
        conn = sqlite3.connect(self.db_path)
        try:
            return self._fts_query(conn, sanitized, limit)
        except sqlite3.OperationalError:
            return []
        finally:
            conn.close()

    def _fts_query(self, conn, query: str, limit: int) -> list[dict]:
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

    def search_recent(self, days: int = 7, limit: int = 50) -> list[dict]:
        """Return recent conversations from the past N days."""
        since = (datetime.now() - timedelta(days=days)).isoformat()
        conn = sqlite3.connect(self.db_path)
        try:
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
        finally:
            conn.close()

    def get_context(self, query: str, n: int = 5) -> str:
        """Return a formatted string of top-N matching snippets for LLM injection."""
        hits = self.search(query, limit=n)
        if not hits:
            return ""
        lines = ["[Memory context]"]
        for h in hits:
            ts = h["timestamp"][:16].replace("T", " ")
            snippet = h["content"][:300].replace("\n", " ")
            lines.append(f"  [{ts}] {h['role'].upper()}: {snippet}")
        return "\n".join(lines)

    def get_sessions(self, limit: int = 20) -> list[dict]:
        """Return distinct sessions with message count and last timestamp."""
        conn = sqlite3.connect(self.db_path)
        try:
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
        finally:
            conn.close()

    def cleanup(self, retention_days: int = 90) -> dict:
        """Delete conversations older than retention_days and VACUUM the database.
        Returns dict with deleted count and final size."""
        cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat()
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            before = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            conn.execute("DELETE FROM conversations WHERE timestamp < ?", (cutoff,))
            conn.commit()
            after = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            deleted = before - after
            # Rebuild FTS after bulk delete
            if deleted > 0:
                conn.execute("INSERT INTO conversations_fts(conversations_fts) VALUES('rebuild')")
                conn.commit()
            conn.execute("VACUUM")
            size = os.path.getsize(self.db_path)
            return {"deleted": deleted, "remaining": after, "size_bytes": size}
        finally:
            conn.close()

    def rebuild_fts(self) -> int:
        """Full FTS rebuild — use after bulk imports. Returns row count."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("INSERT INTO conversations_fts(conversations_fts) VALUES('rebuild')")
            conn.commit()
            count = conn.execute("SELECT COUNT(*) FROM conversations_fts").fetchone()[0]
            return count
        finally:
            conn.close()


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
