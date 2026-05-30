"""
CODEC Pilot — Phase 5: Skill Approval Gate
============================================

Compiled skills do NOT auto-register with CODEC's SkillRegistry. They land in:

    ~/.codec/skills/.pending/pilot_{slug}.py

The dashboard lists pending skills. The user reviews each one (read the
code, edit if desired) and either approves it → moves to:

    ~/.codec/skills/pilot_{slug}.py

…or rejects it (file deleted).

This protects against prompt-injection-spawned malicious skills auto-
registering. The blueprint mandates this gate.

Usage:
    from pilot.skill_review import (
        save_pending, list_pending, get_pending,
        approve_pending, reject_pending,
    )

    save_pending("hn_top_stories", "<python source>")
    print(list_pending())   # [{slug, path, mtime, size}]
    approve_pending("hn_top_stories")
"""

from __future__ import annotations

import re
import shutil
import time
from pathlib import Path
from typing import Optional

from .audit import audit  # PP-8 (P-12): forensic trail for skill writes
from .safety import is_dangerous_skill_code  # PP-11 (P-3): AST gate at approve-time


# ─── Paths ────────────────────────────────────────────────────────────────────

SKILLS_DIR         = Path.home() / ".codec" / "skills"
SKILLS_PENDING_DIR = SKILLS_DIR / ".pending"


def _ensure_dirs() -> None:
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    SKILLS_PENDING_DIR.mkdir(parents=True, exist_ok=True)


# ─── Slug helpers ─────────────────────────────────────────────────────────────

_SLUG_RE = re.compile(r"[^a-z0-9_]+")

def slugify(text: str, max_len: int = 40) -> str:
    """
    Make a filesystem-safe slug from a free-form task description.

        "Find top 5 HN stories" → "find_top_5_hn_stories"
    """
    s = (text or "").lower().strip()
    s = _SLUG_RE.sub("_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:max_len] or f"pilot_{int(time.time())}"


def _filename(slug: str) -> str:
    return f"pilot_{slug}.py"


# ─── Save / list / approve / reject ───────────────────────────────────────────

def save_pending(slug: str, source: str) -> Path:
    """
    Write a compiled skill to the pending directory.
    Returns the absolute path.
    """
    _ensure_dirs()
    slug = slugify(slug)
    path = SKILLS_PENDING_DIR / _filename(slug)

    # If file exists, append a numeric suffix to avoid silent overwrite.
    if path.exists():
        i = 2
        while (SKILLS_PENDING_DIR / f"pilot_{slug}_{i}.py").exists():
            i += 1
        path = SKILLS_PENDING_DIR / f"pilot_{slug}_{i}.py"

    path.write_text(source, encoding="utf-8")
    return path


def list_pending() -> list[dict]:
    """
    Return summary dicts for every pending skill, newest first.
    Shape: {slug, filename, path, size_bytes, mtime, head_doc}
    """
    _ensure_dirs()
    out: list[dict] = []
    for p in sorted(SKILLS_PENDING_DIR.glob("pilot_*.py"),
                    key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            text = ""
        head = _head_doc(text)
        out.append({
            "slug":       p.stem.replace("pilot_", "", 1),
            "filename":   p.name,
            "path":       str(p),
            "size_bytes": p.stat().st_size,
            "mtime":      p.stat().st_mtime,
            "head_doc":   head,
        })
    return out


def list_active() -> list[dict]:
    """Return summary dicts for approved (active) pilot skills."""
    _ensure_dirs()
    out: list[dict] = []
    for p in sorted(SKILLS_DIR.glob("pilot_*.py"),
                    key=lambda f: f.stat().st_mtime, reverse=True):
        # Don't include files inside .pending/
        if SKILLS_PENDING_DIR in p.parents:
            continue
        out.append({
            "slug":       p.stem.replace("pilot_", "", 1),
            "filename":   p.name,
            "path":       str(p),
            "size_bytes": p.stat().st_size,
            "mtime":      p.stat().st_mtime,
        })
    return out


def get_pending(slug: str) -> Optional[dict]:
    """Read the full source of one pending skill."""
    _ensure_dirs()
    slug = slugify(slug)  # P-11: neutralize path/glob traversal in the lookup slug
    path = SKILLS_PENDING_DIR / _filename(slug)
    if not path.exists():
        # Try fuzzy match (handles _2 suffixes)
        candidates = list(SKILLS_PENDING_DIR.glob(f"pilot_{slug}*.py"))
        if not candidates:
            return None
        path = candidates[0]
    return {
        "slug":     path.stem.replace("pilot_", "", 1),
        "filename": path.name,
        "path":     str(path),
        "source":   path.read_text(encoding="utf-8"),
        "mtime":    path.stat().st_mtime,
    }


def approve_pending(slug: str, replace_existing: bool = True) -> Path:
    """
    Move pending skill to the active directory.
    Returns the new active path. Raises FileNotFoundError if no match.
    """
    _ensure_dirs()
    slug = slugify(slug)  # P-11: neutralize path/glob traversal in the lookup slug
    src = SKILLS_PENDING_DIR / _filename(slug)
    if not src.exists():
        candidates = list(SKILLS_PENDING_DIR.glob(f"pilot_{slug}*.py"))
        if not candidates:
            raise FileNotFoundError(f"No pending skill matches '{slug}'")
        src = candidates[0]

    # PP-11 (P-3): AST safety gate BEFORE the file ever reaches the active dir.
    # Defense in depth — PP-2 stops the compiler emitting injected code, and the
    # parent SkillRegistry AST-checks at load; this fails fast at approve so a
    # dangerous file never lands in ~/.codec/skills/. The pending file is left
    # in place (not deleted) so the operator can inspect what was refused.
    try:
        source = src.read_text(encoding="utf-8")
    except Exception as e:
        audit("skill_blocked", slug=slug, reason=f"unreadable: {e}")
        raise PermissionError(f"Cannot read pending skill '{slug}': {e}") from e
    dangerous, reason = is_dangerous_skill_code(source)
    if dangerous:
        audit("skill_blocked", slug=slug, reason=reason, path=str(src))
        raise PermissionError(f"Refusing to approve dangerous skill '{slug}': {reason}")

    dst = SKILLS_DIR / src.name
    if dst.exists() and not replace_existing:
        i = 2
        while (SKILLS_DIR / f"{dst.stem}_{i}.py").exists():
            i += 1
        dst = SKILLS_DIR / f"{dst.stem}_{i}.py"

    shutil.move(str(src), str(dst))
    audit("skill_approved", slug=slug, path=str(dst))  # P-12
    return dst


def reject_pending(slug: str) -> bool:
    """Delete a pending skill. Returns True if a file was deleted."""
    _ensure_dirs()
    slug = slugify(slug)  # P-11: neutralize path/glob traversal in the lookup slug
    src = SKILLS_PENDING_DIR / _filename(slug)
    candidates = [src] if src.exists() else list(SKILLS_PENDING_DIR.glob(f"pilot_{slug}*.py"))
    deleted = False
    for c in candidates:
        try:
            c.unlink()
            deleted = True
        except FileNotFoundError:
            pass
    if deleted:
        audit("skill_rejected", slug=slug)  # P-12
    return deleted


# ─── Small helpers ────────────────────────────────────────────────────────────

def _head_doc(source: str) -> str:
    """Extract first docstring or the first 3 non-blank lines for preview."""
    if not source:
        return ""
    # Triple-quoted at file head
    m = re.match(r'\s*"""(.*?)"""', source, re.S)
    if m:
        return m.group(1).strip()[:400]
    lines = [ln for ln in source.splitlines() if ln.strip()][:3]
    return "\n".join(lines)[:400]
