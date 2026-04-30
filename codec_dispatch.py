"""CODEC Skill Dispatch — load and match skills from ~/.codec/skills/

Uses SkillRegistry for lazy loading: only metadata (name, triggers,
description) is parsed at startup via AST.  The actual module import
happens on first invocation of a skill.
"""
import logging
import secrets
import time

# Audit emits route through the unified log_event adapter (real, not no-op)
# per docs/PHASE1-STEP1-DESIGN.md.
from codec_audit import log_event
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
    module import on first call.  Stores all matching skill names so
    run_skill can fall through to the next match if a skill returns None.
    """
    matches = registry.match_all_triggers(task)
    if not matches:
        return None
    name = matches[0]
    return {
        'name': name,
        'triggers': registry.get_triggers(name),
        '_all_matches': matches,
        'run': lambda task, app="", **kw: registry.run(name, task, app),
    }


def run_skill(skill, task, app=""):
    """Execute a skill and return its result.

    If the skill returns None (indicating it can't handle the task),
    falls through to the next matching skill.
    """
    all_matches = skill.get('_all_matches', [skill.get('name')])
    # One correlation_id for the whole dispatch attempt (covers any
    # fall-through retries across matched skills).
    cid = secrets.token_hex(6)
    t0 = time.monotonic()

    for skill_name in all_matches:
        try:
            result = registry.run(skill_name, task, app)
            if result is None:
                log.info("Skill '%s' returned None — trying next match", skill_name)
                continue
            log_event("wake_dispatch", "codec-dispatch",
                      f"Skill: {skill.get('name', '?')}",
                      tool=skill_name,
                      duration_ms=(time.monotonic() - t0) * 1000.0,
                      extra={"result_len": len(str(result)) if result else 0},
                      correlation_id=cid)
            try:
                import os as _os
                _events_path = _os.path.expanduser("~/.codec/overlay_events.jsonl")
                with open(_events_path, "a") as _f:
                    _f.write(f'{{"type":"skill_fired","name":"{skill_name}"}}\n')
            except Exception as e:
                log.debug("Overlay event write failed: %s", e)
            return result
        except Exception as e:
            log.warning("Skill '%s' error: %s — trying next match", skill_name, e)
            log_event("wake_skill_error", "codec-dispatch",
                      f"Skill error: {e}",
                      tool=skill_name,
                      outcome="error",
                      level="error",
                      error_type=type(e).__name__,
                      error=str(e)[:500],
                      correlation_id=cid)
            continue

    return None  # No skill could handle it
