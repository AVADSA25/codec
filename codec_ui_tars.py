"""UI-TARS on demand (docs/UI-TARS-SERVER-DESIGN.md).

Mouse control asks UI-TARS where to click. The main model server holds one model and
answers 409 to any other (docs/MODEL-MEMORY-GUARD-DESIGN.md, option B), so UI-TARS gets
a server of its own on 127.0.0.1:8087. `endpoint()` starts it on first use;
scripts/ui_tars_server.py stops it after `idle_seconds` without a request. No PM2 app, no
LaunchAgent: nothing stays resident between tasks.

One starter at a time across processes (codec-dashboard, open-codec, codec-mcp-http): the
start runs under an flock on the state file. The server is never asked to download (HF_HUB_OFFLINE=1),
never starts while an image job runs or memory is short, and never touches a port it did
not open. The only process this module ever stops is one it started itself (its pid in the
state file, its own process group).

Standard library only, apart from CODEC's own helpers.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import time
from typing import Optional, Tuple

from codec_jsonstore import atomic_write_json, file_lock

HOST = "127.0.0.1"
LEGACY_URL = "http://localhost:8083/v1"   # mouse control's URL before this change
REPO = os.path.dirname(os.path.abspath(__file__))
SERVER_SCRIPT = os.path.join(REPO, "scripts", "ui_tars_server.py")
SOURCE = "codec-ui-tars"

DEFAULTS = {"on_demand": True, "port": 8087, "idle_seconds": 300, "max_seconds": 7200,
            "min_free_gb": 10, "load_timeout_s": 120}
BOUNDS = {"port": (1024, 65535), "idle_seconds": (30, 3600), "max_seconds": (600, 86400),
          "min_free_gb": (4, 64), "load_timeout_s": (20, 600)}
_STOP_WAIT_S = 20        # a server that is shutting down gets this long to exit
_LOG_MAX_BYTES = 5_000_000
_LOG_KEEP_BYTES = 1_000_000

MESSAGES = {
    "not_installed": ("Mouse control needs the UI-TARS model ({model}), and it is not "
                      "installed on this Mac."),
    "image_job": ("An image is being generated and needs the memory UI-TARS would use. "
                  "Try again when it finishes."),
    "low_memory": ("Not enough free memory for UI-TARS: {free:.0f} GB free, and it needs "
                   "about {need} GB. Close something large and try again."),
    "port_taken": ("Port {port} is used by another program, so the UI-TARS server cannot "
                   "start. Set ui_tars.port in config.json to a free port."),
    "load_failed": ("The UI-TARS server did not start. Its log is "
                    "~/.codec/logs/ui_tars_server.log."),
}

_children: dict = {}     # pid -> Popen, so an exited server is reaped and never looks alive


# ── Paths and settings ───────────────────────────────────────────────────────

def _dir() -> str:
    return os.environ.get("CODEC_UI_TARS_DIR") or os.path.expanduser("~/.codec")


def state_path() -> str:
    return os.path.join(_dir(), "ui_tars_server.json")


def log_path() -> str:
    return os.path.join(_dir(), "logs", "ui_tars_server.log")


def env_enabled() -> bool:
    return os.environ.get("UI_TARS_ON_DEMAND", "true").strip().lower() not in ("false", "0", "no", "off")


def settings(cfg: Optional[dict]) -> dict:
    """config.json:ui_tars with defaults, every number clamped into BOUNDS."""
    raw = (cfg or {}).get("ui_tars")
    raw = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULTS)
    if isinstance(raw.get("on_demand"), bool):
        out["on_demand"] = raw["on_demand"]
    for key, (lo, hi) in BOUNDS.items():
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        out[key] = int(min(max(value, lo), hi))
    return out


def _load_cfg() -> dict:
    try:
        from codec_config_path import config_path
        with open(config_path()) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, ImportError):
        return {}


# ── Small probes (each one a function, so tests replace it) ──────────────────

def _hf_hub() -> str:
    return os.environ.get("HF_HUB_CACHE") or os.path.join(
        os.environ.get("HF_HOME") or os.path.expanduser("~/.cache/huggingface"), "hub")


def model_cached(model: str) -> bool:
    """A hub id already in the local Hugging Face cache, or an existing local folder."""
    if os.path.isabs(model):
        return os.path.isdir(model)
    return os.path.isdir(os.path.join(_hf_hub(), "models--" + model.replace("/", "--"), "snapshots"))


def _image_busy() -> bool:
    try:
        import codec_image
        return bool(codec_image.busy_message())
    except Exception:
        return False


def _free_gb() -> float:
    try:
        import codec_image
        return float(codec_image.free_memory_gb())
    except Exception:
        return 0.0


def _port_open(port: int, timeout: float = 0.5) -> bool:
    """Something accepts connections on 127.0.0.1:port. uvicorn binds only after the
    model has loaded (mlx_vlm preloads in its lifespan), so for our server this also
    means "ready"."""
    try:
        with socket.create_connection((HOST, port), timeout=timeout):
            return True
    except OSError:
        return False


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    child = _children.get(pid)
    if child is not None:
        if child.poll() is None:
            return True
        _children.pop(pid, None)
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _is_ours(pid: int) -> bool:
    """The pid runs scripts/ui_tars_server.py. A pid from the state file may have been
    reused by another program after a crash; that one is never stopped."""
    if pid in _children:
        return True
    try:
        out = subprocess.run(["/bin/ps", "-o", "command=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return f" {SERVER_SCRIPT} " in f" {out.strip()} "


def _reap() -> None:
    for pid, child in list(_children.items()):
        if child.poll() is not None:
            _children.pop(pid, None)


def interpreter() -> str:
    """The Python with mlx_vlm, in start_model_server.sh's order."""
    venv = os.environ.get("CODEC_MODEL_VENV")
    candidates = []
    if venv:
        candidates.append(os.path.join(venv, "bin", "python"))
    candidates.append(os.path.normpath(os.path.join(REPO, "..", "python", "bin", "python3")))
    candidates.append(os.path.expanduser("~/codec-qwen38-venv/bin/python"))
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return shutil.which("python3") or "python3"


# ── State ────────────────────────────────────────────────────────────────────

def _read_state() -> dict:
    try:
        with open(state_path()) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _clear_state() -> None:
    try:
        os.remove(state_path())
    except OSError:
        pass


def _audit(event: str, extra: dict, level: str = "info") -> None:
    try:
        from codec_audit import log_event
        log_event(event, SOURCE, event.replace("_", " "), extra=extra, level=level,
                  outcome="warning" if level == "warning" else "ok")
    except Exception:
        pass


def _refuse(reason: str, **fields) -> Tuple[None, str]:
    _audit("ui_tars_server_refused", {"reason": reason}, level="warning")
    return None, MESSAGES[reason].format(**fields)


def _stop_own(pid: int) -> None:
    """Stop a server this module started (its own process group), then wait for it."""
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        return
    deadline = time.monotonic() + _STOP_WAIT_S
    while time.monotonic() < deadline and _alive(pid):
        time.sleep(0.2)
    if _alive(pid):
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def _wait_exit(pid: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.2)
    return not _alive(pid)


def _open_log() -> int:
    path = log_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        if os.path.getsize(path) > _LOG_MAX_BYTES:
            with open(path, "rb") as f:
                f.seek(-_LOG_KEEP_BYTES, os.SEEK_END)
                tail = f.read()
            with open(path, "wb") as f:
                f.write(tail)
    except OSError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return fd


def _child_env() -> dict:
    import codec_envscrub
    env = codec_envscrub.scrub(os.environ)
    env.update({"HF_HUB_OFFLINE": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"})
    return env


def command(model: str, s: dict) -> list:
    return [interpreter(), SERVER_SCRIPT, "--model", model, "--host", HOST,
            "--port", str(s["port"]), "--idle-seconds", str(s["idle_seconds"]),
            "--max-seconds", str(s["max_seconds"])]


# ── The one entry point ──────────────────────────────────────────────────────

def endpoint(cfg: Optional[dict] = None) -> Tuple[Optional[str], Optional[str]]:
    """(base URL ending in /v1, None) to send UI-TARS requests to, or (None, a message
    for the owner) when the server cannot run. Starts the server when needed; blocks up
    to `load_timeout_s` while it loads."""
    cfg = _load_cfg() if cfg is None else cfg
    base = cfg.get("ui_tars_base_url")
    if isinstance(base, str) and base.strip():
        return base.strip().rstrip("/"), None          # the owner's own server
    s = settings(cfg)
    if not s["on_demand"] or not env_enabled():
        return LEGACY_URL, None
    import codec_models
    model = codec_models.resolve("ui_tars", cfg)
    with file_lock(state_path()):
        return _ensure(model, s)


def _ensure(model: str, s: dict) -> Tuple[Optional[str], Optional[str]]:
    _reap()
    port = s["port"]
    url = f"http://{HOST}:{port}/v1"
    state = _read_state()
    pid = int(state.get("pid") or 0) if isinstance(state.get("pid"), (int, float)) else 0
    if pid and _alive(pid) and _is_ours(pid):
        same = state.get("port") == port and state.get("model") == model
        if same and _port_open(port):
            return url, None
        # A server we started that is shutting down (idle stop) or serves another
        # model or port: let it finish, or stop it, before starting a fresh one.
        if not (same and _wait_exit(pid, _STOP_WAIT_S)):
            _stop_own(pid)
    _clear_state()

    if _port_open(port):
        return _refuse("port_taken", port=port)
    if not model_cached(model):
        return _refuse("not_installed", model=model)
    if _image_busy():
        return _refuse("image_job")
    free = _free_gb()
    if free < s["min_free_gb"]:
        return _refuse("low_memory", free=free, need=s["min_free_gb"])

    fd = _open_log()
    started = time.monotonic()
    try:
        proc = subprocess.Popen(command(model, s), stdin=subprocess.DEVNULL, stdout=fd,
                                stderr=fd, env=_child_env(), cwd=REPO, start_new_session=True,
                                close_fds=True)
    except OSError:
        return _refuse("load_failed")
    finally:
        os.close(fd)
    _children[proc.pid] = proc
    atomic_write_json(state_path(), {"pid": proc.pid, "port": port, "model": model,
                                     "started_at": time.time()})
    deadline = started + s["load_timeout_s"]
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        if _port_open(port):
            load_s = round(time.monotonic() - started, 1)
            _audit("ui_tars_server_started", {"model": model, "port": port, "load_s": load_s})
            return url, None
        time.sleep(0.5)
    if proc.poll() is None:
        _stop_own(proc.pid)
    _children.pop(proc.pid, None)
    _clear_state()
    return _refuse("load_failed")
