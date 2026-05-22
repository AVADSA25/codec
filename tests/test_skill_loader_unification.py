"""Tests for A-4 — skill-loader unification.

The legacy `codec_core` eager loader (loaded_skills/load_skills/run_skill) was
removed; codec.py + the dashboard cortex_skills endpoint now use the canonical
`codec_dispatch` registry. The registry also gained `custom_triggers.json`
support (Option A) so overrides are honored everywhere, not just the old voice
path.

Reference: docs/audits/PHASE-1-CODE-QUALITY.md A-4 + docs/A4-SKILL-LOADER-UNIFICATION-DESIGN.md
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


# ── Legacy symbols removed ───────────────────────────────────────────────────


def test_codec_core_legacy_skill_symbols_removed():
    import codec_core
    for sym in ("loaded_skills", "load_skills", "run_skill"):
        assert not hasattr(codec_core, sym), (
            f"codec_core.{sym} must be removed (A-4) — canonical path is codec_dispatch"
        )


def test_codec_no_local_check_skills_ranked():
    src = (REPO / "codec.py").read_text()
    assert "def check_skills_ranked" not in src, "codec.py local skill matcher must be removed (A-4)"
    assert "def check_skill(task):" not in src, "codec.py local check_skill must be removed (A-4)"
    # And it now imports the canonical dispatch
    assert "from codec_dispatch import check_skill, run_skill, load_skills" in src


def test_codec_imports_clean():
    import importlib
    import codec
    importlib.reload(codec)
    assert callable(codec.check_skill)
    assert callable(codec.run_skill)
    assert callable(codec.load_skills)


# ── Canonical dispatch round-trip ────────────────────────────────────────────


def test_canonical_dispatch_runs_real_skill():
    """check_skill + run_skill execute a real built-in skill end-to-end."""
    import codec_dispatch
    codec_dispatch.registry.scan()
    skill = codec_dispatch.check_skill("calculate 2 + 2")
    assert skill is not None, "calculator should match 'calculate ...'"
    result = codec_dispatch.run_skill(skill, "calculate 2 + 2", "")
    assert result and "4" in str(result), f"expected 4 in result, got {result!r}"


# ── custom_triggers.json honored by the registry (Option A) ──────────────────


@pytest.fixture
def registry_with_custom(tmp_path, monkeypatch):
    """A SkillRegistry over a tmp skills dir + a tmp custom_triggers.json."""
    import codec_skill_registry as reg_mod
    skills = tmp_path / "skills"
    skills.mkdir()
    # A minimal real skill
    (skills / "greeter.py").write_text(
        'SKILL_NAME = "greeter"\n'
        'SKILL_DESCRIPTION = "greets"\n'
        'SKILL_TRIGGERS = ["say hello"]\n'
        'def run(task, app="", ctx=""):\n    return "hi there"\n'
    )
    custom = tmp_path / "custom_triggers.json"
    monkeypatch.setattr(reg_mod, "CUSTOM_TRIGGERS_PATH", str(custom))
    return reg_mod, skills, custom


def test_custom_triggers_override_matching(registry_with_custom):
    reg_mod, skills, custom = registry_with_custom
    # User remaps greeter's trigger to "yo"
    custom.write_text(json.dumps({"greeter": {"triggers": ["yo"]}}))
    r = reg_mod.SkillRegistry(str(skills))
    r.scan()
    # Custom trigger matches
    assert "greeter" in r.match_all_triggers("yo dude")
    # get_triggers reflects the override
    assert r.get_triggers("greeter") == ["yo"]
    # Original trigger no longer matches (it was replaced)
    assert "greeter" not in r.match_all_triggers("say hello")


def test_no_custom_triggers_uses_defaults(registry_with_custom):
    reg_mod, skills, custom = registry_with_custom
    # No custom file written → defaults apply
    r = reg_mod.SkillRegistry(str(skills))
    r.scan()
    assert r.get_triggers("greeter") == ["say hello"]
    assert "greeter" in r.match_all_triggers("please say hello")


def test_malformed_custom_triggers_tolerated(registry_with_custom):
    reg_mod, skills, custom = registry_with_custom
    custom.write_text("{ not valid json")
    r = reg_mod.SkillRegistry(str(skills))
    r.scan()
    # Falls back to defaults, no crash
    assert r.get_triggers("greeter") == ["say hello"]


# ── cortex_skills endpoint reads from the registry ───────────────────────────


def test_cortex_skills_endpoint_uses_registry():
    src = (REPO / "codec_dashboard.py").read_text()
    # Endpoint must no longer import the legacy codec_core list
    idx = src.find("async def cortex_skills")
    assert idx >= 0
    body = src[idx:idx + 700]
    assert "from codec_dispatch import registry" in body
    # No live import of the legacy list (a comment mentioning it is fine)
    assert "from codec_core import loaded_skills" not in body


def test_cortex_skills_endpoint_registered():
    import codec_dashboard
    # The route stays registered post-migration (handler now reads the registry).
    paths = [getattr(r, "path", None) for r in codec_dashboard.app.routes]
    assert "/api/cortex/skills" in paths


# ── source invariant: voice path now goes through run_with_hooks ─────────────


def test_codec_dispatch_run_skill_uses_hooks():
    src = (REPO / "codec_dispatch.py").read_text()
    assert "run_with_hooks" in src, (
        "canonical run_skill must wrap run_with_hooks (the legacy path bypassed it)"
    )
