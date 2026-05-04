#!/usr/bin/env python3
"""
CODEC Watchdog — kills stuck/zombie processes that hog RAM while idle.

Logic:
  - Scans every 60s for Python/Terminal/iTerm processes
  - Tracks each PID over time: if RSS > threshold AND CPU ≈ 0% for N consecutive checks → kill
  - Ignores PM2-managed PIDs (PM2 handles those via max_memory_restart)
  - Logs kills to CODEC audit system

This does NOT limit working processes — a model using 10GB at 80% CPU is fine.
Only targets processes that are stuck: high RAM + zero CPU for extended periods.
"""

import subprocess, time, json, os, signal, datetime, requests

# ── Config ─────────────────────────────────────────────────────────────────────
CHECK_INTERVAL   = 60        # seconds between checks
IDLE_STRIKES_MAX = 10        # consecutive idle checks before kill (10 × 60s = 10 min)
CPU_IDLE_THRESH  = 0.5       # below this % CPU = "idle"
RSS_MIN_MB       = 500       # only care about processes using > 500 MB
AUDIT_URL        = "http://127.0.0.1:8090/api/audit"

# Processes to monitor (substring match on command path)
WATCH_PATTERNS = [
    "Python",
    "python3",
    "python",
    "Terminal",
    "iTerm",
    "mlx_lm",
    "whisper",
    "codec_session",
]

# Never kill these (substring match on full command or PM2 name)
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
# { pid: { 'strikes': int, 'cmd': str, 'rss_mb': float, 'first_seen': str } }
idle_tracker = {}


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


def get_watched_processes():
    """Get list of processes matching our watch patterns."""
    try:
        r = subprocess.run(
            ["ps", "-eo", "pid,rss,%cpu,comm"],
            capture_output=True, text=True, timeout=10
        )
    except Exception:
        return []

    procs = []
    for line in r.stdout.strip().split("\n")[1:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        try:
            pid = int(parts[0])
            rss_kb = int(parts[1])
            cpu = float(parts[2])
            cmd = " ".join(parts[3:])
        except (ValueError, IndexError):
            continue

        rss_mb = rss_kb / 1024.0
        if rss_mb < RSS_MIN_MB:
            continue

        # Check if command matches any watch pattern
        if not any(pat in cmd for pat in WATCH_PATTERNS):
            continue

        procs.append({
            "pid": pid,
            "rss_mb": rss_mb,
            "cpu": cpu,
            "cmd": cmd,
        })

    return procs


def is_protected(proc, pm2_pids):
    """Check if process should never be killed."""
    if proc["pid"] in pm2_pids:
        return True
    if proc["pid"] == os.getpid():
        return True
    if any(nk in proc["cmd"] for nk in NEVER_KILL):
        return True
    return False


def log_audit(message, category="system"):
    """Log to CODEC audit system."""
    try:
        requests.post(AUDIT_URL, json={
            "category": category,
            "action": "watchdog_kill",
            "detail": message,
        }, timeout=5)
    except Exception:
        pass
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[WATCHDOG {timestamp}] {message}")


def kill_process(pid, cmd, rss_mb, strikes):
    """Kill a stuck process and log it."""
    idle_min = strikes
    msg = f"Killed PID {pid} ({cmd}) — {rss_mb:.0f} MB RSS, idle {idle_min} min"
    log_audit(msg, "system")

    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(2)
        # Check if still alive, force kill
        try:
            os.kill(pid, 0)
            os.kill(pid, signal.SIGKILL)
            log_audit(f"Force-killed PID {pid} (SIGTERM didn't work)")
        except ProcessLookupError:
            pass  # Already dead, good
    except ProcessLookupError:
        pass  # Already gone
    except PermissionError:
        log_audit(f"Cannot kill PID {pid} — permission denied")


def check_cycle():
    """One monitoring cycle."""
    pm2_pids = get_pm2_pids()
    procs = get_watched_processes()
    seen_pids = set()

    for proc in procs:
        pid = proc["pid"]
        seen_pids.add(pid)

        if is_protected(proc, pm2_pids):
            continue

        is_idle = proc["cpu"] < CPU_IDLE_THRESH

        if is_idle:
            if pid in idle_tracker:
                idle_tracker[pid]["strikes"] += 1
                idle_tracker[pid]["rss_mb"] = proc["rss_mb"]
            else:
                idle_tracker[pid] = {
                    "strikes": 1,
                    "cmd": proc["cmd"],
                    "rss_mb": proc["rss_mb"],
                    "first_seen": datetime.datetime.now().isoformat(),
                }

            if idle_tracker[pid]["strikes"] >= IDLE_STRIKES_MAX:
                kill_process(pid, proc["cmd"], proc["rss_mb"], idle_tracker[pid]["strikes"])
                del idle_tracker[pid]
        else:
            # Process is active — reset strikes
            if pid in idle_tracker:
                del idle_tracker[pid]

    # Clean up tracker for processes that disappeared
    stale = [p for p in idle_tracker if p not in seen_pids]
    for p in stale:
        del idle_tracker[p]


def main():
    print("=" * 60)
    print("  CODEC Watchdog v2.1")
    print(f"  Check every {CHECK_INTERVAL}s | Kill after {IDLE_STRIKES_MAX} idle checks")
    print(f"  RAM threshold: {RSS_MIN_MB} MB | CPU idle: <{CPU_IDLE_THRESH}%")
    print("=" * 60)

    while True:
        try:
            check_cycle()
        except Exception as e:
            print(f"[WATCHDOG] Error: {e}")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
