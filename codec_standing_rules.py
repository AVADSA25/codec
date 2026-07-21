"""Standing rules — user instructions that actually persist.

Why this exists: a user pasted a rules document into chat and CODEC replied "I
have ingested the 10-point instruction set, I am now operating under this
framework for all future interactions." It had not, and it could not — there was
no mechanism by which a chat message became a standing instruction. The model
bluffed because the honest answer ("I can't do that") was one nobody had built
an alternative to.

codec_claim_check now catches that bluff. This module removes the reason for it:
rules written here are appended to the chat system prompt on every turn, so
"saved" becomes a statement CODEC can back with an artifact.

Storage: ~/.codec/standing_rules.json — deliberately its own file, NOT
prompt_overrides.json, whose `chat` key REPLACES the entire system prompt.
Standing rules are additive; a user adding "always answer in French" must not
silently discard CODEC's identity and safety framing.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import codec_jsonstore

log = logging.getLogger("codec")

RULES_PATH = Path(os.path.expanduser("~/.codec/standing_rules.json"))

MAX_RULES = 25          # a prompt suffix, not a document store
MAX_RULE_CHARS = 500    # one rule, not a pasted essay
SCHEMA = 1


def _empty() -> Dict:
    return {"schema": SCHEMA, "rules": []}


def load() -> Dict:
    try:
        with open(RULES_PATH) as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
            return _empty()
        return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return _empty()


def _save(data: Dict) -> None:
    RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with codec_jsonstore.file_lock(RULES_PATH):
        codec_jsonstore.atomic_write_json(RULES_PATH, data)
    try:
        os.chmod(RULES_PATH, 0o600)
    except OSError:
        pass


def list_rules() -> List[Dict]:
    return load().get("rules", [])


def add_rule(text: str) -> Dict:
    """Add one rule. Returns {ok, message, rule?}. Never raises."""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "message": "A rule needs some text."}
    if len(text) > MAX_RULE_CHARS:
        return {"ok": False, "message":
                f"That's {len(text)} characters — a standing rule has to fit in "
                f"{MAX_RULE_CHARS}. It rides on every message, so keep it to one "
                f"instruction. Split it up and add them one at a time."}
    data = load()
    rules = data.setdefault("rules", [])
    if any(r.get("text", "").strip().lower() == text.lower() for r in rules):
        return {"ok": False, "message": "You already have that rule."}
    if len(rules) >= MAX_RULES:
        return {"ok": False, "message":
                f"You're at the {MAX_RULES}-rule limit. Remove one first — rules "
                f"you never invoke are noise you pay for on every message."}
    rule = {"id": f"r{len(rules) + 1}_{int(datetime.now(timezone.utc).timestamp())}",
            "text": text,
            "added": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    rules.append(rule)
    _save(data)
    return {"ok": True, "rule": rule,
            "message": f"Saved as standing rule {len(rules)}."}


def remove_rule(which: str) -> Dict:
    """Remove by 1-based index or by id. Never raises."""
    data = load()
    rules = data.get("rules", [])
    if not rules:
        return {"ok": False, "message": "There are no standing rules to remove."}
    target = None
    w = (which or "").strip()
    if w.isdigit():
        i = int(w)
        if 1 <= i <= len(rules):
            target = rules[i - 1]
    if target is None:
        target = next((r for r in rules if r.get("id") == w), None)
    if target is None:
        return {"ok": False, "message":
                f"No rule matches '{which}'. Ask to see the rules to get their numbers."}
    rules.remove(target)
    _save(data)
    return {"ok": True, "message": f"Removed: {target.get('text', '')[:80]}"}


def clear_rules() -> Dict:
    data = load()
    n = len(data.get("rules", []))
    _save(_empty())
    return {"ok": True, "message": f"Cleared {n} standing rule(s)."}


def prompt_block() -> str:
    """The block appended to the chat system prompt. "" when there are none."""
    rules = list_rules()
    if not rules:
        return ""
    lines = ["STANDING RULES — the user set these; they apply to every reply:"]
    for i, r in enumerate(rules, 1):
        lines.append(f"{i}. {r.get('text', '')}")
    return "\n".join(lines)
