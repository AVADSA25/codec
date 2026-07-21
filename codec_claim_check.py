"""Claim-to-artifact matching — CODEC may not claim what it did not do.

The incident that motivated this (2026-07-21): a user pasted a standing-rules
document into chat. CODEC replied:

    "Confirmed. I have ingested the 10-point instruction set.
     I am now operating under this framework for all future interactions."

None of that happened. There is no mechanism by which a chat message becomes a
standing rule — no prompt_overrides.json was written, no fact stored, nothing.
The model invented a *capability*, which is worse than inventing a fact: a fact
can be checked against the world, but a claim about the assistant's own internals
sounds authoritative and has nothing to check it against.

This module supplies the missing check. CODEC already records every real action
it takes; a claim of action that has no corresponding action is false **by
construction**, not by opinion. That is a structural guarantee no amount of
prompting can provide.

Two claim families:

1. IMPOSSIBLE — the capability does not exist at all ("I will remember this for
   future sessions"). Always unbacked, regardless of what ran.
2. NEEDS-ACTION — the capability exists but requires a specific action ("saved
   to your Desktop" requires a file-writing skill to have actually run).

Design bias: FALSE NEGATIVES OVER FALSE POSITIVES. A wrongly-flagged honest
sentence trains the user to ignore the warning, which destroys the mechanism.
Patterns are therefore narrow and first-person-past/future only — "I saved the
file", never a bare mention of the word "saved".
"""
from __future__ import annotations

import re
from typing import Iterable, List, NamedTuple, Optional, Set


class Claim(NamedTuple):
    kind: str            # "impossible" | "needs_action"
    quote: str           # the sentence fragment that made the claim
    needs: Optional[str] = None   # human label of the action that would back it


# ── 1. Capabilities CODEC simply does not have ────────────────────────────────
# A chat message never becomes a standing instruction, and the model has no
# cross-session memory of its own. Claims here are false however the turn went.
_IMPOSSIBLE = [
    (re.compile(
        r"\bI(?:'ve| have| am now| will)?\s*(?:now\s+)?"
        r"(?:ingest(?:ed)?|internali[sz]ed|absorbed|adopted|loaded)\b[^.]{0,60}"
        r"\b(?:instruction|rule|framework|guideline|directive|protocol)s?\b",
        re.I),
     "standing instructions"),
    (re.compile(
        r"\b(?:I(?:'m| am)? (?:now )?operating under|I(?:'ll| will) (?:now )?"
        r"(?:apply|follow|use|operate under))\b[^.]{0,60}"
        r"\b(?:for all|in all|going forward|future|from now on|henceforth)\b",
        re.I),
     "standing instructions"),
    (re.compile(
        r"\bI(?:'ll| will)\s+remember\b[^.]{0,50}"
        r"\b(?:for (?:all )?future|next time|going forward|in future|from now on)\b",
        re.I),
     "cross-session memory"),
    (re.compile(
        r"\b(?:committed|saved|stored)\s+(?:this\s+)?to\s+(?:my\s+)?"
        r"(?:long[- ]term\s+)?memory\b", re.I),
     "cross-session memory"),
]

# ── 2. Real capabilities that require a real action ───────────────────────────
# (pattern, human label, skills that would legitimately back the claim)
_NEEDS_ACTION = [
    (re.compile(r"\bI(?:'ve| have)?\s*(?:just\s+)?"
                r"(?:saved|written|wrote|created|exported)\b[^.]{0,40}"
                r"\b(?:to|in|at)\b[^.]{0,40}"
                r"(?:file|desktop|documents|folder|\.md\b|\.txt\b|\.csv\b|vault)",
                re.I),
     "writing a file",
     {"file_write", "file_ops", "google_docs", "obsidian", "vault"}),
    (re.compile(r"\bI(?:'ve| have)?\s*(?:just\s+)?sent\b[^.]{0,30}"
                r"\b(?:email|message|imessage|telegram)\b", re.I),
     "sending a message",
     {"google_gmail", "imessage_send", "telegram", "email_triage"}),
    (re.compile(r"\bI(?:'ve| have)?\s*(?:just\s+)?"
                r"(?:added|created|scheduled)\b[^.]{0,30}"
                r"\b(?:calendar|event|reminder|task)\b", re.I),
     "creating a calendar item",
     {"google_calendar", "google_tasks", "reminders", "scheduler"}),
]


def _sentences(text: str) -> Iterable[str]:
    for part in re.split(r"(?<=[.!?\n])\s+", text or ""):
        part = part.strip()
        if part:
            yield part


def find_unbacked_claims(reply: str, actions_taken: Optional[Set[str]] = None) -> List[Claim]:
    """Claims in `reply` that nothing in this turn actually backs.

    `actions_taken` is the set of skill names that ran during the turn. An empty
    set means the turn was pure text generation, so every action claim is
    unbacked.
    """
    if not reply:
        return []
    done = {a.lower() for a in (actions_taken or set())}
    found: List[Claim] = []

    for sentence in _sentences(reply):
        for pattern, label in _IMPOSSIBLE:
            if pattern.search(sentence):
                found.append(Claim("impossible", sentence[:160], label))
                break
        else:
            for pattern, label, backers in _NEEDS_ACTION:
                if pattern.search(sentence) and not (done & backers):
                    found.append(Claim("needs_action", sentence[:160], label))
                    break
    return found


def correction_note(claims: List[Claim]) -> str:
    """The note appended to a reply that made unbacked claims. Empty if none."""
    if not claims:
        return ""
    impossible = [c for c in claims if c.kind == "impossible"]
    needs = [c for c in claims if c.kind == "needs_action"]
    lines = ["", "---", "**⚠ Correction — I claimed something I did not do.**"]
    if impossible:
        labels = sorted({c.needs for c in impossible if c.needs})
        lines.append(
            f"I cannot persist {' or '.join(labels)}. A chat message does not "
            f"become a standing rule, and I start each session with no memory of "
            f"the last one. Nothing was saved."
        )
    if needs:
        labels = sorted({c.needs for c in needs if c.needs})
        lines.append(
            f"No action was recorded this turn for: {', '.join(labels)}. "
            f"Treat it as not done — ask me to actually run it."
        )
    return "\n".join(lines)
