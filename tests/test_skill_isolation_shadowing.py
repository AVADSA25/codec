"""Skill isolation + shadowing pins (B3 / SR-23).

Audit T2 found 55 stale shadow skills in ~/.codec/skills/ (49 blocked by
AST gate, 5 load successfully). The canonical loader at codec_dispatch
points at the repo's `skills/` directory only, so built-ins are not
actually shadowed in production today. This test pins that invariant:
the production dispatch path uses the repo skills/ dir, not the user
~/.codec/skills/ dir, when both exist.
"""

import os
from pathlib import Path


def test_canonical_skills_dir_is_repo_skills():
    """The production loader at codec_dispatch must not auto-load
    skills from ~/.codec/skills/.

    If a future refactor introduces a merged registry across both dirs,
    this test will fail and prompt a security-implications review.
    """
    import codec_dispatch
    text = Path(codec_dispatch.__file__).read_text()
    # ~/.codec/skills/ must NOT appear in the LOADER CODE — string
    # references in module docstrings are OK. Strip the module docstring
    # before scanning for SkillRegistry construction sites.
    import ast
    tree = ast.parse(text)
    # Remove the module-level docstring if present so we don't false-
    # positive on comments / explanatory text.
    body_no_docstring = list(tree.body)
    if (body_no_docstring and isinstance(body_no_docstring[0], ast.Expr)
            and isinstance(body_no_docstring[0].value, ast.Constant)
            and isinstance(body_no_docstring[0].value.value, str)):
        body_no_docstring = body_no_docstring[1:]
    tree.body = body_no_docstring
    code_text = ast.unparse(tree)
    assert "/.codec/skills" not in code_text, (
        "codec_dispatch code must not reference ~/.codec/skills — "
        "that would re-introduce the shadow-skill risk from T2")


def test_builtin_skill_registry_loads_calculator():
    """Sample assertion: calculator must be in the registry (it's a
    well-known built-in). A future refactor that breaks the loader will
    fail this."""
    from codec_skill_registry import SkillRegistry
    import codec_dispatch
    # Use the canonical loader's path.
    repo_skills = os.path.join(
        os.path.dirname(os.path.abspath(codec_dispatch.__file__)),
        "skills",
    )
    reg = SkillRegistry(repo_skills)
    reg.scan()
    skill_names = reg.names()
    assert "calculator" in skill_names or any("calc" in n for n in skill_names), (
        "calculator skill missing from canonical registry — "
        "shadow-skill protection may have regressed")


def test_dispatch_isolation_per_skill_error():
    """A failure in one skill must not affect dispatch for the next.
    Pinned via codec_dispatch's per-skill try/except + wake_skill_error
    audit emit."""
    import codec_dispatch
    text = Path(codec_dispatch.__file__).read_text()
    # The per-skill exception handler must be present.
    assert "except Exception" in text, (
        "codec_dispatch must catch per-skill exceptions — "
        "removing the handler reintroduces cascading-failure risk")
    assert "wake_skill_error" in text, (
        "wake_skill_error audit emit must remain — "
        "removing it loses forensic visibility on skill failures")
