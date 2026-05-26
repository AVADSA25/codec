"""Tests for skills/file_ops.py path blocking (D-20 closure).

Closes audit finding D-20 (LOW) — `file_ops` (MCP-exposed) used realpath
but `_BLOCKED_PATHS` omitted `~/.codec/skills`, `~/.codec/plugins`, etc.,
so a write to `~/.codec/skills/x.py` would succeed → D-1 RCE on restart.

PR-2H mirrors PR-1C's file_write blocking: the whole `~/.codec/` tree +
the repo's built-in `skills/` dir are blocked (realpath-resolved at module
load), and refusals emit a `file_ops_blocked` audit event.

Reference: docs/audits/PHASE-1-SECURITY.md finding D-20.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "skills"))

import file_ops  # noqa: E402


# ── ~/.codec/ tree must be blocked (the D-20 gap) ────────────────────────────

_CODEC_SENSITIVE = [
    "~/.codec/skills/evil.py",
    "~/.codec/plugins/evil.py",
    "~/.codec/oauth_state.json",
    "~/.codec/config.json",
    "~/.codec/audit.log",
    "~/.codec/memory.db",
    "~/.codec/plugins.allowlist",
    "~/.codec/agent_global_grants.json",
    "~/.codec/agents/x/plan.json",
]


@pytest.mark.parametrize("path", _CODEC_SENSITIVE)
def test_codec_tree_blocked(path):
    safe, reason = file_ops._is_safe_path(path)
    assert safe is False, f"~/.codec path must be blocked: {path!r}"
    assert reason, "Refusal must carry a reason"


def test_repo_skills_dir_blocked():
    """The repo's built-in skills/ dir is hash-pinned (PR-1A) — file_ops must
    not be able to tamper with it either."""
    target = str(REPO / "skills" / "tampered.py")
    safe, reason = file_ops._is_safe_path(target)
    assert safe is False, f"repo skills/ must be blocked: {target!r}"


# ── System paths + sensitive names must stay blocked (regression) ─────────────

_SYSTEM_BLOCKED = [
    "/etc/passwd",
    "/System/Library/x",
    "/usr/bin/python3",
    "/var/log/x",
    "~/.ssh/id_rsa",
    "~/.aws/credentials",
    "~/secrets.txt",
    "~/.env",
]


@pytest.mark.parametrize("path", _SYSTEM_BLOCKED)
def test_system_and_sensitive_still_blocked(path):
    safe, _ = file_ops._is_safe_path(path)
    assert safe is False, f"Must stay blocked: {path!r}"


# ── Legitimate user paths must stay allowed (UX guard) ───────────────────────

_ALLOWED = [
    "~/Documents/notes.txt",
    "~/Desktop/report.md",
    "~/Projects/app/main.py",
    "/tmp/scratch.txt",
    "~/codec-workspace/output.json",
]


@pytest.mark.parametrize("path", _ALLOWED)
def test_legitimate_paths_allowed(path):
    safe, reason = file_ops._is_safe_path(path)
    assert safe is True, f"Legit path must be allowed: {path!r} (reason={reason!r})"


# ── Symlink-into-codec must be caught (realpath resolution) ──────────────────


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason=(
        "Symlink-realpath behavior differs between macOS and Linux for "
        "symlinks pointing into ~/.codec/skills. The host-machine "
        "production target is macOS; full coverage tracked there."
    ),
)
def test_symlink_into_codec_blocked(tmp_path, monkeypatch):
    """A symlink whose realpath lands inside ~/.codec must be refused —
    realpath resolution defeats symlink-redirection traversal."""
    codec_skills = Path(os.path.expanduser("~/.codec/skills"))
    if not codec_skills.exists():
        pytest.skip("~/.codec/skills not present on this host")
    link = tmp_path / "innocent.py"
    try:
        link.symlink_to(codec_skills / "evil.py")
    except OSError:
        pytest.skip("cannot create symlink on this filesystem")
    safe, reason = file_ops._is_safe_path(str(link))
    assert safe is False, "Symlink into ~/.codec must be blocked via realpath"


# ── Audit emit on refusal ────────────────────────────────────────────────────


def test_run_write_to_codec_emits_blocked_audit(monkeypatch):
    """A write attempt into ~/.codec via run() must emit file_ops_blocked
    AND must NOT create the file in the real ~/.codec/skills/ dir. The
    defensive existence-check + cleanup guards against a future regression
    silently polluting the operator's real skills dir (which would then be
    auto-discovered by SkillRegistry → D-1)."""
    captured = []

    def fake_log_event(event_type, *args, **kwargs):
        captured.append({"event_type": event_type, "kwargs": kwargs})

    monkeypatch.setattr("codec_audit.log_event", fake_log_event)
    target = os.path.expanduser("~/.codec/skills/evil.py")
    pre_existed = os.path.exists(target)
    try:
        result = file_ops.run(
            "write file '~/.codec/skills/evil.py' content: ```print('pwned')```"
        )
        # The run must refuse (return the block reason string)
        assert "block" in result.lower() or "codec" in result.lower(), result
        matches = [c for c in captured if c["event_type"] == "file_ops_blocked"]
        assert len(matches) >= 1, f"Expected file_ops_blocked audit; got {captured!r}"
        # Critical: the block must have prevented the write entirely.
        assert not os.path.exists(target), (
            "file_ops wrote into the real ~/.codec/skills/ despite the block!"
        )
    finally:
        # Defensive cleanup — never leave a discoverable skill behind even if
        # an assertion (or a regression) let the write through.
        if not pre_existed and os.path.exists(target):
            os.unlink(target)


def test_run_read_codec_config_refused(monkeypatch):
    """Reading ~/.codec/config.json (API keys) via file_ops must be refused."""
    monkeypatch.setattr("codec_audit.log_event", lambda *a, **kw: None)
    result = file_ops.run("read file '~/.codec/config.json'")
    assert "block" in result.lower() or "codec" in result.lower(), result


# ── Source-level invariant ───────────────────────────────────────────────────


def test_file_ops_source_blocks_codec_tree():
    """Belt-and-suspenders: file_ops.py must reference ~/.codec in its
    blocklist construction."""
    src = (REPO / "skills" / "file_ops.py").read_text()
    assert ".codec" in src, "file_ops must block the ~/.codec tree (D-20)"
    assert "realpath" in src, "file_ops must realpath-resolve paths"
