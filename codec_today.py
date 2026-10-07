"""Today cards (UI phase 3, P3.3; docs/P3.3-DESIGN.md).

A small store of cards that Home shows above Flash: a scheduled job's result
delivered to "Today", the Morning briefing (P3.1, with its open threads and a
snooze), and the Today home (P3.4) later. ``~/.codec/today.json`` (0600, atomic write, cross-process lock) keeps
the newest MAX_CARDS; dismissed cards stay until they age out so a dismiss is
not undone by a concurrent writer.
"""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import codec_jsonstore

TODAY_PATH = Path(os.path.expanduser("~/.codec/today.json"))
# P3.4: open threads snoozed from Today, {thread key: until}; memory.db is never touched.
THREAD_SNOOZE_PATH = Path(os.path.expanduser("~/.codec/thread_snooze.json"))
MAX_CARDS = 30
MAX_BODY = 4000


def _read() -> List[Dict[str, Any]]:
    try:
        data = json.loads(TODAY_PATH.read_text())
    except (FileNotFoundError, ValueError, OSError):
        return []
    cards = data.get("cards") if isinstance(data, dict) else None
    return [c for c in (cards or []) if isinstance(c, dict) and c.get("id")]


def add_card(title: str, body: str, *, kind: str = "schedule", source: Optional[str] = None,
             url: Optional[str] = None, threads: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    card = {"id": "card_" + secrets.token_hex(5), "kind": kind, "title": str(title)[:120],
            "body": str(body or "")[:MAX_BODY], "source": source, "url": url,
            "created": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "dismissed": False,
            "threads": [{"key": str(t.get("key")), "kind": str(t.get("kind") or ""), "text": str(t.get("text") or "")[:300]}
                        for t in (threads or []) if isinstance(t, dict) and t.get("key")][:20],
            "snoozed_until": None}
    with codec_jsonstore.file_lock(TODAY_PATH):
        cards = [card] + _read()
        codec_jsonstore.atomic_write_json(TODAY_PATH, {"cards": cards[:MAX_CARDS]})
    return card


def _awake(card: Dict[str, Any], now: datetime) -> bool:
    until = card.get("snoozed_until")
    try:
        return not until or datetime.fromisoformat(until) <= now
    except ValueError:
        return True


def list_cards(include_dismissed: bool = False, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Cards to show: not dismissed and not snoozed (unless include_dismissed)."""
    now = now or datetime.now()
    if include_dismissed:
        return _read()
    return [c for c in _read() if not c.get("dismissed") and _awake(c, now)]


def get_card(card_id: str) -> Optional[Dict[str, Any]]:
    return next((c for c in _read() if c.get("id") == card_id), None)


def _change(card_id: str, mutate) -> bool:
    with codec_jsonstore.file_lock(TODAY_PATH):
        cards = _read()
        hit = False
        for c in cards:
            if c.get("id") == card_id and not c.get("dismissed"):
                hit = mutate(c) is not False
        if hit:
            codec_jsonstore.atomic_write_json(TODAY_PATH, {"cards": cards})
    return hit


def dismiss(card_id: str) -> bool:
    return _change(card_id, lambda c: c.update(dismissed=True))


def snooze(card_id: str, minutes: int) -> bool:
    minutes = max(5, min(int(minutes), 24 * 60))
    until = (datetime.now() + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S")
    return _change(card_id, lambda c: c.update(snoozed_until=until))


def drop_thread(card_id: str, key: str) -> bool:
    """Remove a thread from a card once it is closed."""
    def mutate(c):
        before = len(c.get("threads") or [])
        c["threads"] = [t for t in (c.get("threads") or []) if t.get("key") != key]
        return len(c["threads"]) < before
    return _change(card_id, mutate)


# ── Thread snoozes (P3.4, docs/P3.4-DESIGN.md) ────────────────────────────────
def _later(iso: str, now: datetime) -> bool:
    try:
        return datetime.fromisoformat(str(iso)) > now
    except ValueError:
        return False


def _read_snoozes() -> Dict[str, str]:
    try:
        data = json.loads(THREAD_SNOOZE_PATH.read_text())
    except (FileNotFoundError, ValueError, OSError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def snooze_thread(key: str, hours: int) -> str:
    """Hide an open thread from Today for `hours` (1 to 168). Returns the time it comes back."""
    hours = max(1, min(int(hours), 168))
    now = datetime.now()
    until = (now + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
    with codec_jsonstore.file_lock(THREAD_SNOOZE_PATH):
        data = {k: v for k, v in _read_snoozes().items() if _later(v, now)}  # expired ones drop out
        data[str(key)] = until
        codec_jsonstore.atomic_write_json(THREAD_SNOOZE_PATH, data)
    return until


def snoozed_threads(now: Optional[datetime] = None) -> set:
    now = now or datetime.now()
    return {k for k, v in _read_snoozes().items() if _later(v, now)}
