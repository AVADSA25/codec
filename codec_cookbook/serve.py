"""Cookbook PM2 serve/stop + port allocation + health — the SAFETY-CRITICAL core.

Structural guarantees (the whole point of Cookbook):
  * launch() only ever allocates a port in 8110-8119 that a live socket probe
    + pm2 jlist + our own served.json all agree is free. Protected ports
    (8083/8090/8094/9223/5678) are never in range and are skipped anyway.
  * Every process we start is named `cookbook-<id>-<port>`.
  * stop() will ONLY delete a process that (a) we recorded in served.json,
    (b) is named `cookbook-…`, and (c) is NOT on a protected port — and only
    when confirm=True. Anything else returns a refusal (or a dry-run).
  * Nothing here issues docker stop/rm, changes an existing service's port, or
    restarts/stops a non-cookbook process.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Optional, Union

from codec_jsonstore import atomic_write_json, file_lock
from . import probe

log = logging.getLogger("codec_cookbook.serve")

COOKBOOK_PREFIX = "cookbook-"
PROTECTED_PORTS = probe.PROTECTED_PORTS          # 8083/8090/8094/9223/5678
SERVE_RANGE = probe.SERVE_RANGE                  # range(8110, 8120)
SERVED_PATH = os.path.expanduser("~/.codec/cookbook/served.json")
_CONFIG_PATH = os.path.expanduser("~/.codec/config.json")
_HEALTH_TIMEOUT_S = 90
_PM2_TIMEOUT_S = 30


# ── persistence ─────────────────────────────────────────────────────────────

def _load_served() -> list[dict]:
    try:
        with open(SERVED_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save_served(records: list[dict]) -> None:
    with file_lock(SERVED_PATH):
        atomic_write_json(SERVED_PATH, records, default=str)


def _record_served(rec: dict) -> None:
    with file_lock(SERVED_PATH):
        records = _load_served()
        records = [r for r in records if r.get("pm2_name") != rec["pm2_name"]]
        records.append(rec)
        atomic_write_json(SERVED_PATH, records, default=str)


def _forget_served(pm2_name: str) -> None:
    with file_lock(SERVED_PATH):
        records = [r for r in _load_served() if r.get("pm2_name") != pm2_name]
        atomic_write_json(SERVED_PATH, records, default=str)


# ── interpreter discovery (no hardcoded venv) ───────────────────────────────

def resolve_mlx_python() -> str:
    """Python that has mlx-lm: config.json:cookbook.mlx_python if set, else
    sys.executable (the interpreter already running CODEC, which serves
    qwen3.6 and therefore has MLX)."""
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        py = (cfg.get("cookbook") or {}).get("mlx_python")
        if py and os.path.exists(py):
            return py
    except (OSError, json.JSONDecodeError):
        pass
    return sys.executable


def resolve_llama_server() -> Optional[str]:
    """`llama-server` resolved from PATH, or None if absent."""
    import shutil
    return shutil.which("llama-server")


# ── port allocation ─────────────────────────────────────────────────────────

def allocate_port() -> Optional[int]:
    """First free port in 8110-8119. Skips: protected ports, ports a live
    socket probe says are bound, ports any PM2 process declares, and ports we
    already recorded in served.json. Returns None if the range is exhausted."""
    bound = probe.bound_ports_in_range(SERVE_RANGE.start, SERVE_RANGE.stop)
    pm2_ports = {p["port"] for p in probe.pm2_processes() if p.get("port")}
    ours = {r["port"] for r in _load_served() if r.get("port")}
    for port in SERVE_RANGE:
        if port in PROTECTED_PORTS:          # belt-and-suspenders (none in range)
            continue
        if port in bound or port in pm2_ports or port in ours:
            continue
        return port
    return None


# ── launch ───────────────────────────────────────────────────────────────────

def _build_command(entry: dict, port: int, context_length: int) -> tuple[Optional[list[str]], Optional[str]]:
    """Return (pm2_argv, error). Builds the corrected serve command for the
    entry's backend."""
    mid = entry["id"]
    pm2_name = f"{COOKBOOK_PREFIX}{mid}-{port}"
    backend = entry.get("backend", "mlx")
    if backend == "mlx":
        py = resolve_mlx_python()
        # NOTE: `python -m mlx_lm server` (subcommand form) — NOT `-m mlx_lm.server`.
        # --max-tokens is mandatory: mlx-lm defaults to 512 and silently truncates.
        argv = ["pm2", "start", py, "--name", pm2_name, "--",
                "-m", "mlx_lm", "server", "--model", entry["hf_repo"],
                "--host", "127.0.0.1", "--port", str(port), "--max-tokens", "16384"]
        return argv, None
    if backend in ("gguf", "llama", "llama.cpp"):
        server = resolve_llama_server()
        if not server:
            return None, "llama-server not found on PATH"
        gguf = entry.get("gguf_path") or entry.get("hf_repo")
        # Metal is on by default on Apple Silicon; -ngl 999 forces full GPU offload.
        argv = ["pm2", "start", server, "--name", pm2_name, "--",
                "-m", gguf, "--host", "127.0.0.1", "--port", str(port),
                "-ngl", "999", "-c", str(context_length)]
        return argv, None
    return None, f"unknown backend: {backend!r}"


def _health_ok(port: int, timeout_s: int = _HEALTH_TIMEOUT_S) -> bool:
    """Poll GET /v1/models until 200 or timeout."""
    import urllib.request
    deadline = time.monotonic() + timeout_s
    url = f"http://127.0.0.1:{port}/v1/models"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False


def launch(entry: dict, context_length: int = 8192,
           wait_health: bool = True) -> dict:
    """Allocate a free 8110-8119 port and start the model under PM2. Returns a
    status dict. Never touches an existing service."""
    port = allocate_port()
    if port is None:
        return {"status": "error", "reason": "no_free_port",
                "range": [SERVE_RANGE.start, SERVE_RANGE.stop - 1]}
    argv, err = _build_command(entry, port, context_length)
    if err:
        return {"status": "error", "reason": err}
    pm2_name = f"{COOKBOOK_PREFIX}{entry['id']}-{port}"
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=_PM2_TIMEOUT_S)
    except FileNotFoundError:
        return {"status": "error", "reason": "pm2_not_found"}
    except subprocess.TimeoutExpired:
        return {"status": "error", "reason": "pm2_start_timeout", "pm2_name": pm2_name}
    if r.returncode != 0:
        return {"status": "error", "reason": "pm2_start_failed",
                "detail": (r.stderr or r.stdout or "")[:300], "pm2_name": pm2_name}

    rec = {
        "id": entry["id"],
        "port": port,
        "pm2_name": pm2_name,
        "backend": entry.get("backend", "mlx"),
        "hf_repo": entry.get("hf_repo"),
        "context": context_length,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    _record_served(rec)

    healthy = _health_ok(port) if wait_health else None
    return {"status": "serving" if healthy or not wait_health else "started_unhealthy",
            "healthy": healthy, **rec}


# ── stop — the guard ─────────────────────────────────────────────────────────

def _resolve_target(target: Union[str, int]) -> Optional[dict]:
    """Resolve a stop target (a cookbook pm2 name OR a port) to OUR served
    record. Returns None if it isn't a process we started — the caller treats
    None as a hard refusal, so we never delete anything we don't own."""
    served = _load_served()
    s = str(target).strip()
    if s.isdigit():
        port = int(s)
        for r in served:
            if r.get("port") == port:
                return r
        return None
    for r in served:
        if r.get("pm2_name") == s:
            return r
    return None


def stop(target: Union[str, int], confirm: bool = False) -> dict:
    """Stop a Cookbook-started model. Layered refusals (in order):
      1. target not a process WE started (served.json)      → refused
      2. resolved port is a protected port                  → refused
      3. resolved pm2 name not in the `cookbook-` namespace → refused
      4. confirm is not True                                → dry-run (would_stop)
    Only after all four pass do we `pm2 delete <name>`."""
    rec = _resolve_target(target)
    if rec is None:
        return {"status": "refused", "reason": "not_a_cookbook_process",
                "target": str(target),
                "detail": "Cookbook only stops models it started (recorded in served.json)."}
    name, port = rec.get("pm2_name", ""), rec.get("port")
    if port in PROTECTED_PORTS:
        return {"status": "refused", "reason": "protected_port", "port": port}
    if not name.startswith(COOKBOOK_PREFIX):
        return {"status": "refused", "reason": "not_cookbook_namespace", "pm2_name": name}
    if not confirm:
        return {"status": "would_stop", "pm2_name": name, "port": port,
                "hint": "re-run with confirm=true to actually stop it"}
    try:
        r = subprocess.run(["pm2", "delete", name],
                           capture_output=True, text=True, timeout=_PM2_TIMEOUT_S)
    except FileNotFoundError:
        return {"status": "error", "reason": "pm2_not_found", "pm2_name": name}
    except subprocess.TimeoutExpired:
        return {"status": "error", "reason": "pm2_delete_timeout", "pm2_name": name}
    if r.returncode != 0:
        return {"status": "error", "reason": "pm2_delete_failed",
                "detail": (r.stderr or r.stdout or "")[:300], "pm2_name": name}
    _forget_served(name)
    return {"status": "stopped", "pm2_name": name, "port": port}


# ── list ──────────────────────────────────────────────────────────────────────

def list_served() -> list[dict]:
    """Cookbook-served models with live status (online/stopped via pm2) +
    a current health probe. Read-only."""
    live = {p["name"]: p for p in probe.pm2_processes()}
    out = []
    for r in _load_served():
        name = r.get("pm2_name", "")
        proc = live.get(name)
        out.append({
            **r,
            "pm2_status": proc.get("status") if proc else "absent",
            "healthy": probe.is_port_bound(r["port"]) if r.get("port") else None,
        })
    return out
