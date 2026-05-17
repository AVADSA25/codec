"""Tests for codec_skill_registry.SkillRegistry.load — load-time AST safety check.

Closes D-1 (CRITICAL): skill registry lazy-load = RCE for anyone who can drop
a .py file in ~/.codec/skills/. The load-time AST check refuses dangerous
skills BEFORE exec_module, regardless of how the file reached disk.

Reference: docs/audits/PHASE-1-SECURITY.md finding D-1 and
docs/audits/PHASE-1-CONSOLIDATED-TRIAGE.md §3.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from codec_skill_registry import SkillRegistry  # noqa: E402


# ── Skill fixtures (source bodies written to disk by each test) ───────────────

SAFE_SKILL = '''
SKILL_NAME = "safe_test"
SKILL_DESCRIPTION = "Safe test skill for AST check verification"
SKILL_TRIGGERS = ["safe test"]

def run(task, app="", ctx=""):
    return f"ok: {task}"
'''

DANGEROUS_SUBPROCESS = '''
SKILL_NAME = "danger_subprocess"
SKILL_DESCRIPTION = "Uses subprocess at module level (load-time exec risk)"
SKILL_TRIGGERS = ["danger subprocess"]

import subprocess

def run(task, app="", ctx=""):
    subprocess.run(["echo", "hacked"])
    return "done"
'''

DANGEROUS_EVAL = '''
SKILL_NAME = "danger_eval"
SKILL_DESCRIPTION = "Calls eval() with user-controlled input"
SKILL_TRIGGERS = ["danger eval"]

def run(task, app="", ctx=""):
    return eval(task)
'''

DANGEROUS_OS_SYSTEM = '''
SKILL_NAME = "danger_os_system"
SKILL_DESCRIPTION = "Calls os.system with user-controlled input"
SKILL_TRIGGERS = ["danger os system"]

import os

def run(task, app="", ctx=""):
    os.system(task)
    return "done"
'''

DANGEROUS_DUNDER_IMPORT = '''
SKILL_NAME = "danger_dunder"
SKILL_DESCRIPTION = "Uses __import__ to fetch os at runtime"
SKILL_TRIGGERS = ["danger dunder"]

def run(task, app="", ctx=""):
    mod = __import__("os")
    return mod.getcwd()
'''


# ── Helpers ───────────────────────────────────────────────────────────────────


def _write_skill(tmp_path: Path, filename: str, body: str) -> tuple[SkillRegistry, Path]:
    """Create a temp skills dir with one skill file; return scanned registry + path."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    skill_file = skills_dir / filename
    skill_file.write_text(body, encoding="utf-8")
    reg = SkillRegistry(str(skills_dir))
    reg.scan()
    return reg, skill_file


def _was_block_event_emitted(mock_log_event) -> bool:
    """Check whether log_event was called with event_type='skill_load_blocked'."""
    for call in mock_log_event.call_args_list:
        # log_event signature: log_event(event_type, source, ...) — positional or kwarg
        event_type = (
            call.args[0]
            if call.args
            else call.kwargs.get("event_type")
        )
        if event_type == "skill_load_blocked":
            return True
    return False


# ── Tests (D-1) ───────────────────────────────────────────────────────────────


def test_load_refuses_dangerous_skill_code(tmp_path):
    """A skill that imports subprocess must be refused at load time."""
    reg, _ = _write_skill(tmp_path, "danger_subprocess.py", DANGEROUS_SUBPROCESS)

    with patch("codec_skill_registry.log_event") as mock_log_event:
        result = reg.load("danger_subprocess")

    assert result is None, "load() must return None for a skill with `import subprocess`"
    assert _was_block_event_emitted(mock_log_event), (
        "Expected log_event(event_type='skill_load_blocked', ...) — got "
        f"{mock_log_event.call_args_list}"
    )


def test_load_refuses_skill_with_eval(tmp_path):
    """A skill that calls eval() must be refused at load time."""
    reg, _ = _write_skill(tmp_path, "danger_eval.py", DANGEROUS_EVAL)

    with patch("codec_skill_registry.log_event") as mock_log_event:
        result = reg.load("danger_eval")

    assert result is None, "load() must return None for a skill that calls eval()"
    assert _was_block_event_emitted(mock_log_event)


def test_load_refuses_skill_with_os_system(tmp_path):
    """A skill that calls os.system() must be refused at load time."""
    reg, _ = _write_skill(tmp_path, "danger_os_system.py", DANGEROUS_OS_SYSTEM)

    with patch("codec_skill_registry.log_event") as mock_log_event:
        result = reg.load("danger_os_system")

    assert result is None, "load() must return None for a skill that calls os.system()"
    assert _was_block_event_emitted(mock_log_event)


def test_load_refuses_skill_with_dunder_import(tmp_path):
    """A skill that calls __import__() must be refused at load time."""
    reg, _ = _write_skill(tmp_path, "danger_dunder.py", DANGEROUS_DUNDER_IMPORT)

    with patch("codec_skill_registry.log_event") as mock_log_event:
        result = reg.load("danger_dunder")

    assert result is None, "load() must return None for a skill that calls __import__()"
    assert _was_block_event_emitted(mock_log_event)


def test_load_accepts_safe_skill(tmp_path):
    """A safe skill with only return-string logic must load successfully."""
    reg, _ = _write_skill(tmp_path, "safe_test.py", SAFE_SKILL)

    with patch("codec_skill_registry.log_event") as mock_log_event:
        result = reg.load("safe_test")

    assert result is not None, "load() must succeed for a safe skill"
    assert hasattr(result, "run"), "loaded module must expose run()"
    # Sanity: run() actually works
    assert result.run("hello") == "ok: hello"
    # No block event for safe load
    assert not _was_block_event_emitted(mock_log_event), (
        "Safe skill must not emit skill_load_blocked. Got: "
        f"{mock_log_event.call_args_list}"
    )


def test_load_handles_unreadable_file(tmp_path):
    """If the skill file is unreadable (e.g. perms revoked after scan),
    load() must return None gracefully — not crash."""
    reg, skill_file = _write_skill(tmp_path, "safe_test.py", SAFE_SKILL)

    # Remove all read permissions after scan; load should refuse cleanly.
    # On systems where root can still read, the test instead asserts
    # the broader "no crash" behavior by deleting the file.
    try:
        os.chmod(skill_file, 0)
        # Verify the chmod actually blocks reading (skip test if running as root)
        try:
            skill_file.read_text(encoding="utf-8")
            unreadable = False
        except (PermissionError, OSError):
            unreadable = True
        if not unreadable:
            # Running as root or perm bits ignored — fall back to file deletion
            os.chmod(skill_file, stat.S_IRWXU)
            skill_file.unlink()

        with patch("codec_skill_registry.log_event"):
            result = reg.load("safe_test")

        assert result is None, "load() must return None on read failure, not crash"
    finally:
        # Restore perms for tmp_path cleanup (best-effort).
        try:
            if skill_file.exists():
                os.chmod(skill_file, stat.S_IRWXU)
        except OSError:
            pass


def test_ast_check_failure_fails_safe(tmp_path):
    """If is_dangerous_skill_code itself raises, load() must refuse the skill
    rather than fall through to exec_module — fail-safe behavior."""
    reg, _ = _write_skill(tmp_path, "safe_test.py", SAFE_SKILL)

    def boom(_code):
        raise RuntimeError("intentional AST check failure")

    with patch("codec_skill_registry.is_dangerous_skill_code", side_effect=boom):
        with patch("codec_skill_registry.log_event"):
            result = reg.load("safe_test")

    assert result is None, "Fail-safe: AST check exception must refuse the load"


# ── Extra: audit event payload shape ──────────────────────────────────────────


def test_load_block_event_includes_skill_metadata(tmp_path):
    """The skill_load_blocked audit event must include skill_name, skill_path,
    and a reason field so forensic review has enough context."""
    reg, skill_file = _write_skill(tmp_path, "danger_subprocess.py", DANGEROUS_SUBPROCESS)

    with patch("codec_skill_registry.log_event") as mock_log_event:
        reg.load("danger_subprocess")

    # Find the block call
    block_call = None
    for call in mock_log_event.call_args_list:
        event_type = call.args[0] if call.args else call.kwargs.get("event_type")
        if event_type == "skill_load_blocked":
            block_call = call
            break

    assert block_call is not None, "no skill_load_blocked event emitted"

    extra = block_call.kwargs.get("extra", {})
    assert extra.get("skill_name") == "danger_subprocess"
    assert extra.get("skill_path") == str(skill_file)
    assert extra.get("reason"), "reason field must be populated (non-empty string)"


def test_load_cached_module_skips_recheck(tmp_path):
    """Once a skill is loaded successfully, subsequent loads should hit the
    in-memory cache and NOT re-run the AST check (perf + idempotence)."""
    reg, _ = _write_skill(tmp_path, "safe_test.py", SAFE_SKILL)

    # First load: AST check runs
    first = reg.load("safe_test")
    assert first is not None

    # Second load: should hit cache; AST check must not run again
    with patch("codec_skill_registry.is_dangerous_skill_code") as mock_check:
        second = reg.load("safe_test")
    assert second is first, "cached module must be returned, not re-loaded"
    mock_check.assert_not_called()


# ── Trusted-manifest tests ────────────────────────────────────────────────────


def _write_manifest(skills_dir: Path, mapping: dict[str, str]) -> None:
    """Write a .manifest.json with the given filename -> sha256-hex pairs."""
    import json
    (skills_dir / ".manifest.json").write_text(
        json.dumps({"schema": 1, "skills": mapping}, indent=2),
        encoding="utf-8",
    )


def test_load_trusted_manifest_bypasses_ast_check(tmp_path):
    """A skill whose sha256 is in the trusted manifest must load even if its
    source contains dangerous patterns. This is the path that lets built-ins
    like calculator/system/file_write/pilot continue to work."""
    import hashlib

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    skill_file = skills_dir / "trusted_danger.py"
    skill_file.write_text(DANGEROUS_SUBPROCESS, encoding="utf-8")

    # Pin the file's hash in a manifest BEFORE scan.
    file_hash = hashlib.sha256(skill_file.read_bytes()).hexdigest()
    _write_manifest(skills_dir, {"trusted_danger.py": file_hash})

    reg = SkillRegistry(str(skills_dir))
    reg.scan()

    # SKILL_NAME in the dangerous skill body is "danger_subprocess"
    with patch("codec_skill_registry.log_event") as mock_log_event:
        result = reg.load("danger_subprocess")

    assert result is not None, (
        "load() must succeed for a manifest-trusted skill, even if its source "
        "contains dangerous patterns"
    )
    assert hasattr(result, "run")
    assert not _was_block_event_emitted(mock_log_event), (
        "Trusted skill must not emit skill_load_blocked"
    )


def test_load_dangerous_skill_with_mismatched_hash_still_refused(tmp_path):
    """If the manifest contains a hash that doesn't match the on-disk file
    (e.g. attacker swapped the file after the manifest was committed), the
    file is NOT trusted and the AST check runs — and refuses it."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    skill_file = skills_dir / "tampered.py"
    skill_file.write_text(DANGEROUS_SUBPROCESS, encoding="utf-8")

    # Manifest claims a different hash — the file's actual hash will not match.
    _write_manifest(skills_dir, {"tampered.py": "0" * 64})

    reg = SkillRegistry(str(skills_dir))
    reg.scan()

    with patch("codec_skill_registry.log_event") as mock_log_event:
        result = reg.load("danger_subprocess")

    assert result is None, "Tampered file (hash mismatch) must be refused"
    assert _was_block_event_emitted(mock_log_event), (
        "Hash mismatch → AST check runs → blocks → block event emitted"
    )


def test_load_corrupted_manifest_falls_back_to_ast_check(tmp_path):
    """A malformed manifest must not crash the registry. Behavior: treat as
    empty (no trusted hashes), so the AST check applies to every file."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    skill_file = skills_dir / "danger_eval.py"
    skill_file.write_text(DANGEROUS_EVAL, encoding="utf-8")

    # Write corrupted manifest.
    (skills_dir / ".manifest.json").write_text("{ this is not json", encoding="utf-8")

    reg = SkillRegistry(str(skills_dir))
    reg.scan()  # must not raise

    with patch("codec_skill_registry.log_event") as mock_log_event:
        result = reg.load("danger_eval")

    assert result is None, "Corrupted manifest → AST check runs → dangerous skill refused"
    assert _was_block_event_emitted(mock_log_event)


def test_load_no_manifest_treats_dir_as_untrusted(tmp_path):
    """No manifest at all (the typical ~/.codec/skills/ case) means every
    skill in that dir runs the AST check. Dangerous skills → refused."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "danger_os_system.py").write_text(DANGEROUS_OS_SYSTEM, encoding="utf-8")
    assert not (skills_dir / ".manifest.json").exists()

    reg = SkillRegistry(str(skills_dir))
    reg.scan()

    with patch("codec_skill_registry.log_event") as mock_log_event:
        result = reg.load("danger_os_system")

    assert result is None
    assert _was_block_event_emitted(mock_log_event)
