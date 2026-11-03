"""CODEC Memory — SQLite FTS5 full-text search over all conversations."""
import logging
import os
import re
import sqlite3
import threading
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


# ── Query builder for search() (P-062, docs/MEMORY-RECALL-DESIGN.md) ─────────
# A query word: letters, digits and underscores, joined by an inner ' - or .
# ("what's", "follow-up", "3.7.0"). Anything else between words is a gap.
_QUERY_WORD = re.compile(r"\w+(?:['\u2019.\-]\w+)*")
_POSSESSIVE = re.compile(r"['\u2019]s$", re.IGNORECASE)
_LIKE_MAX_TERMS = 8

# Words that say how a question is asked, not what it is about. They are left
# out of the content-word passes of search(), so "what's the weather" does not
# return every row with "what" or "the" in it. A query made only of these words
# still runs the first pass (every word present), as search() always did.
_COMMON_WORDS = frozenset("""
a about above after again against ago all also am an and any anything are aren't arent as at
be because been before being below between both but by
can can't cannot cant could couldn't couldnt
did didn't didnt do does doesn't doesnt doing don't dont done down during
each earlier either else ever every everything
few for from further
get gets getting got
had hadn't hadnt has hasn't hasnt have haven't havent having he he'd he'll he's hello her here
here's hers herself hey hi him himself his how how's
i i'd i'll i'm i've if im in into is isn't isnt it it's its itself ive
just know last later let let's lets like
me might more most much must my myself
next no nor not nothing now
of off ok okay on once only or other our ours ourselves out over own
please really recent recently
same shall she she'd she'll she's should shouldn't shouldnt so some something still such
than thank thanks that that's thats the their theirs them themselves then there there's theres
these they they'd they'll they're they've theyre thing things think this those though through to
today tomorrow tonight too
under until up us
very
was wasn't wasnt we we'd we'll we're we've were weren't werent what what's whatever whats when
when's where where's whether which while who who's whom whose why why's will with won't wont would
wouldn't wouldnt
yes yesterday yet you you'd you'll you're you've your youre yours yourself yourselves
ask asked chat chatted conversation conversations discuss discussed discussing discussion find
look looking mention mentioned recall recalled remember remembered remind reminded said say saying
says search show talk talked talking tell telling told
""".split())


def _word_key(word: str) -> str:
    return word.lower().replace("\u2019", "'")


def _query_words(sanitized: str) -> tuple[list[str], list[str]]:
    """Split an already sanitized query into its words (in order, each once)
    and its content words: the same without common words, a trailing
    possessive dropped ("hotel's" looks for "hotel")."""
    words, seen = [], set()
    for raw in _QUERY_WORD.findall(sanitized):
        word = raw.strip("_")
        key = _word_key(word)
        if word and key not in seen:
            seen.add(key)
            words.append(word)
    content, seen = [], set()
    for word in words:
        if _word_key(word) in _COMMON_WORDS:
            continue
        word = _POSSESSIVE.sub("", word) or word
        key = _word_key(word)
        if key not in seen:
            seen.add(key)
            content.append(word)
    return words, content


def _fts_phrase(word: str, column: str = "") -> str:
    """One word as an FTS5 string, so no character in it is read as syntax."""
    phrase = '"' + word.replace('"', '""') + '"'
    return f"{column}:{phrase}" if column else phrase


def _fts_queries(words: list[str], content: list[str]) -> list[str]:
    """The MATCH expressions search() runs, most exact first: every word in
    any column (what search() returned before P-062), then every content word
    in the message text, then any content word in the message text."""
    queries = [" ".join(_fts_phrase(w) for w in words)]
    if content and content != words:
        queries.append(" ".join(_fts_phrase(w, "content") for w in content))
    if len(content) > 1:
        queries.append(" OR ".join(_fts_phrase(w, "content") for w in content))
    return queries


def _like_escape(word: str) -> str:
    return word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# P-037 (docs/MEMORY-PROVENANCE-DESIGN.md): rows a prompt never carries. Crew and scheduled crew
# results are saved with role "agent" (codec_agents.save_to_memory), an untrusted memory write gets
# role "untrusted" (P-021), and crew sessions have always been named "agents_<crew>_<time>", which
# also covers crew rows saved as user / assistant before this. Read at query time; no row changes.
PROMPT_LEFT_OUT_ROLES = ("agent", "untrusted")
AGENT_SESSION_PREFIX = "agents_"


def _prompt_clause(for_prompt: bool) -> tuple[str, list]:
    """The extra WHERE condition (and its values) that leaves out PROMPT_LEFT_OUT_ROLES and crew
    sessions, or ("", []) when `for_prompt` is off."""
    if not for_prompt:
        return "", []
    roles = list(PROMPT_LEFT_OUT_ROLES)
    sql = (f" AND COALESCE(role,'') NOT IN ({','.join('?' * len(roles))})"
           " AND COALESCE(session_id,'') NOT LIKE ? ESCAPE '\\'")
    return sql, roles + [_like_escape(AGENT_SESSION_PREFIX) + "%"]


class CodecMemory:
    """Wraps ~/.codec/memory.db with an FTS5 virtual table for instant search."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._conn = None
        # C5 (Fix #5): serialize all runtime use of the shared sqlite3
        # connection (check_same_thread=False) across threads — concurrent
        # access to one connection can corrupt cursor state or segfault.
        # RLock so a locked method calling another (get_context->search,
        # cleanup->close) doesn't self-deadlock. WAL + busy_timeout remain the
        # cross-PROCESS layer; this lock is the in-process one.
        self._lock = threading.RLock()
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
        with self._lock:
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

            # Migrate existing tables: add user_id if missing
            cols = {r[1] for r in conn.execute("PRAGMA table_info(conversations)").fetchall()}
            if "user_id" not in cols:
                conn.execute("ALTER TABLE conversations ADD COLUMN user_id TEXT DEFAULT 'default'")
                conn.commit()
                log.info("Migrated conversations table: added user_id column")

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
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(
                "INSERT INTO conversations (session_id, timestamp, role, content, user_id) VALUES (?,?,?,?,?)",
                (session_id, datetime.now().isoformat(), role, content[:4000], user_id),
            )
            conn.commit()
            return cur.lastrowid

    # ── Search ───────────────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 10, user_id: str = None,
               for_prompt: bool = False) -> list[dict]:
        """Full-text search ranked by BM25. Returns list of row dicts.
        If user_id is provided, only return results for that user.
        `for_prompt` leaves out agent and untrusted rows and crew sessions (P-037).

        Every word is quoted, so nothing in the query is FTS5 syntax. Rows come
        from the passes of _fts_queries (every word, then every content word,
        then any content word), each ranked by BM25, no row twice. If the FTS
        index fails, the conversations table is searched with LIKE instead."""
        sanitized = _sanitize_fts_query(query)
        if not sanitized or limit < 1:
            return []
        words, content = _query_words(sanitized)
        if not words:
            return []
        with self._lock:
            conn = self._get_conn()
            try:
                return self._fts_search(conn, words, content, limit, user_id=user_id,
                                        for_prompt=for_prompt)
            except sqlite3.OperationalError as e:
                log.warning("Memory search: full-text index failed (%s); searching the "
                            "conversations table with LIKE", type(e).__name__)
            try:
                return self._like_query(conn, words, content, limit, user_id=user_id,
                                        for_prompt=for_prompt)
            except sqlite3.OperationalError as e:
                log.warning("Memory search: LIKE fallback failed too (%s)", type(e).__name__)
                return []

    def _fts_search(self, conn, words: list[str], content: list[str], limit: int,
                    user_id: str = None, for_prompt: bool = False) -> list[dict]:
        found, seen = [], set()
        for match in _fts_queries(words, content):
            if len(found) >= limit:
                break
            for row in self._fts_query(conn, match, limit, user_id=user_id, for_prompt=for_prompt):
                if row["id"] not in seen:
                    seen.add(row["id"])
                    found.append(row)
        return found[:limit]

    def _like_query(self, conn, words: list[str], content: list[str], limit: int,
                    user_id: str = None, for_prompt: bool = False) -> list[dict]:
        """Fallback when the FTS index fails: substring match on the message
        text. Rows holding any content word, more of them first, then newest;
        with only common words, rows holding all of them. At most
        _LIKE_MAX_TERMS words. score is minus the words found (lower is better,
        as with bm25)."""
        terms = (content or words)[:_LIKE_MAX_TERMS]
        test = "content LIKE ? ESCAPE '\\'"
        patterns = ["%" + _like_escape(t) + "%" for t in terms]
        hits = " + ".join(f"({test})" for _ in terms)
        where = (" OR " if content else " AND ").join(test for _ in terms)
        sql = (f"SELECT id, session_id, timestamp, role, content, {hits} AS hits "
               f"FROM conversations WHERE ({where})")
        params = patterns + patterns
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        left_out, left_out_params = _prompt_clause(for_prompt)
        sql += left_out
        params.extend(left_out_params)
        sql += " ORDER BY hits DESC, id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [
            {"id": r[0], "session_id": r[1], "timestamp": r[2],
             "role": r[3], "content": r[4], "score": -float(r[5])}
            for r in rows
        ]

    def _fts_query(self, conn, query: str, limit: int, user_id: str = None,
                   for_prompt: bool = False) -> list[dict]:
        left_out, left_out_params = _prompt_clause(for_prompt)
        if user_id is not None:
            rows = conn.execute("""
                SELECT src_id, session_id, timestamp, role, content,
                       bm25(conversations_fts) AS score
                FROM conversations_fts
                WHERE conversations_fts MATCH ? AND user_id = ?""" + left_out + """
                ORDER BY score
                LIMIT ?
            """, (query, user_id, *left_out_params, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT src_id, session_id, timestamp, role, content,
                       bm25(conversations_fts) AS score
                FROM conversations_fts
                WHERE conversations_fts MATCH ?""" + left_out + """
                ORDER BY score
                LIMIT ?
            """, (query, *left_out_params, limit)).fetchall()
        return [
            {"id": r[0], "session_id": r[1], "timestamp": r[2],
             "role": r[3], "content": r[4], "score": round(r[5], 4)}
            for r in rows
        ]

    def search_recent(self, days: int = 7, limit: int = 50, user_id: str = None,
                      for_prompt: bool = False) -> list[dict]:
        """Return recent conversations from the past N days.
        If user_id is provided, only return results for that user.
        `for_prompt` leaves out agent and untrusted rows and crew sessions (P-037)."""
        since = (datetime.now() - timedelta(days=days)).isoformat()
        left_out, left_out_params = _prompt_clause(for_prompt)
        with self._lock:
            conn = self._get_conn()
            if user_id is not None:
                rows = conn.execute("""
                    SELECT id, session_id, timestamp, role, content
                    FROM conversations
                    WHERE timestamp >= ? AND user_id = ?""" + left_out + """
                    ORDER BY id DESC
                    LIMIT ?
                """, (since, user_id, *left_out_params, limit)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT id, session_id, timestamp, role, content
                    FROM conversations
                    WHERE timestamp >= ?""" + left_out + """
                    ORDER BY id DESC
                    LIMIT ?
                """, (since, *left_out_params, limit)).fetchall()
            return [
                {"id": r[0], "session_id": r[1], "timestamp": r[2],
                 "role": r[3], "content": r[4]}
                for r in rows
            ]

    def get_context(self, query: str, n: int = 5, user_id: str = None,
                    for_prompt: bool = True) -> str:
        """Return a formatted string of top-N matching snippets for LLM injection.
        Every caller builds a prompt, so agent and untrusted rows and crew sessions are left
        out by default (P-037)."""
        hits = self.search(query, limit=n, user_id=user_id, for_prompt=for_prompt)
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
        with self._lock:
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
        with self._lock:
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
            self.close()  # RLock is reentrant, so this nested acquire is safe
            tmp = sqlite3.connect(self.db_path)
            tmp.execute("VACUUM")
            tmp.close()
            size = os.path.getsize(self.db_path)
            return {"deleted": deleted, "remaining": after, "size_bytes": size}

    def rebuild_fts(self) -> int:
        """Full FTS rebuild — use after bulk imports. Returns row count."""
        with self._lock:
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
