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
    """Rules with telemetry fields guaranteed present (older stores are
    backfilled in-memory; the on-disk backfill happens on the next write)."""
    data = load()
    _migrate(data)
    return data.get("rules", [])


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
            "added": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            # Telemetry — see prompt_block() for exactly what this counts.
            "inject_count": 0,
            "last_injected": None}
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


def _migrate(data: Dict) -> bool:
    """Backfill telemetry fields on rules written before they existed.

    Additive only — an existing rule keeps its id, text and added date, and
    starts at inject_count 0 / last_injected None. Never resets the store.
    Returns True when something changed (so the caller can persist).
    """
    changed = False
    for r in data.get("rules", []):
        if "inject_count" not in r:
            r["inject_count"] = 0
            changed = True
        if "last_injected" not in r:
            r["last_injected"] = None
            changed = True
    return changed


def prompt_block(record: bool = False) -> str:
    """The block appended to the chat system prompt. "" when there are none.

    `record=True` counts this as an INJECTION — see the honesty note below.
    Only the live chat path passes it; listing, tests and previews must not,
    or the count would measure itself.

    WHAT inject_count MEANS, AND DOES NOT
    -------------------------------------
    It counts how many times a rule was placed into the system prompt. That is
    the ONLY thing this pipeline can observe. A standing rule's entire lifecycle
    is: prompt_block() -> string -> appended to sys_prompt -> sent to the LLM.
    Nothing downstream reports whether the model consulted the rule, obeyed it,
    or ignored it.

    So this is deliberately NOT called fired_count. Measuring influence would
    need ablation (run the turn with and without the rule and diff — doubles
    cost, and non-determinism makes a single diff weak evidence) or asking the
    model which rules it used — which is exactly the unverifiable self-report
    this codebase exists to refuse. A field named fired_count would be a
    fabricated metric wearing an authoritative name.

    Practical consequence: inject_count == 0 proves a rule is dead weight
    (it never even reached the model). A high inject_count proves nothing about
    usefulness — only that the rule is being paid for on every message.
    """
    if not record:
        rules = list_rules()
    else:
        # Read-modify-write under the same lock the writers use, so a concurrent
        # add/remove can't lose the increment.
        data = load()
        _migrate(data)
        rules = data.get("rules", [])
        if rules:
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for r in rules:
                r["inject_count"] = int(r.get("inject_count") or 0) + 1
                r["last_injected"] = now
            try:
                _save(data)
            except Exception as e:      # never break a reply over telemetry
                log.warning("standing rules: inject-count write failed: %s", e)

    if not rules:
        return ""
    lines = ["STANDING RULES — the user set these; they apply to every reply:"]
    for i, r in enumerate(rules, 1):
        lines.append(f"{i}. {r.get('text', '')}")
    return "\n".join(lines)
