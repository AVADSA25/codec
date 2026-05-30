"""Pilot PP-8 (audit P-12): minimal forensic audit trail.

Pilot was invisible to any audit log — navigation of logged-in sites, typing, and
skill writes left no record. This appends JSON lines to ~/.codec/pilot_audit.log
(0600). It is deliberately a SEPARATE log from the parent's ~/.codec/audit.log so
it doesn't need the parent's HMAC signing (PR-2E) or cross-process flock (PR-4E) —
Pilot is a separate repo and can't cleanly import codec_audit. Never raises.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

_AUDIT_PATH = Path.home() / ".codec" / "pilot_audit.log"


def audit(event: str, **fields) -> None:
    """Append one forensic JSON line {ts, source, event, **fields}. Never raises."""
    try:
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z") or str(time.time()),
            "source": "pilot",
            "event": event,
            **fields,
        }
        _AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(_AUDIT_PATH), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
    except Exception:
        pass
