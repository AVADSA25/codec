"""CODEC Skill: Email triage — read-only ranked inbox digest (local-classified)."""
import re

from codec_email_triage import triage

SKILL_NAME = "email_triage"
SKILL_DESCRIPTION = (
    "Triage your Gmail inbox: read a recent window, classify each message "
    "(lead/support/personal/transactional/noise + priority) on the LOCAL model, "
    "and return a ranked digest. Read-only — applies no labels, sends nothing."
)
SKILL_TAGS = ["email", "triage", "gmail", "inbox", "productivity"]
SKILL_TRIGGERS = [
    "triage my email", "triage inbox", "triage my inbox", "email triage",
    "prioritize my email", "what's important in my inbox", "sort my inbox",
]
SKILL_MCP_EXPOSE = True  # read-only digest (metadata only); mirrors google_gmail's exposure

_PRI_EMOJI = {"high": "🔴", "medium": "🟡", "low": "⚪"}
_PRI_ORDER = ("high", "medium", "low")


def _parse_count(task: str, default: int = 25) -> int:
    m = re.search(r"\b(\d{1,2})\b", task or "")
    if m:
        try:
            return max(1, min(int(m.group(1)), 50))
        except ValueError:
            pass
    return default


def run(task, app="", ctx=""):
    low = (task or "").lower()
    query = "is:unread is:inbox" if "unread" in low else "is:inbox"
    count = _parse_count(task)
    try:
        result = triage(max_messages=count, query=query)
    except Exception as e:
        msg = str(e).lower()
        if "credential" in msg or "token" in msg or "auth" in msg:
            return "I can't reach your Gmail — connect Google first (the google auth flow), then try again."
        return f"Email triage failed: {e}"

    items = result.get("items", [])
    if not items:
        scope = "unread " if "unread" in query else ""
        return f"No {scope}inbox messages to triage."

    bp = result.get("by_priority", {})
    head = (f"📥 Inbox triage — {result['count']} message"
            f"{'s' if result['count'] != 1 else ''} "
            f"({bp.get('high', 0)} high, {bp.get('medium', 0)} medium, {bp.get('low', 0)} low)")
    lines = [head]
    for pri in _PRI_ORDER:
        group = [it for it in items if it.get("priority") == pri]
        if not group:
            continue
        lines.append(f"\n{_PRI_EMOJI[pri]} {pri.upper()}")
        for it in group:
            star = "* " if it.get("unread") else "  "
            reason = f"  — {it['reason']}" if it.get("reason") else ""
            lines.append(f"  {star}[{it.get('category')}] {it.get('sender')} — "
                         f"{it.get('subject')}{reason}")
    return "\n".join(lines)
