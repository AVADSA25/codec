"""codec_health: daemon health beats (P-017, docs/HEALTH-BEATS-DESIGN.md).

PM2 shows a bridge "online" while every round of its loop fails, or while it hangs inside a call. Each
loop round of codec-imessage, codec-telegram and codec-autopilot calls `beat()` once; the heartbeat
(inside codec-dashboard) reads the folder and alerts the owner when a beat is older than 3x its cadence
(never less than 15 minutes) or when 20 rounds in a row ended in an error. The observer gets no call: its
beat is the newest `observation_tick` line in the audit log (`observer_beat`).

One file per daemon, `<HEALTH_DIR>/<name>.json`, written atomically (0600, folder 0700):
    {"ts": epoch seconds, "ok": bool, "consecutive_errors": int, "last_error_class": str | None,
     "cadence_s": seconds, "stopped": bool}
Names, times, counts and an exception class name only: never message text, senders or paths.
`beat()` never raises and writes at most once a minute unless what the file says changes.
Stdlib only (`codec_jsonstore` is stdlib too), so any daemon imports it without an import cycle.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime
from typing import Any, Optional

HEALTH_DIR = os.environ.get("CODEC_HEALTH_DIR") or os.path.expanduser("~/.codec/health")
WRITE_EVERY_S = 60       # a beat file is rewritten at most this often unless what it says changes
STALE_FACTOR = 3         # silent: older than 3x the cadence ...
STALE_MIN_S = 15 * 60    # ... and never less than 15 minutes (the heartbeat looks every 30)
FAILING_AFTER = 20       # failing: this many rounds in a row ended in an error
AUDIT_TAIL_BYTES = 2_000_000             # how much of audit.log the observer check reads (about 4 days)
OBSERVER_EVENTS = ("observation_tick", "observation_tick_slow")
OBSERVER_MAX_CADENCE_S = 300             # its idle cadence, for a tail without any tick

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_NOT_CLASS = re.compile(r"[^A-Za-z0-9_.]")
_lock = threading.Lock()
_rounds: dict = {}  # name -> {"errors", "last_class", "written_at", "said"}; this process only


def path_for(name: str) -> str:
    """The beat file of `name`; a name that is not a plain service name raises ValueError."""
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ValueError(f"not a service name: {name!r}")
    return os.path.join(HEALTH_DIR, name + ".json")


def error_class(error: Any) -> Optional[str]:
    """A short class name for a failed round (an exception, a class or a name), None for no error."""
    if error is None or error is False:
        return None
    if isinstance(error, BaseException):
        text = type(error).__name__
    elif isinstance(error, type):
        text = error.__name__
    else:
        text = str(error)
    return _NOT_CLASS.sub("", text)[:80] or "Error"


def _write(name: str, data: dict) -> None:
    import codec_jsonstore
    path = path_for(name)
    os.makedirs(HEALTH_DIR, mode=0o700, exist_ok=True)
    codec_jsonstore.atomic_write_json(path, data)


def beat(name: str, *, cadence_s: float, error: Any = None, now: Optional[float] = None) -> bool:
    """One loop round of `name` ended, failed when `error` is set. Returns True when the file was
    written. Never raises: a health write must not break a bridge."""
    try:
        path_for(name)  # a bad name writes nothing and keeps no count
        now = time.time() if now is None else float(now)
        cls = error_class(error)
        cadence = max(float(cadence_s), float(WRITE_EVERY_S))
        with _lock:
            r = _rounds.setdefault(name, {"errors": 0, "last_class": None, "written_at": None, "said": None})
            if cls:
                r["errors"] += 1
                r["last_class"] = cls
            else:
                r["errors"] = 0
            said = (cls is None, r["last_class"], cadence, r["errors"] >= FAILING_AFTER)
            if r["written_at"] is not None and now - r["written_at"] < WRITE_EVERY_S and said == r["said"]:
                return False
            _write(name, {"ts": now, "ok": cls is None, "consecutive_errors": r["errors"],
                          "last_error_class": r["last_class"], "cadence_s": cadence, "stopped": False})
            r["written_at"], r["said"] = now, said
            return True
    except Exception:
        return False


def stopped(name: str, now: Optional[float] = None) -> bool:
    """The daemon stops on purpose (its shutdown cleanup): the heartbeat no longer checks it.
    Never raises."""
    try:
        now = time.time() if now is None else float(now)
        with _lock:
            r = _rounds.get(name) or {"errors": 0, "last_class": None}
            _write(name, {"ts": now, "ok": r["errors"] == 0, "consecutive_errors": r["errors"],
                          "last_error_class": r["last_class"], "cadence_s": float(WRITE_EVERY_S),
                          "stopped": True})
            _rounds.pop(name, None)
        return True
    except Exception:
        return False


def _num(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def read_beats(folder: Optional[str] = None) -> dict:
    """{name: beat} for every readable beat file in `folder` (default HEALTH_DIR; codec_doctor passes
    its own). A missing folder gives {}."""
    out: dict = {}
    folder = folder or HEALTH_DIR
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return out
    for fn in names:
        name = fn[:-5] if fn.endswith(".json") else ""
        if not _NAME_RE.match(name):
            continue
        try:
            with open(os.path.join(folder, fn), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and _num(data.get("ts")) is not None:
            out[name] = data
    return out


def stale_after(cadence_s: Any) -> float:
    """Seconds after which a beat with this cadence counts as silent."""
    cadence = _num(cadence_s) or float(WRITE_EVERY_S)
    return max(STALE_FACTOR * cadence, float(STALE_MIN_S))


def verdict(data: dict, now: Optional[float] = None) -> str:
    """'stopped', 'silent', 'failing' or 'ok' for one beat."""
    now = time.time() if now is None else now
    if data.get("stopped") is True:
        return "stopped"
    if now - (_num(data.get("ts")) or 0.0) > stale_after(data.get("cadence_s")):
        return "silent"
    errors = _num(data.get("consecutive_errors")) or 0
    return "failing" if errors >= FAILING_AFTER else "ok"


def _iso_epoch(text: Any) -> Optional[float]:
    try:
        return datetime.fromisoformat(str(text)).timestamp()
    except (TypeError, ValueError):
        return None


def last_audit_tick(audit_path: str, events=OBSERVER_EVENTS, max_bytes: int = AUDIT_TAIL_BYTES):
    """Read the tail of an audit log: (ts, cadence_s) of the newest line whose event is in `events`
    (None, None when there is none) and the ts of the tail's first line. None when there is no log."""
    try:
        with open(audit_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            start = max(0, size - max_bytes)
            f.seek(start)
            chunk = f.read()
    except OSError:
        return None
    lines = chunk.split(b"\n")
    if start > 0 and lines:
        lines = lines[1:]  # the first line was cut by the seek
    lines = [ln for ln in lines if ln.strip()]
    window_start = None
    for ln in lines:
        try:
            window_start = _iso_epoch(json.loads(ln).get("ts"))
        except (ValueError, AttributeError):
            continue
        if window_start is not None:
            break
    needles = tuple(b'"event":"' + e.encode() + b'"' for e in events)
    for ln in reversed(lines):
        if not any(n in ln for n in needles):
            continue
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        if not isinstance(rec, dict) or rec.get("event") not in events:
            continue
        ts = _iso_epoch(rec.get("ts"))
        if ts is None:
            continue
        extra = rec.get("extra") if isinstance(rec.get("extra"), dict) else {}
        return (ts, _num(extra.get("cadence_used_s"))), window_start
    return (None, None), window_start


def observer_beat(audit_path: str, now: Optional[float] = None) -> Optional[dict]:
    """The observer's beat, from its newest observation tick in the audit log. None when that cannot
    be told: no log, or no tick in a tail that is itself younger than the stale window."""
    now = time.time() if now is None else now
    found = last_audit_tick(audit_path)
    if found is None:
        return None
    (tick_ts, cadence), window_start = found
    if tick_ts is not None:
        ts, cadence = tick_ts, max(cadence or float(WRITE_EVERY_S), float(WRITE_EVERY_S))
    elif window_start is not None and now - window_start > stale_after(OBSERVER_MAX_CADENCE_S):
        ts, cadence = window_start, float(OBSERVER_MAX_CADENCE_S)  # no tick since at least then
    else:
        return None
    return {"ts": ts, "ok": True, "consecutive_errors": 0, "last_error_class": None,
            "cadence_s": cadence, "stopped": False}
