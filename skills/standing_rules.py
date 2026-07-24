"""CODEC Skill: Standing Rules — instructions that persist across every session.

Say "add a standing rule: always answer in French" and it is written to
~/.codec/standing_rules.json and appended to the system prompt on every turn
from then on.

This exists because CODEC used to bluff about it. Asked to adopt a rules
document, it replied "I have ingested the instruction set, I am now operating
under this framework for all future interactions" — with no mechanism behind it.
codec_claim_check now catches that claim; this skill makes the honest version
possible.
"""

import re

SKILL_NAME = "standing_rules"
SKILL_DESCRIPTION = (
    "Save, list or remove standing rules — instructions CODEC applies to every "
    "reply, in every session. Persisted to disk, not just remembered in chat."
)
SKILL_MCP_EXPOSE = True
SKILL_TRIGGERS = [
    "add a standing rule",
    "add standing rule",
    "standing rule",
    "standing rules",
    "my rules",
    "always remember to",
    "from now on always",
]

_ADD = re.compile(
    r"^(?:add\s+(?:a\s+)?standing\s+rule|standing\s+rule|from\s+now\s+on\s+always|"
    r"always\s+remember\s+to)\b[:\s]*(.+)$", re.I | re.S)
_REMOVE = re.compile(
    r"\b(?:remove|delete|drop|forget)\b.*\b(?:standing\s+)?rule\b\s*#?\s*(\w+)?", re.I)
_LIST = re.compile(
    r"\b(?:list|show|what(?:'s| are)|see)\b.*\b(?:standing\s+)?rules?\b", re.I)
_CLEAR = re.compile(r"\b(?:clear|wipe|reset)\b.*\b(?:standing\s+)?rules\b", re.I)


def _render(rules):
    if not rules:
        return ("You have no standing rules yet. Add one with: "
                "\"add a standing rule: <instruction>\".")
    lines = [f"You have {len(rules)} standing rule(s), added to every reply:"]
    never = []
    for i, r in enumerate(rules, 1):
        n = int(r.get("inject_count") or 0)
        last = r.get("last_injected")
        when = f", last {str(last)[:10]}" if last else ""
        lines.append(f"  {i}. {r.get('text', '')}")
        lines.append(f"     sent to the model {n}x{when}" if n else
                     "     never sent to the model yet")
        if not n:
            never.append(i)
    lines.append("")
    lines.append("\"sent to the model\" counts INJECTION into the prompt — not whether "
                 "the model actually used the rule. Nothing in the pipeline can see that, "
                 "so it isn't claimed.")
    if never:
        lines.append(f"Rules {', '.join(f'#{i}' for i in never)} have never even reached "
                     f"the model — those are safe to delete.")
    lines.append("Remove one with: \"remove standing rule 2\".")
    return "\n".join(lines)


def run(task, app="", ctx=""):
    import codec_standing_rules as sr

    t = (task or "").strip()
    low = t.lower()

    if _CLEAR.search(low):
        return sr.clear_rules()["message"]

    m = _REMOVE.search(t)
    if m and m.group(1):
        return sr.remove_rule(m.group(1))["message"]

    m = _ADD.match(t)
    if m and m.group(1).strip():
        res = sr.add_rule(m.group(1).strip())
        if not res["ok"]:
            return res["message"]
        rules = sr.list_rules()
        return (f"{res['message']} It's saved to ~/.codec/standing_rules.json "
                f"and will be applied to every reply from now on — including "
                f"after a restart. You now have {len(rules)}.")

    if _LIST.search(low) or low in ("standing rules", "my rules"):
        return _render(sr.list_rules())

    return _render(sr.list_rules())
