"""Structured JSON audit log for CODEC MCP calls.

One JSON line per tool invocation to ~/.codec/audit.log. Cheap, greppable,
machine-readable forensics. Daily rotation (keep last 30 days).

Schema:
    {"ts": ISO8601, "tool": str, "task_len": int, "context_len": int,
     "duration_ms": float, "outcome": "ok"|"error"|"validation"|"timeout",
     "error_type": str | null, "client_id": str | null, "transport": str}
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

_AUDIT_DIR = Path(os.path.expanduser("~/.codec"))
_AUDIT_DIR.mkdir(parents=True, exist_ok=True)
_AUDIT_LOG = _AUDIT_DIR / "audit.log"
_LOCK = threading.Lock()
_RETAIN_DAYS = 30


def _rotate_if_needed():
    """Rotate audit.log daily. Keep .log.YYYY-MM-DD files, prune >30d."""
    if not _AUDIT_LOG.exists():
        return
    mtime_day = datetime.fromtimestamp(_AUDIT_LOG.stat().st_mtime, timezone.utc).date()
    today = datetime.now(timezone.utc).date()
    if mtime_day >= today:
        return
    rotated = _AUDIT_DIR / f"audit.log.{mtime_day.isoformat()}"
    try:
        _AUDIT_LOG.rename(rotated)
    except OSError:
        return
    cutoff = time.time() - _RETAIN_DAYS * 86400
    for p in _AUDIT_DIR.glob("audit.log.*"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass


def audit(
    tool: str,
    *,
    task_len: int = 0,
    context_len: int = 0,
    duration_ms: float | None = None,
    outcome: str = "ok",
    error_type: str | None = None,
    client_id: str | None = None,
    transport: str | None = None,
    extra: dict | None = None,
) -> None:
    """Write one structured audit line. Never raises."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "tool": tool,
        "task_len": task_len,
        "context_len": context_len,
        "duration_ms": round(duration_ms, 2) if duration_ms is not None else None,
        "outcome": outcome,
        "error_type": error_type,
        "client_id": client_id,
        "transport": transport or os.environ.get("CODEC_MCP_TRANSPORT", "stdio"),
    }
    if extra:
        record.update(extra)
    line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
    try:
        with _LOCK:
            _rotate_if_needed()
            with open(_AUDIT_LOG, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass
