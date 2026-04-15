"""auto_memorize — scan yesterday's conversations and extract durable facts.

Pulls the last 24h of conversation turns from CODEC memory, chunks them by
session, runs fact_extract on each chunk, and saves the extracted facts back
to memory tagged as auto-extracted.

Designed to run once per day via autopilot. Idempotent — keeps a marker file
~/.codec/auto_memorize_last.json so it never re-processes the same window.
"""
SKILL_NAME = "auto_memorize"
SKILL_DESCRIPTION = "Scan the last 24 hours of CODEC conversations and auto-extract durable facts to long-term memory; designed for nightly autopilot runs."
SKILL_MCP_EXPOSE = True

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

_MARKER = Path(os.path.expanduser("~/.codec/auto_memorize_last.json"))

# Minimum text length to bother extracting from
MIN_CHUNK = 200
# Max chars per chunk sent to LLM
MAX_CHUNK = 6000


def _load_marker() -> str | None:
    if _MARKER.exists():
        try:
            return json.loads(_MARKER.read_text()).get("last_run")
        except Exception:
            return None
    return None


def _save_marker(ts: str):
    _MARKER.parent.mkdir(parents=True, exist_ok=True)
    _MARKER.write_text(json.dumps({"last_run": ts}))


def _pull_conversations() -> list[dict]:
    """Return list of conversation turns from the last 24h."""
    from codec_memory import CodecMemory
    mem = CodecMemory()
    turns = mem.search_recent(days=1, limit=500) or []
    # Only keep user+assistant turns that look like real conversation
    return [
        t for t in turns
        if t.get("role") in ("user", "assistant") and len(t.get("content", "")) > 5
    ]


def _chunk_by_session(turns: list[dict]) -> list[str]:
    """Group turns by session_id, join into a single text blob per session."""
    from collections import defaultdict
    by_sess = defaultdict(list)
    for t in turns:
        sid = t.get("session_id", "default")
        role = t.get("role", "user")
        content = (t.get("content") or "").strip()
        by_sess[sid].append(f"[{role}] {content}")
    chunks = []
    for sid, lines in by_sess.items():
        blob = "\n".join(lines)
        if len(blob) < MIN_CHUNK:
            continue
        # Slice long blobs into MAX_CHUNK-sized pieces
        for i in range(0, len(blob), MAX_CHUNK):
            chunks.append(blob[i:i + MAX_CHUNK])
    return chunks


def run(task: str = "", context: str = "") -> str:
    now = datetime.now(timezone.utc)

    # Dedup: if run within last 20h, skip unless task includes "force"
    last = _load_marker()
    if last and "force" not in (task or "").lower():
        try:
            last_dt = datetime.fromisoformat(last)
            if (now - last_dt) < timedelta(hours=20):
                return f"Already ran at {last}. Pass 'force' to override."
        except Exception:
            pass

    turns = _pull_conversations()
    if not turns:
        _save_marker(now.isoformat())
        return "No conversations in the last 24h."

    chunks = _chunk_by_session(turns)
    if not chunks:
        _save_marker(now.isoformat())
        return f"Pulled {len(turns)} turns but no chunk met the {MIN_CHUNK}-char threshold."

    # Defer to fact_extract for the heavy lifting
    from fact_extract import run as extract
    total_saved = 0
    per_chunk = []
    for i, blob in enumerate(chunks, 1):
        res = extract(blob, "")
        per_chunk.append(res.splitlines()[0] if res else "")
        # Parse "Saved N/M facts" line
        import re
        m = re.search(r"Saved\s+(\d+)", res or "")
        if m:
            total_saved += int(m.group(1))

    _save_marker(now.isoformat())
    return (f"auto_memorize: processed {len(chunks)} chunk(s) from "
            f"{len(turns)} turn(s) → {total_saved} fact(s) saved.\n"
            + "\n".join(f"  • {line}" for line in per_chunk[:10]))
