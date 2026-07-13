"""Tests for skills/file_write.py — verifies the path-safety gate refuses
writes to security-sensitive directories while still allowing legitimate
user paths.

Closes D-4 (CRITICAL): the file_write skill is MCP-exposed and NOT in
codec_config._HTTP_BLOCKED, so claude.ai over the 30-day OAuth token can
call it. Before this PR, _BLOCKED_ROOTS only listed /System, /etc, etc. —
so the skill happily wrote to ~/.codec/skills/<x>.py, which (combined
with the load-time gate from PR-1A's defense-in-depth) was a write-path
to disk that an attacker could chain. This PR closes the write path at
the skill itself.

Reference: docs/audits/PHASE-1-SECURITY.md finding D-4.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "skills"))

import file_write  # noqa: E402


HOME = os.path.expanduser("~")
CODEC_DIR = os.path.expanduser("~/.codec")
REPO_SKILLS = str(REPO / "skills")


# ── Tests that must REFUSE writes (D-4 closure) ───────────────────────────────


def test_refuses_codec_skills_dir():
    """Writing into ~/.codec/skills/ is the D-1 RCE write-path."""
    safe, reason = file_write._is_safe_target(
        os.path.join(CODEC_DIR, "skills", "backdoor.py")
    )
    assert not safe, "Must refuse writes to ~/.codec/skills/ (D-4 / D-1 chain)"
    assert reason, "Must surface a reason"


def test_refuses_codec_plugins_dir():
    """Writing into ~/.codec/plugins/ — plugins wrap every tool call per
    CLAUDE.md §3 Plugin lifecycle hooks. Same RCE risk as skills."""
    safe, reason = file_write._is_safe_target(
        os.path.join(CODEC_DIR, "plugins", "innocent.py")
    )
    assert not safe, "Must refuse writes to ~/.codec/plugins/"
    assert reason


def test_refuses_oauth_state_file():
    """OAuth tokens are 30-day bearer credentials per CLAUDE.md §10 don't-touch."""
    safe, reason = file_write._is_safe_target(
        os.path.join(CODEC_DIR, "oauth_state.json")
    )
    assert not safe, "Must refuse writes to ~/.codec/oauth_state.json"
    assert reason


def test_refuses_audit_log():
    """Audit log integrity is the compliance foundation (CLAUDE.md §6)."""
    safe, reason = file_write._is_safe_target(
        os.path.join(CODEC_DIR, "audit.log")
    )
    assert not safe, "Must refuse writes to ~/.codec/audit.log"


def test_refuses_codec_config_file():
    """config.json contains API keys, dashboard token, PIN hash."""
    safe, reason = file_write._is_safe_target(
        os.path.join(CODEC_DIR, "config.json")
    )
    assert not safe, "Must refuse writes to ~/.codec/config.json"


def test_refuses_memory_db():
    """memory.db is the SQLite store for conversations + facts."""
    safe, reason = file_write._is_safe_target(
        os.path.join(CODEC_DIR, "memory.db")
    )
    assert not safe, "Must refuse writes to ~/.codec/memory.db"


def test_refuses_agents_state_file():
    """~/.codec/agents/<id>/state.json governs the Phase 3 agent runtime."""
    safe, reason = file_write._is_safe_target(
        os.path.join(CODEC_DIR, "agents", "abc123", "state.json")
    )
    assert not safe, "Must refuse writes inside ~/.codec/agents/"


def test_refuses_agent_global_grants():
    """Cross-agent allowlist; tampering elevates permission grants."""
    safe, reason = file_write._is_safe_target(
        os.path.join(CODEC_DIR, "agent_global_grants.json")
    )
    assert not safe, "Must refuse writes to ~/.codec/agent_global_grants.json"


def test_refuses_pending_questions():
    """pending_questions.json — direct edits race ask_user.ask() (CLAUDE.md §10)."""
    safe, reason = file_write._is_safe_target(
        os.path.join(CODEC_DIR, "pending_questions.json")
    )
    assert not safe, "Must refuse writes to ~/.codec/pending_questions.json"


def test_refuses_triggers_killed():
    """Per-trigger kill state — tampering re-enables muted triggers."""
    safe, reason = file_write._is_safe_target(
        os.path.join(CODEC_DIR, "triggers_killed.json")
    )
    assert not safe


def test_refuses_repo_skills_dir():
    """<repo>/skills/ holds built-in skills; an attacker writing here
    contaminates the hash-pinned trusted manifest from PR-1A."""
    safe, reason = file_write._is_safe_target(
        os.path.join(REPO_SKILLS, "backdoor.py")
    )
    assert not safe, "Must refuse writes to <repo>/skills/ (built-in skill dir)"
    assert reason


def test_refuses_codec_root_itself():
    """Even direct files at ~/.codec/<file> are off-limits."""
    safe, reason = file_write._is_safe_target(
        os.path.join(CODEC_DIR, "evil.txt")
    )
    assert not safe, "Must refuse arbitrary writes anywhere under ~/.codec/"


def test_refuses_symlinked_path_into_codec(tmp_path):
    """An attacker dropping a symlink ~/Documents/sneaky → ~/.codec/skills
    must NOT slip past the gate. realpath resolution + blocked-root check
    of the resolved path is the fix."""
    # Create a symlink in tmp_path pointing into ~/.codec/skills/
    target = os.path.join(CODEC_DIR, "skills")
    link = tmp_path / "sneaky"
    try:
        os.symlink(target, str(link))
    except OSError:
        # Some filesystems don't support symlinks; skip in that case
        import pytest
        pytest.skip("filesystem does not support symlinks")

    # Now try to write through the symlink
    safe, reason = file_write._is_safe_target(
        os.path.join(str(link), "backdoor.py")
    )
    assert not safe, (
        "Symlink into ~/.codec/skills/ must be resolved by realpath and refused"
    )


# ── Tests that must STILL ACCEPT (no regression on legitimate paths) ──────────


def test_accepts_documents_dir():
    """The main legitimate write target for claude.ai's file_write usage."""
    safe, reason = file_write._is_safe_target(
        os.path.join(HOME, "Documents", "notes", "plan.md")
    )
    assert safe, f"~/Documents must remain writable (D-4 regression): {reason}"


def test_accepts_desktop_dir():
    safe, reason = file_write._is_safe_target(
        os.path.join(HOME, "Desktop", "scratch.txt")
    )
    assert safe, f"~/Desktop must remain writable: {reason}"


def test_accepts_tmp_dir():
    safe, reason = file_write._is_safe_target("/tmp/codec-tmp.log")
    assert safe, f"/tmp must remain writable: {reason}"


def test_accepts_arbitrary_home_subdir(tmp_path, monkeypatch):
    """Writes to arbitrary user subdirs under $HOME (e.g. ~/Projects/foo.md)
    should still work — that's the entire utility of the skill."""
    # Use the real $HOME so the existing `under_home` guard is exercised.
    safe, reason = file_write._is_safe_target(
        os.path.join(HOME, "Projects", "test-pr1c-only.md")
    )
    assert safe, f"~/Projects must remain writable: {reason}"


def test_accepts_codec_workspace_subdir():
    """~/codec-workspace is used by the Vibe doSave() flow — must stay open."""
    safe, reason = file_write._is_safe_target(
        os.path.join(HOME, "codec-workspace", "snippet.py")
    )
    assert safe, f"~/codec-workspace must remain writable: {reason}"


# ── Existing protections still active (regression checks) ─────────────────────


def test_refuses_etc_passwd():
    """Pre-existing /etc block must still work."""
    safe, _ = file_write._is_safe_target("/etc/passwd")
    assert not safe


def test_refuses_ssh_key():
    """Pre-existing .ssh/ filename pattern block must still work."""
    safe, _ = file_write._is_safe_target(os.path.join(HOME, ".ssh", "id_rsa"))
    assert not safe


def test_refuses_env_file():
    """Pre-existing .env filename pattern block must still work."""
    safe, _ = file_write._is_safe_target(os.path.join(HOME, "project", ".env"))
    assert not safe


# ── Audit emission on blocked write (D-4 closure §3) ──────────────────────────


def test_blocked_write_emits_file_write_blocked_audit_event(monkeypatch):
    """When file_write refuses a target, it must emit an audit event so the
    operator can grep ~/.codec/audit.log for attempted writes to sensitive
    paths."""
    captured = []

    def fake_log_event(event_type, *args, **kwargs):
        captured.append({
            "event_type": event_type,
            "args": args,
            "kwargs": kwargs,
        })

    monkeypatch.setattr("codec_audit.log_event", fake_log_event)

    result = file_write.run(
        task=f"path: {os.path.join(CODEC_DIR, 'skills', 'attempt.py')}\n"
             "content: print('payload')"
    )

    assert result.startswith("file_write: refused"), (
        f"Expected refusal message, got: {result!r}"
    )
    matching = [c for c in captured if c["event_type"] == "file_write_blocked"]
    assert len(matching) == 1, (
        f"Expected exactly one file_write_blocked audit event, "
        f"got {len(matching)}: {captured}"
    )
    extra = matching[0]["kwargs"].get("extra", {})
    assert "target_path" in extra
    assert "reason" in extra
    assert "skills" in extra["target_path"], (
        f"target_path should reference the attempted sensitive path: {extra}"
    )


# ── sensitive-dir hardening (2026-07): file_write is now exposed to remote MCP
# callers, so a write INTO ~/.ssh (basename doesn't match the .ssh pattern) must
# be blocked at the directory level, not just by filename. ──
import importlib as _il
import file_write as _fw
_il.reload(_fw)


@pytest.mark.parametrize("path", [
    "~/.ssh/authorized_keys",   # SSH key injection
    "~/.ssh/config",
    "~/.ssh/random",
    "~/.aws/credentials",
    "~/.gnupg/anything",
    "~/.kube/config",
    "~/.gcloud/creds",
    "~/.config/gcloud/token",
    "~/.zshrc",                 # code exec on next shell
    "~/.bashrc",
    "~/.bash_profile",
    "~/.zshenv",
])
def test_sensitive_targets_refused(path):
    out = _fw.run(f"save to {path} content: pwned")
    assert "refused" in out.lower(), f"{path} was NOT blocked: {out}"


@pytest.mark.parametrize("path", [
    "~/Downloads/note.txt",
    "~/Desktop/scratch.md",
    "~/Documents/plan.md",
])
def test_safe_targets_still_allowed(tmp_path, path):
    import os
    out = _fw.run(f"save to {path} content: ok-{os.getpid()}")
    p = os.path.expanduser(path)
    try:
        assert "saved" in out.lower() and os.path.exists(p), out
    finally:
        if os.path.exists(p):
            os.remove(p)
