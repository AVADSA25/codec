"""CODEC Skill Dispatch — load and match skills from ~/.codec/skills/"""
import os
import importlib.util
import logging

from codec_config import SKILLS_DIR

log = logging.getLogger('codec')

loaded_skills = []


def load_skills():
    """Load all skill plugins from SKILLS_DIR into loaded_skills"""
    global loaded_skills
    loaded_skills = []
    if not os.path.isdir(SKILLS_DIR):
        return
    for fname in os.listdir(SKILLS_DIR):
        if fname.startswith('_') or not fname.endswith('.py'):
            continue
        path = os.path.join(SKILLS_DIR, fname)
        try:
            spec = importlib.util.spec_from_file_location(fname[:-3], path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, 'SKILL_TRIGGERS') and hasattr(mod, 'run'):
                loaded_skills.append({
                    'name': getattr(mod, 'SKILL_NAME', fname[:-3]),
                    'triggers': mod.SKILL_TRIGGERS,
                    'run': mod.run,
                })
                log.info(f"Skill loaded: {fname[:-3]}")
        except Exception as e:
            log.warning(f"Skill error ({fname}): {e}")


def check_skill(task):
    """Return matching skill dict for task, or None"""
    low = task.lower()
    for skill in loaded_skills:
        if any(trigger in low for trigger in skill['triggers']):
            return skill
    return None


def run_skill(skill, task, app=""):
    """Execute a skill and return its result"""
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
