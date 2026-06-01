"""CODEC Email Triage — read-only inbox classification + ranked digest.

v1 is deliberately READ-ONLY: it reads a recent inbox window via the user's
existing Gmail OAuth (the same `codec_google_auth.build_service` path
google_gmail uses), classifies each message, and returns a ranked digest.
It applies NO labels, creates NO drafts, and sends NOTHING — those are later,
consent-gated phases (outbound is bridge-only per the CODEC operating
principles).

Local-first: classification runs on the LOCAL Qwen (config llm_base_url) by
default, so email content never leaves the machine. The digest that comes back
is metadata only (sender / subject / category / priority / one-line reason).

This is the engine (Gmail API + LLM live here) so skills/email_triage.py stays
thin and passes the SkillRegistry AST gate.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

log = logging.getLogger("codec_email_triage")

_CONFIG_PATH = os.path.expanduser("~/.codec/config.json")
CATEGORIES = ("lead", "support", "personal", "transactional", "noise")
PRIORITIES = ("high", "medium", "low")
_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}
_CATEGORY_RANK = {c: i for i, c in enumerate(CATEGORIES)}
_MAX_MESSAGES = 25
_SNIPPET_CHARS = 200

_CLASSIFY_SYSTEM = (
    "You are an email triage classifier. For EACH email you are given, decide:\n"
    "  category: one of lead, support, personal, transactional, noise\n"
    "    (lead=sales/business opportunity/new inquiry; support=help/issue/question "
    "from an existing contact; personal=from a person you know, non-business; "
    "transactional=receipts/calendar/automated account notices; noise=newsletters/"
    "marketing/spam)\n"
    "  priority: one of high, medium, low (how much it needs a human reply soon)\n"
    "  reason: <= 12 words, why.\n"
    "Return ONLY a JSON array, one object per email, each "
    '{"idx": <int>, "category": "...", "priority": "...", "reason": "..."}. '
    "No prose, no code fences."
)


def _load_cfg() -> dict:
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _gmail_service():
    """Reuse the same authed Gmail service google_gmail uses. Raises if the
    user hasn't connected Google (caller turns that into a friendly message)."""
    import sys
    repo = os.path.dirname(os.path.abspath(__file__))
    if repo not in sys.path:
        sys.path.insert(0, repo)
    from codec_google_auth import build_service
    return build_service("gmail", "v1")


def fetch_recent(max_messages: int = _MAX_MESSAGES, query: str = "is:inbox",
                 service=None) -> list[dict]:
    """Recent inbox messages as {id, sender, subject, snippet, unread, date}.
    Read-only — list + get(metadata/snippet) only."""
    svc = service or _gmail_service()
    max_messages = max(1, min(max_messages, 50))
    listing = svc.users().messages().list(
        userId="me", q=query, maxResults=max_messages).execute()
    out = []
    for ref in listing.get("messages", []):
        try:
            msg = svc.users().messages().get(
                userId="me", id=ref["id"],
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"]).execute()
        except Exception as e:
            log.debug("message fetch failed (%s): %s", ref.get("id"), e)
            continue
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        sender = headers.get("From", "Unknown")
        if "<" in sender:
            sender = sender.split("<")[0].strip().strip('"') or sender
        out.append({
            "id": ref["id"],
            "sender": sender,
            "subject": headers.get("Subject", "(no subject)"),
            "snippet": (msg.get("snippet", "") or "")[:_SNIPPET_CHARS],
            "unread": "UNREAD" in msg.get("labelIds", []),
            "date": headers.get("Date", ""),
        })
    return out


def _parse_classification(raw: str, n: int) -> dict:
    """Parse the LLM's JSON array into {idx: {category, priority, reason}}.
    Tolerant: strips code fences; coerces unknown enums; returns {} on failure
    so the caller falls back to 'unclassified'."""
    if not raw:
        return {}
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)
    # grab the first JSON array if there's surrounding prose
    m = re.search(r"\[.*\]", s, re.DOTALL)
    if m:
        s = m.group(0)
    try:
        arr = json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return {}
    by_idx = {}
    for obj in arr if isinstance(arr, list) else []:
        try:
            idx = int(obj.get("idx"))
        except (TypeError, ValueError):
            continue
        if not (0 <= idx < n):
            continue
        cat = str(obj.get("category", "")).lower().strip()
        pri = str(obj.get("priority", "")).lower().strip()
        by_idx[idx] = {
            "category": cat if cat in CATEGORIES else "noise",
            "priority": pri if pri in PRIORITIES else "medium",
            "reason": str(obj.get("reason", ""))[:120],
        }
    return by_idx


def classify(messages: list[dict], *, base_url: Optional[str] = None,
             model: Optional[str] = None, timeout: int = 60) -> list[dict]:
    """Classify messages with the LOCAL LLM in ONE call. Returns each message
    enriched with category/priority/reason. On LLM failure, every message is
    'unclassified'/'medium' (read-only digest still works)."""
    if not messages:
        return []
    cfg = _load_cfg()
    base_url = base_url or cfg.get("llm_base_url", "http://localhost:8083/v1")
    model = model or cfg.get("llm_model", "local-qwen")
    listing = "\n".join(
        f'{i}) From: {m["sender"]} | Subject: {m["subject"]} | {m["snippet"]}'
        for i, m in enumerate(messages)
    )
    raw = ""
    try:
        import codec_llm
        raw = codec_llm.call(
            [{"role": "system", "content": _CLASSIFY_SYSTEM},
             {"role": "user", "content": listing}],
            base_url=base_url, model=model, max_tokens=2000,
            temperature=0.1, timeout=timeout)
    except Exception as e:
        log.warning("triage classify LLM call failed: %s", e)
    by_idx = _parse_classification(raw, len(messages))
    enriched = []
    for i, m in enumerate(messages):
        c = by_idx.get(i)
        enriched.append({
            **m,
            "category": c["category"] if c else "unclassified",
            "priority": c["priority"] if c else "medium",
            "reason": c["reason"] if c else "",
        })
    return enriched


def _rank_key(item: dict) -> tuple:
    return (_PRIORITY_RANK.get(item.get("priority"), 1),
            _CATEGORY_RANK.get(item.get("category"), len(CATEGORIES)),
            0 if item.get("unread") else 1)


def triage(max_messages: int = _MAX_MESSAGES, query: str = "is:inbox",
           service=None, **classify_kwargs) -> dict:
    """Read-only triage: fetch → classify (local) → rank. Returns
    {count, query, items:[...ranked], by_priority, by_category}."""
    messages = fetch_recent(max_messages, query, service=service)
    if not messages:
        return {"count": 0, "query": query, "items": [],
                "by_priority": {}, "by_category": {}}
    items = classify(messages, **classify_kwargs)
    items.sort(key=_rank_key)
    by_pri, by_cat = {}, {}
    for it in items:
        by_pri[it["priority"]] = by_pri.get(it["priority"], 0) + 1
        by_cat[it["category"]] = by_cat.get(it["category"], 0) + 1
    return {"count": len(items), "query": query, "items": items,
            "by_priority": by_pri, "by_category": by_cat}
