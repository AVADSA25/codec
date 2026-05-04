"""Pytest configuration.

Path setup: when running from a git worktree (e.g. .claude/worktrees/<branch>),
make sure the worktree's repo dir wins over ~/codec-repo so local codec_*.py
gets imported, not the main checkout. Without this, a worktree's Step 3
codec_audit (with new ASKUSER_EVENT_* constants) gets shadowed by main's
older copy and tests that rely on the new constants fail.
"""
import sys
import os
from pathlib import Path

# Worktree's repo root = parent of `tests/`. When running from main repo
# directly, this resolves to ~/codec-repo (same as the next insert) — harmless.
_WORKTREE_REPO = Path(__file__).resolve().parent.parent

# Order matters: insert(0) prepends. Last insert wins → worktree at sys.path[0].
sys.path.insert(0, os.path.expanduser("~/codec-repo"))
sys.path.insert(0, os.path.expanduser("~/.codec/skills"))
sys.path.insert(0, str(_WORKTREE_REPO))
