"""CODEC Skill: Memory Entities — manage CCF compression abbreviation map."""
SKILL_NAME = "memory_entities"
SKILL_DESCRIPTION = "Manage the entity abbreviation map used by CCF memory compression"
SKILL_TRIGGERS = [
    "list entities", "show entities", "entity map",
    "add entity", "remove entity", "compress text", "decompress text",
]
SKILL_MCP_EXPOSE = True

import sys, os, re, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from codec_memory_upgrade import (
    list_entities, add_entity, remove_entity,
    compress_rule_based, decompress_for_display,
)


def run(task: str, app: str = "", ctx: str = "") -> str:
    low = task.lower().strip()

    m = re.match(r'add entity\s+"?([^"]+?)"?\s*(?:=|->|as)\s*"?([^"]+?)"?$', task, re.I)
    if m:
        full, abbr = m.group(1).strip(), m.group(2).strip()
        add_entity(full, abbr)
        return f"Added: {full} → {abbr}"

    m = re.match(r'remove entity\s+"?([^"]+?)"?$', task, re.I)
    if m:
        full = m.group(1).strip()
        remove_entity(full)
        return f"Removed: {full}"

    m = re.match(r'compress text[:\s]+(.+)', task, re.I | re.S)
    if m:
        return compress_rule_based(m.group(1))

    m = re.match(r'decompress text[:\s]+(.+)', task, re.I | re.S)
    if m:
        return decompress_for_display(m.group(1))

    # default: list entities
    return json.dumps(list_entities(), indent=2)
