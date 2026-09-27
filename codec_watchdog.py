#!/usr/bin/env python3
"""
CODEC Watchdog — flags (and, only when enforced, kills) stuck CODEC processes
that hog RAM while idle.

Logic:
  - Scans every 60s with `ps -eo pid,rss,%cpu,args` (the FULL command line)
  - Only CODEC's own processes are candidates: a Python interpreter running a
    codec_*.py script, a script inside this repo, or an mlx_vlm / mlx_audio
    server on a CODEC model port. Generic Python, Terminal, iTerm, editors and
    everything else on the machine are NEVER candidates.
  - Tracks each candidate PID over time: RSS > threshold AND CPU ≈ 0% for N
    consecutive checks → stuck
  - Ignores PM2-managed PIDs and NEVER_KILL matches (matched on the full args)
  - DRY-RUN by default: a stuck process is logged, audited and posted as a
    dashboard notification, but NOT killed. Real kills only when
    ~/.codec/config.json has {"watchdog": {"enforce": true}} (re-read every
    cycle, so no restart is needed to flip it)
  - Audits via codec_audit.log_event (source "codec-watchdog")

This does NOT limit working processes — a model using 10GB at 80% CPU is fine.

History: v2.1 matched against `ps comm` (the executable path only), so the
NEVER_KILL argument strings ("codec_dashboard", "uvicorn", ...) never matched,
and any idle Python/Terminal/iTerm process >500 MB outside PM2 got SIGTERM then
SIGKILL — 908 kills logged, most of them not CODEC's. Its audit POST went to
/api/audit, which is GET-only, so none of those kills reached the audit log.
"""

import datetime
import json
import os
import re
import signal
import subprocess
import time
import uuid
from urllib.parse import urlparse

# ── Config ─────────────────────────────────────────────────────────────────────
CHECK_INTERVAL   = 60        # seconds between checks
IDLE_STRIKES_MAX = 10        # consecutive idle checks before acting (10 × 60s = 10 min)
CPU_IDLE_THRESH  = 0.5       # below this % CPU = "idle"
RSS_MIN_MB       = 500       # only care about processes using > 500 MB
CMD_LOG_MAX      = 200       # chars of the command line kept in logs / audit

REPO_DIR           = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH        = os.path.expanduser("~/.codec/config.json")
NOTIFICATIONS_PATH = os.path.expanduser("~/.codec/notifications.json")

# Ports CODEC runs its MLX servers on: scripts/start_model_server.sh default
# (mlx_vlm) and kokoro-82m (mlx_audio). The live llm_base_url port from
# config.json is added at runtime. An mlx server on any other port is not ours.
CODEC_MODEL_PORTS = frozenset({8083, 8085})

# A Python interpreter as a path component: python, python3, python3.13, or the
# macOS framework binary .../MacOS/Python. Terminal/iTerm/editors never match.
_PYTHON_EXEC_RE = re.compile(r"(?:^|/)(?:python[0-9.]*|Python)(?=\s|$)")
# A codec_*.py script, or a codec_* module run with -m.
_CODEC_SCRIPT_RE = re.compile(r"(?:^|[\s/])codec_\w+\.py\b|\s-m\s+codec_\w+")
_MLX_SERVER_RE = re.compile(r"\s-m\s+(?:mlx_vlm|mlx_audio)\.server\b")
_PORT_RE = re.compile(r"--port[=\s]+(\d+)")

# Never kill these (substring match on the FULL command line)
NEVER_KILL = [
    "codec_watchdog",        # don't kill ourselves
    "codec_dictate",         # PM2 managed
    "codec_dashboard",       # PM2 managed
    "codec.py",              # PM2 managed
    "codec_mcp",             # PM2 managed
    "mlx_lm.server",         # PM2 managed (qwen models)
    "codec_voice.py",        # PM2 managed
    "uvicorn",               # dashboard server
    "CODECOverlay",          # swift overlay
    "PM2",                   # PM2 itself
    "node",                  # PM2 node processes
    "claude",                # claude code
]

# ── State ──────────────────────────────────────────────────────────────────────
# { pid: { 'strikes': int, 'cmd': str, 'rss_mb': float, 'first_seen': str,
#          'reported': bool } }
idle_tracker = {}


def _short(cmd):
    """Command line for logs / audit / notifications: secrets redacted (same
    patterns as the audit log), then truncated."""
    cmd = " ".join((cmd or "").split())
    try:
        from codec_audit import _redact_string
        cmd = _redact_string(cmd)
    except Exception:
        pass
    return cmd if len(cmd) <= CMD_LOG_MAX else cmd[:CMD_LOG_MAX - 1] + "…"


def load_config():
    """Read ~/.codec/config.json fresh (never cached). {} on any error."""
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def enforce_enabled(cfg):
    """Real kills only when config sets watchdog.enforce to exactly true."""
    wd = cfg.get("watchdog")
    return isinstance(wd, dict) and wd.get("enforce") is True


def model_ports(cfg):
    """CODEC model-server ports, plus the port of config's llm_base_url."""
    ports = set(CODEC_MODEL_PORTS)
    try:
        port = urlparse(cfg.get("llm_base_url") or "").port
        if port:
            ports.add(port)
    except (ValueError, TypeError):
        pass
    return frozenset(ports)


def is_codec_process(cmd, ports=CODEC_MODEL_PORTS):
    """True only when the full command line clearly belongs to CODEC.

    Must be a Python interpreter AND one of: a codec_*.py script / codec_*
    module, a script inside this repo, or an mlx_vlm / mlx_audio server on a
    CODEC model port. Everything else — generic Python, Terminal, iTerm,
    editors — is never a candidate.
    """
    if not cmd or not _PYTHON_EXEC_RE.search(cmd):
        return False
    if _CODEC_SCRIPT_RE.search(cmd):
        return True
    if REPO_DIR and (REPO_DIR.rstrip(os.sep) + os.sep) in cmd:
        return True
    if _MLX_SERVER_RE.search(cmd):
        m = _PORT_RE.search(cmd)
        return bool(m) and int(m.group(1)) in ports
    return False


def get_pm2_pids():
    """Get PIDs of all PM2-managed processes so we skip them."""
    try:
        r = subprocess.run(
            ["pm2", "jlist"], capture_output=True, text=True, timeout=10
        )
        data = json.loads(r.stdout)
        return {p["pid"] for p in data if p.get("pid")}
    except Exception:
        return set()


def get_watched_processes(ports=CODEC_MODEL_PORTS):
    """List CODEC processes above the RSS threshold (full command lines)."""
    try:
        r = subprocess.run(
            ["ps", "-ww", "-eo", "pid,rss,%cpu,args"],
            capture_output=True, text=True, timeout=10
        )
    except Exception:
        return []

    procs = []
    for line in r.stdout.strip().split("\n")[1:]:
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        try:
            pid = int(parts[0])
            rss_kb = int(parts[1])
            cpu = float(parts[2])
            cmd = parts[3]
        except (ValueError, IndexError):
            continue

        rss_mb = rss_kb / 1024.0
        if rss_mb < RSS_MIN_MB:
            continue

        if not is_codec_process(cmd, ports):
            continue

        procs.append({
            "pid": pid,
            "rss_mb": rss_mb,
            "cpu": cpu,
            "cmd": cmd,
        })

    return procs


def is_protected(proc, pm2_pids):
    """Check if process should never be killed (NEVER_KILL on the full args)."""
    if proc["pid"] in pm2_pids:
        return True
    if proc["pid"] == os.getpid():
        return True
    if any(nk in proc["cmd"] for nk in NEVER_KILL):
        return True
    return False


def _log(message):
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[WATCHDOG {timestamp}] {message}", flush=True)


def log_audit(event, message, *, pid, cmd, rss_mb=None, idle_min=None,
              level="warning", outcome="ok", enforce=False):
    """Print + emit a codec_audit event. Never raises."""
    _log(message)
    try:
        from codec_audit import log_event
        log_event(
            event, "codec-watchdog", message,
            extra={
                "pid": pid,
                "cmd": _short(cmd),
                "rss_mb": round(rss_mb) if rss_mb is not None else None,
                "idle_minutes": idle_min,
                "enforce": enforce,
            },
            level=level, outcome=outcome,
        )
    except Exception as e:
        _log(f"audit emit failed: {type(e).__name__}: {e}")


def notify(title, body):
    """Post a dashboard notification (~/.codec/notifications.json). Never raises."""
    try:
        import codec_jsonstore
        with codec_jsonstore.file_lock(NOTIFICATIONS_PATH):
            try:
                with open(NOTIFICATIONS_PATH) as f:
                    notifs = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                notifs = []
            if not isinstance(notifs, list):
                notifs = []
            notifs.insert(0, {
                "id": f"notif_{uuid.uuid4().hex[:10]}",
                "type": "task_report",
                "title": title,
                "body": body[:2000],
                "status": "warning",
                "created": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "read": False,
                "schedule_id": "watchdog",
            })
            codec_jsonstore.atomic_write_json(NOTIFICATIONS_PATH, notifs)
    except Exception as e:
        _log(f"notification write failed: {type(e).__name__}: {e}")


def report_would_kill(pid, cmd, rss_mb, strikes):
    """Dry-run: say what would be killed. Nothing is signalled."""
    msg = (f"DRY-RUN would kill PID {pid} — {rss_mb:.0f} MB RSS, idle "
           f"{strikes} min: {_short(cmd)}")
    log_audit("watchdog_would_kill", msg, pid=pid, cmd=cmd, rss_mb=rss_mb,
              idle_min=strikes, outcome="warning", enforce=False)
    notify("Watchdog (dry-run)",
           f"{msg}\n\nNothing was killed. To let the watchdog kill stuck CODEC "
           f'processes, set "watchdog": {{"enforce": true}} in ~/.codec/config.json.')


def kill_process(pid, cmd, rss_mb, strikes):
    """Kill a stuck process and log it (enforce mode only)."""
    idle_min = strikes
    msg = f"Killed PID {pid} — {rss_mb:.0f} MB RSS, idle {idle_min} min: {_short(cmd)}"
    log_audit("watchdog_kill", msg, pid=pid, cmd=cmd, rss_mb=rss_mb,
              idle_min=idle_min, enforce=True)

    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(2)
        # Check if still alive, force kill
        try:
            os.kill(pid, 0)
            os.kill(pid, signal.SIGKILL)
            log_audit("watchdog_force_kill",
                      f"Force-killed PID {pid} (SIGTERM didn't work): {_short(cmd)}",
                      pid=pid, cmd=cmd, enforce=True)
        except ProcessLookupError:
            pass  # Already dead, good
    except ProcessLookupError:
        pass  # Already gone
    except PermissionError:
        log_audit("watchdog_kill_failed",
                  f"Cannot kill PID {pid} — permission denied: {_short(cmd)}",
                  pid=pid, cmd=cmd, outcome="error", enforce=True)


def check_cycle():
    """One monitoring cycle."""
    cfg = load_config()
    enforce = enforce_enabled(cfg)
    pm2_pids = get_pm2_pids()
    procs = get_watched_processes(model_ports(cfg))
    seen_pids = set()

    for proc in procs:
        pid = proc["pid"]
        seen_pids.add(pid)

        if is_protected(proc, pm2_pids):
            continue

        is_idle = proc["cpu"] < CPU_IDLE_THRESH

        if is_idle:
            entry = idle_tracker.get(pid)
            if entry is None or entry["cmd"] != proc["cmd"]:  # new, or PID reused
                entry = idle_tracker[pid] = {
                    "strikes": 0,
                    "cmd": proc["cmd"],
                    "rss_mb": proc["rss_mb"],
                    "first_seen": datetime.datetime.now().isoformat(),
                    "reported": False,
                }
                _log(f"Idle CODEC candidate PID {pid} ({proc['rss_mb']:.0f} MB): "
                     f"{_short(proc['cmd'])}")
            entry["strikes"] += 1
            entry["rss_mb"] = proc["rss_mb"]

            if entry["strikes"] >= IDLE_STRIKES_MAX:
                if enforce:
                    kill_process(pid, proc["cmd"], proc["rss_mb"], entry["strikes"])
                    del idle_tracker[pid]
                elif not entry["reported"]:
                    report_would_kill(pid, proc["cmd"], proc["rss_mb"], entry["strikes"])
                    entry["reported"] = True
        else:
            # Process is active — reset strikes
            idle_tracker.pop(pid, None)

    # Clean up tracker for processes that disappeared
    stale = [p for p in idle_tracker if p not in seen_pids]
    for p in stale:
        del idle_tracker[p]


def main():
    mode = "ENFORCE (kills)" if enforce_enabled(load_config()) else "DRY-RUN (no kills)"
    print("=" * 60)
    print("  CODEC Watchdog v3.0")
    print(f"  Mode: {mode} — watchdog.enforce in ~/.codec/config.json")
    print(f"  Check every {CHECK_INTERVAL}s | Act after {IDLE_STRIKES_MAX} idle checks")
    print(f"  RAM threshold: {RSS_MIN_MB} MB | CPU idle: <{CPU_IDLE_THRESH}%")
    print("  Targets: CODEC processes only (codec_*.py, this repo, CODEC mlx servers)")
    print("=" * 60, flush=True)

    while True:
        try:
            check_cycle()
        except Exception as e:
            print(f"[WATCHDOG] Error: {e}", flush=True)
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
