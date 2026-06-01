"""Cookbook hardware + process probe — STRICTLY READ-ONLY.

Nothing here ever starts, stops, binds, or mutates anything. It reads:
  * chip + total unified memory   (sysctl / system_profiler)
  * free memory                   (vm_stat)
  * running PM2 processes + RSS   (pm2 jlist)
  * whether a TCP port is bound    (socket connect probe)
  * mlx-lm version (Qwen3 MoE needs >= 0.25.2)

Every function is defensive: on any failure it returns a safe default
(None / 0 / [] / False) rather than raising, so a probe never breaks a skill.
"""
from __future__ import annotations

import json
import logging
import socket
import subprocess
from functools import lru_cache
from typing import Optional

log = logging.getLogger("codec_cookbook.probe")

# Protected ports Cookbook must never bind to or stop. Mirrored by serve.py.
# Verified by lsof against the live box (2026-06-01), not assumed:
#   8083  mlx_vlm.server   — Qwen3.6 LLM + UI-TARS/VLM vision
#   8084  whisper_server   — STT (Whisper)
#   8085  mlx_audio.server — TTS
#   8090  codec-dashboard
#   8094  pilot-runner
#   9222  Chrome DevTools CDP (routes/cdp.py + chrome skills; on-demand)
#   9223  pilot CDP (on-demand)
#   5678  n8n
# 8081/8082 probed FREE — Qwen+vision were consolidated onto 8083, so those
# slots are vacated; not protected (nothing to fat-finger there). This static
# denylist is belt-and-suspenders: allocate_port() also skips ANY live-bound
# port at call time, and stop() refuses anything outside the cookbook- namespace.
PROTECTED_PORTS = frozenset({8083, 8084, 8085, 8090, 8094, 9222, 9223, 5678})
# Cookbook's own serve range.
SERVE_RANGE = range(8110, 8120)  # 8110-8119 inclusive
OS_RESERVE_GB = 24


def _run(cmd: list[str], timeout: int = 10) -> Optional[str]:
    """Run a read-only command, return stdout or None. Never raises."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode == 0:
            return r.stdout
        log.debug("probe cmd %s rc=%s: %s", cmd[:2], r.returncode, (r.stderr or "")[:200])
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        log.debug("probe cmd %s failed: %s", cmd[:2], e)
        return None


@lru_cache(maxsize=1)
def chip() -> str:
    """Apple Silicon chip string (e.g. 'Apple M1 Ultra'), or '' if unknown."""
    out = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
    return (out or "").strip()


@lru_cache(maxsize=1)
def unified_total_gb() -> float:
    """Total unified memory in GB (hw.memsize). 0.0 if unreadable."""
    out = _run(["sysctl", "-n", "hw.memsize"])
    if out:
        try:
            return int(out.strip()) / 1e9
        except ValueError:
            pass
    return 0.0


def vm_free_gb() -> float:
    """Free + inactive memory in GB from vm_stat (a live, fluctuating figure).
    Used for the scan report only — fit uses the deterministic resident-sum
    formula in available_gb()."""
    out = _run(["vm_stat"])
    if not out:
        return 0.0
    page_size = 16384  # Apple Silicon default; corrected below if vm_stat reports it
    free_pages = inactive_pages = 0
    for line in out.splitlines():
        low = line.lower()
        if "page size of" in low:
            for tok in low.replace("(", " ").replace(")", " ").split():
                if tok.isdigit():
                    page_size = int(tok)
                    break
        elif low.startswith("pages free:"):
            free_pages = _trailing_int(line)
        elif low.startswith("pages inactive:"):
            inactive_pages = _trailing_int(line)
    return ((free_pages + inactive_pages) * page_size) / 1e9


def _trailing_int(line: str) -> int:
    digits = "".join(c for c in line if c.isdigit())
    return int(digits) if digits else 0


def pm2_jlist() -> list[dict]:
    """Raw `pm2 jlist` output as a list of dicts. [] on any failure."""
    out = _run(["pm2", "jlist"])
    if not out:
        return []
    try:
        data = json.loads(out)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, ValueError):
        return []


def pm2_processes() -> list[dict]:
    """Parsed PM2 process summaries: name, status, rss_gb, pm_id, port (if the
    process declares one via its PORT env or args)."""
    procs = []
    for p in pm2_jlist():
        env = p.get("pm2_env", {}) or {}
        monit = p.get("monit", {}) or {}
        procs.append({
            "name": p.get("name", "?"),
            "pm_id": p.get("pm_id"),
            "status": env.get("status", "?"),
            "rss_gb": round((monit.get("memory", 0) or 0) / 1e9, 3),
            "port": _proc_declared_port(p),
        })
    return procs


def _proc_declared_port(p: dict) -> Optional[int]:
    """Best-effort port a PM2 proc declares (PORT env or a --port/-port arg).
    Not authoritative for binding — that's what is_port_bound() is for."""
    env = p.get("pm2_env", {}) or {}
    port_env = env.get("PORT") or env.get("env", {}).get("PORT") if isinstance(env.get("env"), dict) else env.get("PORT")
    if port_env:
        try:
            return int(port_env)
        except (ValueError, TypeError):
            pass
    # scan args for --port N / -port N / --port=N
    args = env.get("args") or []
    if isinstance(args, str):
        args = args.split()
    for i, a in enumerate(args):
        a = str(a)
        if a in ("--port", "-port", "--listen-port") and i + 1 < len(args):
            try:
                return int(args[i + 1])
            except (ValueError, TypeError):
                pass
        if a.startswith("--port="):
            try:
                return int(a.split("=", 1)[1])
            except (ValueError, TypeError):
                pass
    return None


def resident_gb_total() -> float:
    """Sum of RSS (GB) across all online PM2 processes — the live stack's
    footprint, used by available_gb()."""
    return round(sum(p["rss_gb"] for p in pm2_processes()
                     if p.get("status") == "online"), 3)


def available_gb(os_reserve_gb: int = OS_RESERVE_GB) -> float:
    """Unified memory available for a NEW model:
        total - os_reserve - sum(resident PM2 RSS).
    Deterministic (doesn't depend on the fluctuating vm_stat free figure)."""
    total = unified_total_gb()
    if total <= 0:
        return 0.0
    return round(total - os_reserve_gb - resident_gb_total(), 3)


def is_port_bound(port: int, host: str = "127.0.0.1") -> bool:
    """True if something is listening on host:port right now (socket probe)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.4)
            return s.connect_ex((host, port)) == 0
    except OSError:
        return False


def bound_ports_in_range(lo: int, hi: int) -> set[int]:
    """Subset of [lo, hi) that is currently bound (socket probe)."""
    return {p for p in range(lo, hi) if is_port_bound(p)}


@lru_cache(maxsize=1)
def mlx_version() -> Optional[str]:
    try:
        import mlx_lm
        return getattr(mlx_lm, "__version__", None)
    except Exception:
        return None


def mlx_version_ok(minimum: tuple = (0, 25, 2)) -> bool:
    """True if mlx-lm meets the Qwen3-MoE minimum. None version → False (warn)."""
    v = mlx_version()
    if not v:
        return False
    try:
        parts = tuple(int(x) for x in v.split(".")[:3])
        return parts >= minimum
    except ValueError:
        return False


def snapshot() -> dict:
    """Full read-only state for the scan skill."""
    procs = pm2_processes()
    return {
        "chip": chip(),
        "unified_total_gb": round(unified_total_gb(), 1),
        "vm_free_gb": round(vm_free_gb(), 1),
        "resident_gb_total": resident_gb_total(),
        "available_gb": available_gb(),
        "os_reserve_gb": OS_RESERVE_GB,
        "pm2_process_count": len(procs),
        "pm2_processes": procs,
        "mlx_version": mlx_version(),
        "mlx_version_ok": mlx_version_ok(),
        "protected_ports": sorted(PROTECTED_PORTS),
        "serve_range": [SERVE_RANGE.start, SERVE_RANGE.stop - 1],
        "serve_ports_bound": sorted(bound_ports_in_range(SERVE_RANGE.start, SERVE_RANGE.stop)),
    }
