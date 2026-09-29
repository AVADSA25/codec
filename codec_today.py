"""Today cards (UI phase 3, P3.3; docs/P3.3-DESIGN.md).

A small store of cards that Home shows above Flash: a scheduled job's result
delivered to "Today" now, the Morning briefing (P3.1) and the Today home (P3.4)
later. ``~/.codec/today.json`` (0600, atomic write, cross-process lock) keeps
the newest MAX_CARDS; dismissed cards stay until they age out so a dismiss is
not undone by a concurrent writer.
"""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import codec_jsonstore

TODAY_PATH = Path(os.path.expanduser("~/.codec/today.json"))
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
             url: Optional[str] = None) -> Dict[str, Any]:
    card = {"id": "card_" + secrets.token_hex(5), "kind": kind, "title": str(title)[:120],
            "body": str(body or "")[:MAX_BODY], "source": source, "url": url,
            "created": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "dismissed": False}
    with codec_jsonstore.file_lock(TODAY_PATH):
        cards = [card] + _read()
        codec_jsonstore.atomic_write_json(TODAY_PATH, {"cards": cards[:MAX_CARDS]})
    return card


def list_cards(include_dismissed: bool = False) -> List[Dict[str, Any]]:
    return [c for c in _read() if include_dismissed or not c.get("dismissed")]


def dismiss(card_id: str) -> bool:
    with codec_jsonstore.file_lock(TODAY_PATH):
        cards = _read()
        hit = False
        for c in cards:
            if c.get("id") == card_id and not c.get("dismissed"):
                c["dismissed"] = True
                hit = True
        if hit:
            codec_jsonstore.atomic_write_json(TODAY_PATH, {"cards": cards})
    return hit
