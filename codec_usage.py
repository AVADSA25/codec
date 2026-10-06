"""Usage numbers for Settings > Usage (UI phase 3, P3.11; docs/P3.11-DESIGN.md).

One line per chat or voice reply goes to ~/.codec/reply_stats.jsonl (0600):
when, which model, local or cloud, and for chat its tokens and tokens per
second. Never the question, the answer or anything else said. The file keeps
about the last 30 days. Reading it gives the local against cloud reply counts
and the speed over time; the busiest skills come from the audit log
(codec_audit.get_stats with a 7-day window).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from typing import Any, Dict, Optional

log = logging.getLogger("codec.usage")

REPLY_LOG = os.path.expanduser("~/.codec/reply_stats.jsonl")
KEEP_DAYS = 30
MAX_BYTES = 2_000_000  # trimmed to KEEP_DAYS when it grows past this
_LOCK = threading.Lock()


def _is_cloud(base_url: str) -> bool:
    try:
        import codec_cloud_models
        return not codec_cloud_models.is_local_url(base_url or "")
    except Exception:
        return False


def _num(v) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def record_reply(model: str, base_url: str, stats: Optional[Dict[str, Any]] = None, *,
                 source: str = "chat", now: Optional[float] = None) -> None:
    """Append one reply's line. Never raises: usage numbers must not break a reply."""
    try:
        s = stats if isinstance(stats, dict) else {}
        line = {"ts": round(now if now is not None else time.time(), 3), "source": source,
                "model": str(model or "")[:200], "cloud": _is_cloud(base_url)}
        for key, out in (("completion_tokens", "tokens"), ("tok_per_s", "tok_s"), ("elapsed_s", "elapsed_s")):
            v = _num(s.get(key))
            if v is not None:
                line[out] = int(v) if out == "tokens" else v
        data = (json.dumps(line, separators=(",", ":")) + "\n").encode("utf-8")
        os.makedirs(os.path.dirname(REPLY_LOG), exist_ok=True)
        with _LOCK:
            fd = os.open(REPLY_LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, data)  # one write of one short line: appends from two processes do not mix
                size = os.fstat(fd).st_size
            finally:
                os.close(fd)
            if size > MAX_BYTES:
                _trim(line["ts"])
    except Exception as e:
        log.debug("reply stats not written: %s", e)


def _trim(now: float) -> None:
    cutoff = now - KEEP_DAYS * 86400
    keep = [ln for ln in _lines() if (_num(ln.get("ts")) or 0) >= cutoff]
    tmp = REPLY_LOG + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for ln in keep:
            f.write(json.dumps(ln, separators=(",", ":")) + "\n")
    os.replace(tmp, REPLY_LOG)


def _lines():
    try:
        with open(REPLY_LOG, encoding="utf-8") as f:
            for raw in f:
                try:
                    d = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(d, dict):
                    yield d
    except OSError:
        return


def reply_summary(days: int = 7, speed_days: int = 14, now: Optional[float] = None) -> Dict[str, Any]:
    """Local against cloud replies over `days`, and the average speed per model and
    day over `speed_days` (replies that reported a speed)."""
    now = now if now is not None else time.time()
    cut, speed_cut = now - days * 86400, now - speed_days * 86400
    counts = {"local": 0, "cloud": 0}
    by_source: Dict[str, int] = {}
    speed: Dict[tuple, list] = {}
    first = None
    for ln in _lines():
        ts = _num(ln.get("ts"))
        if ts is None:
            continue
        first = ts if first is None else min(first, ts)
        if ts >= cut:
            counts["cloud" if ln.get("cloud") else "local"] += 1
            src = str(ln.get("source") or "chat")
            by_source[src] = by_source.get(src, 0) + 1
        tok_s = _num(ln.get("tok_s"))
        if ts >= speed_cut and tok_s:
            day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            speed.setdefault((day, str(ln.get("model") or ""), bool(ln.get("cloud"))), []).append(tok_s)
    rows = [{"day": d, "model": m, "cloud": c, "tok_s": round(sum(v) / len(v), 1), "replies": len(v)}
            for (d, m, c), v in sorted(speed.items())]
    return {"days": days, "replies": counts, "by_source": by_source, "speed": rows,
            "since": datetime.fromtimestamp(first).isoformat(timespec="seconds") if first else None}


def busiest_skills(days: int = 7, top: int = 8) -> list:
    """The skills that ran most often over `days`, from the audit log."""
    try:
        import codec_audit
        by_tool = codec_audit.get_stats(hours=days * 24).get("by_tool") or {}
    except Exception:
        return []
    return [{"name": k, "count": v} for k, v in sorted(by_tool.items(), key=lambda kv: (-kv[1], kv[0]))[:top]]
