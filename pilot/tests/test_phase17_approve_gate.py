"""Pilot PP-11 — approving an auto-compiled skill runs an AST safety gate (Pilot can't
import the parent codec_config, so a minimal equivalent is vendored). Closes audit P-3
(approve was a bare shutil.move with no safety check). Defense-in-depth on top of PP-2
(compiler can't inject) + the parent registry's load-time gate.

Reference: docs/PP11-APPROVE-GATE-DESIGN.md.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # ~/codec on sys.path

from pilot import safety, skill_review as sr  # noqa: E402


def test_ast_gate_flags_dangerous_code():
    bad, reason = safety.is_dangerous_skill_code("import os\nos.system('id')\n")
    assert bad and "os" in reason


def test_ast_gate_flags_eval():
    bad, _ = safety.is_dangerous_skill_code("x = eval('2+2')\n")
    assert bad


def test_ast_gate_passes_compiled_skill_shape():
    safe_src = (
        '"""auto-generated"""\n'
        "import asyncio\n"
        "from pathlib import Path\n"
        "SKILL_NAME = 'pilot_demo'\n"
        "async def run():\n    return {}\n"
    )
    bad, reason = safety.is_dangerous_skill_code(safe_src)
    assert not bad, reason


def test_ast_gate_flags_syntax_error():
    bad, _ = safety.is_dangerous_skill_code("def (:\n")
    assert bad


def test_approve_refuses_dangerous_skill(tmp_path, monkeypatch):
    pend = tmp_path / "pending"
    active = tmp_path / "skills"
    pend.mkdir()
    active.mkdir()
    (pend / "pilot_evil.py").write_text("import subprocess\nsubprocess.run(['id'])\n")
    monkeypatch.setattr(sr, "SKILLS_PENDING_DIR", pend)
    monkeypatch.setattr(sr, "SKILLS_DIR", active)

    with pytest.raises(PermissionError):
        sr.approve_pending("evil")
    assert not (active / "pilot_evil.py").exists(), "dangerous skill must NOT be moved to active (P-3)"


def test_approve_allows_safe_skill(tmp_path, monkeypatch):
    pend = tmp_path / "pending"
    active = tmp_path / "skills"
    pend.mkdir()
    active.mkdir()
    (pend / "pilot_ok.py").write_text("SKILL_NAME='pilot_ok'\nasync def run():\n    return {}\n")
    monkeypatch.setattr(sr, "SKILLS_PENDING_DIR", pend)
    monkeypatch.setattr(sr, "SKILLS_DIR", active)

    dst = sr.approve_pending("ok")
    assert Path(dst).exists()
