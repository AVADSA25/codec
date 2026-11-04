"""Cookbook model catalog — verified, downloadable options.

Loaded from `catalog.json` (next to this file). The primary `qwen3.6@8083`
model that the live stack serves is intentionally NOT in here — Cookbook never
manages it. `qwen25-7b` is the model the app ships (packaging/macos/models.json
`qwen-7b-4bit`, same repo and revision), so Settings can rank it (P-039).

Each entry's `licence` is the `license` field of its model card, read on
2026-11-04; `kv` is copied from its config.json; `size_gb` is its weight files
on the Hub. docs/MODEL-FIT-OFFER-DESIGN.md
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Optional

_CATALOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalog.json")

# Model-weight licences CODEC may offer a buyer: each one allows commercial use.
# An entry whose licence is not a key here is never offered (offer.build).
# `notice` is text the licence asks to show wherever the model is offered.
LLAMA_32_NOTICE = ("Llama 3.2 is licensed under the Llama 3.2 Community License, "
                   "Copyright © Meta Platforms, Inc. All Rights Reserved.")
COMMERCIAL_LICENCES: dict[str, dict] = {
    "apache-2.0": {"name": "Apache 2.0", "notice": "", "policy_url": ""},
    "llama3.2": {"name": "Llama 3.2 Community License",
                 "notice": LLAMA_32_NOTICE + " Built with Llama.",
                 "policy_url": "https://www.llama.com/llama3_2/use-policy"},
}


def licence(entry: dict) -> Optional[dict]:
    """{id, name, notice, policy_url, card_url} for an allowlisted licence, else None."""
    lid = str(entry.get("licence") or "").strip().lower()
    known = COMMERCIAL_LICENCES.get(lid)
    if known is None:
        return None
    return {"id": lid, **known, "card_url": f"https://huggingface.co/{entry.get('hf_repo', '')}"}


def licence_ok(entry: dict) -> bool:
    """True when the entry's model licence allows CODEC to offer it."""
    return licence(entry) is not None


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
