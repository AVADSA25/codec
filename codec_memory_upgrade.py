"""CODEC Memory Upgrade — tiered boot, temporal facts, CCF compression.

Three layers shipped together:

  L0/L1  identity.txt     →  always-loaded boot payload (<200 tok)
  L2     recent rooms     →  last N sessions from conversations
  L3     deep FTS search  →  on-demand query over full history

  facts  table            →  temporal key/value store with
                             valid_from / valid_until / superseded_by

  CCF    rule-based       →  entity abbreviation + filler stripping
                             for memory writes that need shrinking
"""
from __future__ import annotations
import json, os, re, sqlite3, logging
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger("codec_memory_upgrade")

from codec_config import DB_PATH

MEMORY_DIR      = os.path.expanduser("~/.codec/memory")
IDENTITY_PATH   = os.path.join(MEMORY_DIR, "identity.txt")
ENTITY_MAP_PATH = os.path.join(MEMORY_DIR, "entity_map.json")

os.makedirs(MEMORY_DIR, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 — Tiered Boot Loader
# ─────────────────────────────────────────────────────────────────────────────

def load_identity() -> str:
    """Return L0+L1 identity.txt contents, empty string if missing."""
    try:
        with open(IDENTITY_PATH) as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def l2_room_recall(days: int = 7, limit: int = 10) -> list[dict]:
    """Last N distinct sessions with previews."""
    from codec_memory import CodecMemory
    return CodecMemory().get_sessions(limit=limit)


def l3_deep_search(query: str, limit: int = 5) -> list[dict]:
    """FTS5 search over full history."""
    from codec_memory import CodecMemory
    return CodecMemory().search(query, limit=limit)


def get_boot_context(include_rooms: bool = True) -> str:
    """Compose the full boot payload: identity + optional recent rooms preview."""
    parts = [load_identity()]
    if include_rooms:
        rooms = l2_room_recall(limit=5)
        if rooms:
            parts.append("\n## L2 — Recent sessions")
            for r in rooms:
                ts = (r.get("last_msg") or "")[:16].replace("T", " ")
                parts.append(f"- [{ts}] {r.get('preview','')[:80]}")
    return "\n".join(p for p in parts if p).strip()


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — Temporal Fact Tracking (separate `facts` table, non-destructive)
# ─────────────────────────────────────────────────────────────────────────────

_FACTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    fact_type TEXT DEFAULT 'generic',
    confidence REAL DEFAULT 1.0,
    valid_from TEXT NOT NULL,
    valid_until TEXT,
    superseded_by INTEGER,
    user_id TEXT DEFAULT 'default',
    source TEXT
);
CREATE INDEX IF NOT EXISTS idx_facts_key ON facts(key);
CREATE INDEX IF NOT EXISTS idx_facts_valid ON facts(valid_until);
CREATE INDEX IF NOT EXISTS idx_facts_user ON facts(user_id);
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(_FACTS_SCHEMA)
    return c


def store_fact(key: str, value: str, fact_type: str = "generic",
               confidence: float = 1.0, user_id: str = "default",
               source: str = "", supersede: bool = True) -> int:
    """Store a fact. If supersede=True and an active fact with same key exists,
    mark it valid_until=now and link superseded_by → new row."""
    now = datetime.now().isoformat()
    c = _conn()
    try:
        new_id = c.execute(
            "INSERT INTO facts (key,value,fact_type,confidence,valid_from,user_id,source) "
            "VALUES (?,?,?,?,?,?,?)",
            (key, value, fact_type, confidence, now, user_id, source),
        ).lastrowid
        if supersede:
            c.execute(
                "UPDATE facts SET valid_until=?, superseded_by=? "
                "WHERE key=? AND user_id=? AND valid_until IS NULL AND id!=?",
                (now, new_id, key, user_id, new_id),
            )
        c.commit()
        return new_id
    finally:
        c.close()


def query_valid_facts(key: Optional[str] = None, user_id: str = "default",
                      limit: int = 50) -> list[dict]:
    """Facts currently active (valid_until IS NULL)."""
    c = _conn()
    try:
        if key:
            rows = c.execute(
                "SELECT id,key,value,fact_type,confidence,valid_from,source "
                "FROM facts WHERE key=? AND user_id=? AND valid_until IS NULL "
                "ORDER BY id DESC LIMIT ?",
                (key, user_id, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT id,key,value,fact_type,confidence,valid_from,source "
                "FROM facts WHERE user_id=? AND valid_until IS NULL "
                "ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        cols = ["id", "key", "value", "fact_type", "confidence", "valid_from", "source"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        c.close()


def get_fact_history(key: str, user_id: str = "default") -> list[dict]:
    """Full timeline for a key — all versions, newest first."""
    c = _conn()
    try:
        rows = c.execute(
            "SELECT id,value,valid_from,valid_until,superseded_by,confidence,source "
            "FROM facts WHERE key=? AND user_id=? ORDER BY id DESC",
            (key, user_id),
        ).fetchall()
        cols = ["id", "value", "valid_from", "valid_until", "superseded_by", "confidence", "source"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        c.close()


def find_contradictions(user_id: str = "default") -> list[dict]:
    """Keys with >1 version still marked valid (shouldn't happen, audit tool)."""
    c = _conn()
    try:
        rows = c.execute(
            "SELECT key, COUNT(*) as n FROM facts "
            "WHERE user_id=? AND valid_until IS NULL GROUP BY key HAVING n>1",
            (user_id,),
        ).fetchall()
        return [{"key": r[0], "active_versions": r[1]} for r in rows]
    finally:
        c.close()


def cleanup_expired(older_than_days: int = 365, user_id: str = "default") -> int:
    """Delete superseded facts older than N days."""
    cutoff = (datetime.now() - timedelta(days=older_than_days)).isoformat()
    c = _conn()
    try:
        cur = c.execute(
            "DELETE FROM facts WHERE user_id=? AND valid_until IS NOT NULL AND valid_until<?",
            (user_id, cutoff),
        )
        c.commit()
        return cur.rowcount
    finally:
        c.close()


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — CCF (CODEC Compressed Format) rule-based compressor
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_ENTITY_MAP = {
    "Mickael Farina": "MF",
    "Mickael": "MF",
    "AVA Digital": "AVA",
    "AVA Digital LLC": "AVA",
    "Claude Desktop": "CD",
    "Claude Code": "CC",
    "Claude Cursor": "CCur",
    "Marbella": "MRB",
    "Spain": "ES",
    "Mac Studio": "MS",
    "localhost:8081": "L81",
    "localhost:8082": "L82",
    "localhost:8083": "L83",
    "localhost:8084": "L84",
    "localhost:8085": "L85",
}

FILLER_WORDS = {
    "basically", "actually", "literally", "honestly", "sort of", "kind of",
    "you know", "i mean", "like,", "um ", "uh ", "er ",
}


def _load_entity_map() -> dict:
    if os.path.exists(ENTITY_MAP_PATH):
        try:
            with open(ENTITY_MAP_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return DEFAULT_ENTITY_MAP.copy()


def _save_entity_map(m: dict) -> None:
    with open(ENTITY_MAP_PATH, "w") as f:
        json.dump(m, f, indent=2, sort_keys=True)


def compress_rule_based(text: str, entity_map: Optional[dict] = None) -> str:
    """Apply entity substitutions + filler removal. Preserves FTS-friendly tokens."""
    if not text:
        return text
    emap = entity_map or _load_entity_map()
    out = text
    # Sort by length desc so longer phrases match first (Mickael Farina before Mickael)
    for full, abbr in sorted(emap.items(), key=lambda kv: -len(kv[0])):
        out = re.sub(r'\b' + re.escape(full) + r'\b', abbr, out, flags=re.IGNORECASE)
    for f in FILLER_WORDS:
        out = re.sub(r'\b' + re.escape(f) + r'\b', '', out, flags=re.IGNORECASE)
    out = re.sub(r'\s+', ' ', out).strip()
    return out


def decompress_for_display(text: str, entity_map: Optional[dict] = None) -> str:
    """Expand abbreviations back for human readability."""
    if not text:
        return text
    emap = entity_map or _load_entity_map()
    out = text
    for full, abbr in sorted(emap.items(), key=lambda kv: -len(kv[1])):
        out = re.sub(r'\b' + re.escape(abbr) + r'\b', full, out)
    return out


def add_entity(full: str, abbr: str) -> dict:
    m = _load_entity_map()
    m[full] = abbr
    _save_entity_map(m)
    return m


def remove_entity(full: str) -> dict:
    m = _load_entity_map()
    m.pop(full, None)
    _save_entity_map(m)
    return m


def list_entities() -> dict:
    return _load_entity_map()


# Seed default entity map file on first import
if not os.path.exists(ENTITY_MAP_PATH):
    _save_entity_map(DEFAULT_ENTITY_MAP)
