"""Tiny argument parser shared by the thin cookbook_* skills.

Skills receive a single `task` string (CODEC's `run(task, app, ctx)` contract),
so structured options are parsed out of it here. `re` only — keeps the skill
files AST-safe (no os/subprocess) and DRY.
"""
from __future__ import annotations

import re
from typing import Optional

from . import catalog


def parse_model_id(task: str) -> Optional[str]:
    """First known catalog id that appears as a whole token in `task`."""
    if not task:
        return None
    tokens = re.findall(r"[A-Za-z0-9_.\-]+", task.lower())
    known = set(catalog.ids())
    for tok in tokens:
        if tok in known:
            return tok
    return None


def parse_context(task: str, default: int = 8192) -> int:
    """`context 8192`, `context=8192`, `ctx 4096`, `context_length: 16384`."""
    m = re.search(r"(?:context|ctx)(?:[ _]?length)?\s*[=:]?\s*(\d{3,7})", (task or "").lower())
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return default


def parse_flag(task: str, flag: str) -> bool:
    """True if `flag` appears as a whole word, or `flag=true`/`flag:yes`."""
    low = (task or "").lower()
    if re.search(rf"\b{re.escape(flag)}\b\s*[=:]\s*(?:true|yes|1|on)\b", low):
        return True
    if re.search(rf"\b{re.escape(flag)}=(?:true|yes|1|on)\b", low):
        return True
    return bool(re.search(rf"\b{re.escape(flag)}\b", low))


def parse_port(task: str) -> Optional[int]:
    """A bare port number in the Cookbook range (8110-8119) mentioned in `task`."""
    for m in re.findall(r"\b(\d{4,5})\b", task or ""):
        try:
            p = int(m)
        except ValueError:
            continue
        if 8110 <= p <= 8119:
            return p
    return None


def parse_role(task: str) -> Optional[str]:
    """A recommendation role mentioned in `task` (chat/reason/code/max/fast/tiny)."""
    low = (task or "").lower()
    for role in ("chat", "reason", "code", "max", "fast", "tiny"):
        if re.search(rf"\b{role}\b", low):
            return role
    return None
