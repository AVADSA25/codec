"""CODEC Skill: Memory History — temporal fact queries (valid now, history, contradictions)."""
SKILL_NAME = "memory_history"
SKILL_DESCRIPTION = "Query the temporal facts store: active facts, history of a key, or contradictions"
SKILL_TRIGGERS = [
    "fact history", "what do you know about", "fact for", "remember fact",
    "list facts", "active facts", "what's my", "whats my",
    "set fact", "record fact", "contradictions",
]
SKILL_MCP_EXPOSE = True

import sys, os, re, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from codec_memory_upgrade import (
    store_fact, query_valid_facts, get_fact_history, find_contradictions,
)


def run(task: str, app: str = "", ctx: str = "") -> str:
    low = task.lower().strip()

    # set fact <key> = <value>
    m = re.match(r'(?:set|record|remember)\s+fact\s+(.+?)\s*(?:=|is|:)\s*(.+)', task, re.I)
    if m:
        key, value = m.group(1).strip(), m.group(2).strip()
        fid = store_fact(key, value, source="skill:memory_history")
        return f"Stored fact #{fid}: {key} = {value}"

    if "contradict" in low:
        return json.dumps(find_contradictions(), indent=2) or "No contradictions."

    m = re.search(r'history (?:of |for )?(.+)', low)
    if m:
        key = m.group(1).strip(" ?.")
        h = get_fact_history(key)
        return json.dumps(h, indent=2) if h else f"No history for '{key}'."

    m = re.search(r'(?:fact for|know about|what.?s my)\s+(.+)', low)
    if m:
        key = m.group(1).strip(" ?.")
        rows = query_valid_facts(key=key)
        return json.dumps(rows, indent=2) if rows else f"No active fact for '{key}'."

    # default: list all active
    rows = query_valid_facts(limit=25)
    return json.dumps(rows, indent=2) if rows else "No active facts."
