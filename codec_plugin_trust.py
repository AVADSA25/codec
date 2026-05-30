"""CODEC Plugin Trust Store — SHA-256 allowlist for ~/.codec/plugins/*.py.

B6-P1 / SR-32: extracted from codec_hooks.py. The trust-store layer is
purely stateless data — allowlist read/write, hash compute, refusal
audit. The runtime layer (lifecycle dispatcher, PluginRegistry, hook
fire orchestration, approve_plugin operator entry) stays in codec_hooks.

Why split: codec_hooks.py was 1,097 LOC doing two clearly separable
concerns: (a) a 5-event lifecycle dispatcher with mutation/veto
semantics, and (b) a SHA-256 allowlist trust store. The two move at
different speeds (lifecycle is contract-stable, the trust store gets
new audit fields per audit pass) and a future reader wants to know
which subsystem each change touches.

Back-compat: codec_hooks re-exports everything below as private members
so any test or external caller doing `codec_hooks._read_allowlist` (or
similar) keeps working without changes.

See `docs/PHASE1-STEP2-DESIGN.md` for the PR-2F (D-18) trust-model
design that introduced these primitives.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import stat
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from codec_audit import log_event as _log_event

log = logging.getLogger("codec_plugin_trust")

# ── Storage ────────────────────────────────────────────────────────────────────
_PLUGINS_DIR_DEFAULT = os.path.expanduser("~/.codec/plugins")
_PLUGINS_ALLOWLIST_DEFAULT = os.path.expanduser("~/.codec/plugins.allowlist")

# Default identifier suffix used when a plugin omits PLUGIN_NAME — the
# file stem. Lives here because allowlist keys + grandfather migration
# both reference it; codec_hooks re-exports for back-compat.
_PLUGIN_FILE_SUFFIX = ".py"

# Cross-thread serialization of allowlist writes. approve_plugin grabs
# this BEFORE read-modify-write so two concurrent approvals can't lose
# each other's changes. Single-process scope (codec-dashboard is the
# only writer); the read path is lock-free.
_ALLOWLIST_LOCK = threading.Lock()


def _default_allowlist_path_for(plugins_dir: str) -> str:
    """Derive the allowlist path from the plugins dir. Production:
    `~/.codec/plugins/` → `~/.codec/plugins.allowlist`. Tests pointing
    at a tmp plugins dir get a sibling allowlist in the same tmp tree,
    so they don't touch the operator's real allowlist file."""
    parent = os.path.dirname(os.path.abspath(plugins_dir.rstrip(os.sep)))
    return os.path.join(parent, "plugins.allowlist")


def _allowlist_path_for(plugins_dir: str) -> Path:
    """Resolved allowlist path for a given plugins dir."""
    return Path(_default_allowlist_path_for(plugins_dir))


def _read_allowlist(path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """Load the allowlist as a dict keyed by plugin filename. Returns {} on
    any error (missing file, parse error, wrong shape) — fail-closed: no
    file = no allowed plugins."""
    p = path if path is not None else Path(_PLUGINS_ALLOWLIST_DEFAULT)
    if not p.exists():
        return {}
    try:
        raw = p.read_text(encoding="utf-8")
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            log.warning("[plugins] allowlist root is not a dict; treating as empty")
            return {}
        # Validate shape — each entry must have a sha256 hex string.
        clean: Dict[str, Dict[str, Any]] = {}
        for fname, entry in obj.items():
            if not isinstance(entry, dict):
                continue
            h = entry.get("sha256")
            if isinstance(h, str) and len(h) == 64 and all(c in "0123456789abcdef" for c in h.lower()):
                clean[fname] = entry
        return clean
    except (OSError, json.JSONDecodeError) as e:
        log.warning("[plugins] allowlist read failed: %s", e)
        return {}


def _write_allowlist(allowlist: Dict[str, Dict[str, Any]],
                     path: Optional[Path] = None) -> bool:
    """Atomic-write the allowlist with 0600 perms. Returns True on success."""
    p = path if path is not None else Path(_PLUGINS_ALLOWLIST_DEFAULT)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(allowlist, indent=2, sort_keys=True),
                       encoding="utf-8")
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp, p)
        try:
            os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        return True
    except OSError as e:
        log.warning("[plugins] allowlist write failed: %s", e)
        return False


def _file_sha256(path: str) -> Optional[str]:
    """SHA-256 hex digest of file contents. None on read error."""
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def _maybe_grandfather_existing_plugins(plugins_dir: str,
                                          allowlist_path: Optional[Path] = None) -> None:
    """One-shot migration: if no allowlist file exists yet AND the plugins
    dir has .py files, write their current hashes to the allowlist with
    `approved_by: "initial_migration"`. Idempotent — runs once at the
    upgrade boundary, then becomes a no-op.

    `allowlist_path` defaults to the sibling of `plugins_dir` (i.e.
    `<dirname(plugins_dir)>/plugins.allowlist`) so tests pointing at a
    tmp plugins dir get a tmp allowlist, not the real one."""
    if allowlist_path is None:
        allowlist_path = _allowlist_path_for(plugins_dir)
    if allowlist_path.exists():
        return
    if not os.path.isdir(plugins_dir):
        return
    pys = [f for f in os.listdir(plugins_dir)
           if f.endswith(_PLUGIN_FILE_SUFFIX) and not f.startswith("_")]
    if not pys:
        # No plugins to grandfather — still create an empty allowlist so
        # subsequent loads don't re-attempt migration on every restart.
        _write_allowlist({}, allowlist_path)
        return
    seed: Dict[str, Dict[str, Any]] = {}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for fname in pys:
        fpath = os.path.join(plugins_dir, fname)
        h = _file_sha256(fpath)
        if h is None:
            continue
        seed[fname] = {
            "sha256": h,
            "approved_at": now,
            "approved_by": "initial_migration",
        }
    if _write_allowlist(seed, allowlist_path):
        log.info("[plugins] grandfathered %d existing plugin(s) into allowlist", len(seed))
        try:
            _log_event(
                "plugin_allowlist_migrated", "codec-plugin-trust",
                f"grandfathered {len(seed)} plugin(s)",
                extra={"plugin_count": len(seed), "filenames": sorted(seed.keys())},
                level="info", outcome="ok",
            )
        except Exception:
            pass


def _is_plugin_allowed(filepath: str,
                       allowlist_path: Optional[Path] = None) -> tuple[bool, str]:
    """Return (allowed, reason). `allowed` True only if the file's current
    SHA-256 matches an entry in the allowlist keyed by basename. Tamper
    detection: a previously-approved plugin whose content changed will
    have a hash mismatch and be refused until re-approved."""
    if allowlist_path is None:
        allowlist_path = _allowlist_path_for(os.path.dirname(filepath))
    fname = os.path.basename(filepath)
    h = _file_sha256(filepath)
    if h is None:
        return False, "file_unreadable"
    allowlist = _read_allowlist(allowlist_path)
    entry = allowlist.get(fname)
    if entry is None:
        return False, "not_in_allowlist"
    if entry.get("sha256") != h:
        return False, "hash_mismatch"
    return True, ""


def _emit_plugin_load_blocked(plugin_name: str, filepath: str,
                              reason: str, extra_detail: str = "") -> None:
    """Audit emit for plugin load refusal. Fire-and-forget."""
    extra: Dict[str, Any] = {
        "plugin_name": plugin_name,
        "plugin_path": filepath,
        "reason": reason,
    }
    if extra_detail:
        extra["detail"] = extra_detail[:200]
    try:
        _log_event(
            "plugin_load_blocked", "codec-plugin-trust",
            f"Plugin {plugin_name!r} refused: {reason}",
            extra=extra,
            level="warning", outcome="error",
        )
    except Exception:
        pass
