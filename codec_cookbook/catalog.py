"""Cookbook model catalog — verified, downloadable options.

Loaded from `catalog.json` (next to this file). The primary `qwen3.6@8083`
model that the live stack serves is intentionally NOT in here — Cookbook never
manages it.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Optional

_CATALOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalog.json")


@lru_cache(maxsize=1)
def _load() -> list[dict]:
    with open(_CATALOG_PATH, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("catalog.json must be a JSON array")
    return data


def all_entries() -> list[dict]:
    """Return a copy of every catalog entry."""
    return [dict(e) for e in _load()]


def ids() -> list[str]:
    """Return the list of known model ids."""
    return [e["id"] for e in _load()]


def get(model_id: Optional[str]) -> dict:
    """Return the catalog entry for `model_id`. Raises KeyError if unknown so
    callers fail loud rather than serving a mystery repo."""
    if not model_id:
        raise KeyError("no model_id given")
    for e in _load():
        if e["id"] == model_id:
            return dict(e)
    raise KeyError(f"unknown model id: {model_id!r} (known: {', '.join(ids())})")


def find(model_id: Optional[str]) -> Optional[dict]:
    """Like get() but returns None instead of raising — for arg-parsing paths."""
    try:
        return get(model_id)
    except KeyError:
        return None


def by_role(role: str) -> list[dict]:
    """All entries advertising a given role (chat/reason/code/max/fast/tiny)."""
    return [dict(e) for e in _load() if role in e.get("roles", [])]
