"""CODEC Skill Dispatch — load and match skills from ~/.codec/skills/

Uses SkillRegistry for lazy loading: only metadata (name, triggers,
description) is parsed at startup via AST.  The actual module import
happens on first invocation of a skill.
"""
import logging

from codec_config import SKILLS_DIR
from codec_skill_registry import SkillRegistry

log = logging.getLogger('codec')

# Global registry instance shared across codec.py
registry = SkillRegistry(SKILLS_DIR)


def load_skills():
    """Scan skill plugins from SKILLS_DIR — extracts metadata only (fast)."""
    registry.scan()


def check_skill(task):
    """Return a skill-like dict for the matching skill, or None.

    The dict has 'name' and a lazy 'run' key that triggers the actual
    module import on first call.
    """
    name = registry.match_trigger(task)
    if name is None:
        return None
    return {
        'name': name,
        'triggers': registry.get_triggers(name),
        'run': lambda task, app="", **kw: registry.run(name, task, app),
    }


def run_skill(skill, task, app=""):
    """Execute a skill and return its result."""
    try:
        result = skill['run'](task, app)
        skill_name = skill.get('name', 'unknown')
        try:
            with open("/tmp/codec_overlay_events.jsonl", "a") as _f:
                _f.write(f'{{"type":"skill_fired","name":"{skill_name}"}}\n')
        except Exception:
            pass
        return result
    except Exception as e:
        return f"Skill error: {e}"
