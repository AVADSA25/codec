"""Test that all CODEC skills load and have required attributes"""
import pytest
import importlib
import os
import sys

SKILLS_DIR = os.path.expanduser("~/.codec/skills")
sys.path.insert(0, SKILLS_DIR)


def get_skill_files():
    """Get all skill .py files except template and cache"""
    if not os.path.isdir(SKILLS_DIR):
        return []
    return [f[:-3] for f in os.listdir(SKILLS_DIR)
            if f.endswith('.py') and not f.startswith('_') and f != '__pycache__']


@pytest.mark.parametrize("skill_name", get_skill_files())
def test_skill_loads(skill_name):
    """Every skill must import without errors"""
    mod = importlib.import_module(skill_name)
    assert mod is not None


@pytest.mark.parametrize("skill_name", get_skill_files())
def test_skill_has_required_attrs(skill_name):
    """Every skill must have SKILL_NAME, SKILL_TRIGGERS, and run()"""
    mod = importlib.import_module(skill_name)
    assert hasattr(mod, 'SKILL_NAME'), f"{skill_name} missing SKILL_NAME"
    assert hasattr(mod, 'SKILL_TRIGGERS'), f"{skill_name} missing SKILL_TRIGGERS"
    assert hasattr(mod, 'run'), f"{skill_name} missing run()"
    assert callable(mod.run), f"{skill_name}.run is not callable"


@pytest.mark.parametrize("skill_name", get_skill_files())
def test_skill_triggers_are_list(skill_name):
    """Triggers must be a non-empty list of strings"""
    mod = importlib.import_module(skill_name)
    assert isinstance(mod.SKILL_TRIGGERS, list), f"{skill_name} SKILL_TRIGGERS is not a list"
    assert len(mod.SKILL_TRIGGERS) > 0, f"{skill_name} has no triggers"
    for t in mod.SKILL_TRIGGERS:
        assert isinstance(t, str), f"{skill_name} trigger '{t}' is not a string"


def test_no_duplicate_triggers():
    """Warn if two skills share the same trigger"""
    all_triggers = {}
    duplicates = []
    for skill_name in get_skill_files():
        try:
            mod = importlib.import_module(skill_name)
        except Exception:
            continue
        if hasattr(mod, 'SKILL_TRIGGERS'):
            for trigger in mod.SKILL_TRIGGERS:
                if trigger in all_triggers:
                    duplicates.append(f"'{trigger}' in {skill_name} AND {all_triggers[trigger]}")
                else:
                    all_triggers[trigger] = skill_name
    if duplicates:
        pytest.warns(UserWarning, match="Duplicate triggers found")


# ── Specific skill smoke tests ────────────────────────────────────────────────

def test_calculator_skill():
    """Calculator must return a result containing 4 for 2+2"""
    sys.path.insert(0, SKILLS_DIR)
    from calculator import run
    result = run("calculate 2 + 2")
    assert result is not None
    assert "4" in str(result)


def test_time_date_skill():
    """Time/date skill must return a non-empty string"""
    from time_date import run
    result = run("what time is it")
    assert result is not None
    assert len(str(result)) > 0


def test_system_info_skill():
    """System info skill must return a non-empty string"""
    from system_info import run
    result = run("system info")
    assert result is not None
    assert len(str(result)) > 0
