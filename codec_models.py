"""Runtime LLM model switching.

CODEC serves every local model from ONE `mlx_vlm.server` on :8083. That server
resolves the model per request (`get_cached_model(openai_request.model)`), so a
switch needs no second server — only a different `model` field.

**A switch restarts the PM2 process rather than swapping in-place.** In-place
swapping works, but the server does not give the freed memory back: repeated
switching grew it to 52 GB (measured) against a 19 GB model. A recording is
exactly the wrong moment to discover that, and a fresh process is the only
reliable way to reclaim it. Two things make the leak easy to miss:

- PM2's `max_memory_restart` guard cannot see it. PM2 measures RSS, and MLX
  allocates GPU-backed memory that never appears there — the server reads ~60 MB
  of RSS while holding 20 GB. `footprint -p <pid>` (`phys_footprint`) is the only
  honest measure.
- A restart is nearly free. `scripts/start_model_server.sh` preloads the active
  model during FastAPI's lifespan, and uvicorn does not bind the port until
  lifespan finishes — so "port accepts a connection" already means "model
  loaded". That is what `restart_server()` waits for.

Three further consequences shape this module:

1. The server holds ONE model at a time. A switch costs a load pause (~20-60s
   for a 15-20 GB model) and two models can never be resident at once.
   Attempting to keep both loaded thrashes swap and roughly halves generation
   speed — measured.

2. If the target fails to load, the previous model is already gone and CODEC has
   NO brain. That is why `set_active()` probes after switching and reverts to the
   previous model when the probe fails. A bad switch must never leave the box
   mute.

3. The restart is best-effort, never load-bearing. Where PM2 is absent (tests,
   a hand-started server, CI) `restart_server()` reports failure and the switch
   proceeds in-place exactly as before — `probe()` forces the load either way.
   Losing the memory reclaim is a worse outcome than losing the switch.

The chat handler re-reads ~/.codec/config.json on every request, so writing
`llm_model` there takes effect on the next message.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from codec_jsonstore import atomic_write_json

CONFIG_PATH = os.path.expanduser("~/.codec/config.json")
HF_CACHE = os.path.expanduser("~/.cache/huggingface/hub")

DEFAULT_MODEL = "mlx-community/Qwen3.6-35B-A3B-4bit"
DEFAULT_BASE_URL = "http://localhost:8083/v1"

# PM2 process that serves the models. Overridable via config.json so a second
# machine with a different service name does not need a code change.
DEFAULT_PM2_PROCESS = "qwen3.6"

# How long to wait for the restarted server to accept connections. Generous on
# purpose: the port does not open until the model finishes preloading, and a
# 20 GB checkpoint on a cold page cache is the slow case.
_RESTART_READY_TIMEOUT = 300.0

# How long to wait for PM2 to report a NEW pid. Only covers process teardown and
# respawn, not the model load, so it is short.
_RESTART_RESPAWN_TIMEOUT = 30.0

# Local dirs that are not general chat models. Speech, vision-only and
# GUI-grounding checkpoints are served on their own paths and must never appear
# in a chat model picker.
_NON_CHAT = ("whisper", "kokoro", "-tts-", "ui-tars", "nanollava", "embed",
             "flux", "bge", "rerank", "stable-diffusion", "clip", "vae",
             "sdxl", "musicgen", "parakeet")

# CODEC serves MLX checkpoints from one mlx_vlm.server; anything outside the
# mlx-community namespace (torch weights, diffusion models, embedding models)
# cannot be loaded by it and must not be offered as a chat model.
_REQUIRED_PREFIX = "mlx-community/"

# A HF cache dir exists as soon as anything is fetched — including a bare
# metadata stub of a few KB. Only treat a model as usable when real weights are
# on disk, otherwise the picker offers models that cannot load.
_MIN_WEIGHT_BYTES = 500 * 1024 * 1024


def _load_config() -> Dict[str, Any]:
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _friendly(model_id: str) -> str:
    return model_id.split("/")[-1].replace("-4bit", "").replace("-", " ")


def _snapshot_bytes(cache_dir: str) -> int:
    """Size of the newest snapshot only.

    A HF cache dir can hold several revisions whose files hard/sym-link into a
    shared blobs/ store; summing the whole tree double-counts them (Qwen3.6
    reported 40.8 GB for a 19 GB model). Measure one revision, and resolve each
    file to its blob so a symlink is not counted as 0 bytes.
    """
    snaps = os.path.join(cache_dir, "snapshots")
    try:
        revs = [os.path.join(snaps, d) for d in os.listdir(snaps)]
        revs = [d for d in revs if os.path.isdir(d)]
        newest = max(revs, key=os.path.getmtime)
    except (OSError, ValueError):
        return 0
    total = 0
    for fn in os.listdir(newest):
        if not fn.endswith(".safetensors"):
            continue
        try:
            total += os.stat(os.path.realpath(os.path.join(newest, fn))).st_size
        except OSError:
            pass
    return total


def discover_local() -> List[Dict[str, Any]]:
    """Chat-capable models with real weights in the local HF cache."""
    out: List[Dict[str, Any]] = []
    try:
        entries = sorted(os.listdir(HF_CACHE))
    except OSError:
        return out
    for entry in entries:
        if not entry.startswith("models--"):
            continue
        model_id = entry[len("models--"):].replace("--", "/")
        if not model_id.startswith(_REQUIRED_PREFIX):
            continue
        low = model_id.lower()
        if any(tok in low for tok in _NON_CHAT):
            continue
        size = _snapshot_bytes(os.path.join(HF_CACHE, entry))
        if size < _MIN_WEIGHT_BYTES:
            continue  # metadata-only stub
        out.append({"id": model_id, "label": _friendly(model_id),
                    "size_gb": round(size / 1e9, 1)})
    return out


def _visible_ids(cfg: Dict[str, Any]) -> Optional[set]:
    """Optional operator allowlist: `models_visible` in config.json.

    Discovery finds every loadable chat model on disk, which includes ones the
    operator does not want offered (an older generation, a vision checkpoint kept
    only because skills/screenshot_text.py hardcodes it). Listing it here hides
    it from the picker WITHOUT deleting weights that other code paths still use.
    Absent or empty -> show everything discovered.
    """
    raw = cfg.get("models_visible")
    if isinstance(raw, list) and raw:
        return {str(x) for x in raw}
    return None


def get_active(config: Optional[Dict[str, Any]] = None) -> str:
    cfg = config if config is not None else _load_config()
    return cfg.get("llm_model") or DEFAULT_MODEL


def _base_url(cfg: Dict[str, Any]) -> str:
    return cfg.get("llm_base_url", DEFAULT_BASE_URL).rstrip("/")


def list_models() -> Dict[str, Any]:
    """Registry for the picker: local models, annotated from config, plus the
    active one. `roles` in config.json:model_roles maps id -> human purpose."""
    cfg = _load_config()
    roles = cfg.get("model_roles", {}) if isinstance(cfg.get("model_roles"), dict) else {}
    active = get_active(cfg)
    models = discover_local()
    allow = _visible_ids(cfg)
    if allow:
        # The active model is always shown, even if it was removed from the
        # allowlist — hiding what CODEC is currently running would be a lie.
        models = [m for m in models if m["id"] in allow or m["id"] == active]
    for m in models:
        m["role"] = roles.get(m["id"], "")
        m["active"] = (m["id"] == active)
    if active and not any(m["id"] == active for m in models):
        # Active model isn't on disk (or was pruned) — still show it as active.
        models.insert(0, {"id": active, "label": _friendly(active), "size_gb": None,
                          "role": roles.get(active, ""), "active": True})
    return {"active": active, "models": models}


def probe(model_id: str, timeout: float = 240.0,
          base_url: Optional[str] = None) -> tuple[bool, str]:
    """Force the server to load `model_id` with a 1-token request.

    Returns (ok, detail). This is what makes a switch safe: it surfaces a load
    failure while we still know which model to fall back to.
    """
    cfg = _load_config()
    url = (base_url or _base_url(cfg)) + "/chat/completions"
    body = json.dumps({
        "model": model_id,
        "messages": [{"role": "user", "content": "ok"}],
        "max_tokens": 1, "temperature": 0,
    }).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
        return True, f"loaded in {time.time() - t0:.1f}s"
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        return False, f"HTTP {e.code}: {detail}"
    except Exception as e:  # timeout, connection refused, ...
        return False, f"{type(e).__name__}: {e}"


def _pm2_process_name(cfg: Optional[Dict[str, Any]] = None) -> str:
    c = cfg if cfg is not None else _load_config()
    return str(c.get("model_pm2_process") or DEFAULT_PM2_PROCESS)


def _host_port(cfg: Dict[str, Any]) -> tuple[str, int]:
    parsed = urllib.parse.urlparse(_base_url(cfg))
    return parsed.hostname or "localhost", parsed.port or 8083


def _is_listening(host: str, port: int, timeout: float = 1.5) -> bool:
    """Bare TCP connect — the readiness signal, not an HTTP call.

    Same reasoning as the #308 heartbeat fix: a socket that accepts a connection
    proves the process is up without asking it to do any work. Here it proves
    more, because uvicorn binds only after FastAPI's lifespan (and therefore the
    model preload) has finished.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _pm2_pid(name: str) -> Optional[int]:
    """Current pid of a PM2 process, or None if pm2 or the process is absent."""
    pm2 = shutil.which("pm2")
    if not pm2:
        return None
    try:
        out = subprocess.run([pm2, "jlist"], capture_output=True, text=True,
                             timeout=20).stdout
        for proc in json.loads(out):
            if proc.get("name") == name:
                env = proc.get("pm2_env") or {}
                if env.get("status") != "online":
                    return None
                return proc.get("pid") or None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return None


def restart_server(process: Optional[str] = None,
                   ready_timeout: float = _RESTART_READY_TIMEOUT) -> tuple[bool, str]:
    """Restart the model server via PM2 and wait until it serves again.

    Returns (ok, detail). Never raises: a failure here downgrades the switch to
    the old in-place behaviour rather than aborting it.

    Waiting is two-stage because the port is a liar for the first moment after
    `pm2 restart` — the OLD process still holds it, so an immediate connect
    succeeds and we would return before the new process exists. So: wait for PM2
    to report a different pid, THEN wait for that pid to bind the port.
    """
    cfg = _load_config()
    name = process or _pm2_process_name(cfg)
    host, port = _host_port(cfg)

    pm2 = shutil.which("pm2")
    if not pm2:
        return False, "pm2 not on PATH — switching in place"

    before = _pm2_pid(name)
    t0 = time.time()
    # NOTE: `pm2 start <name>` fails (pm2 reads the name as a filename);
    # `restart` is the verb that accepts a process name.
    try:
        r = subprocess.run([pm2, "restart", name, "--update-env"],
                           capture_output=True, text=True, timeout=60)
    except subprocess.SubprocessError as e:
        return False, f"pm2 restart {name} failed: {type(e).__name__}: {e}"
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip().replace("\n", " ")[:200]
        return False, f"pm2 restart {name} exited {r.returncode}: {err}"

    deadline = time.time() + _RESTART_RESPAWN_TIMEOUT
    while time.time() < deadline:
        now = _pm2_pid(name)
        if now is not None and now != before:
            break
        time.sleep(0.5)
    else:
        return False, f"{name} did not respawn within {_RESTART_RESPAWN_TIMEOUT:.0f}s"

    deadline = time.time() + ready_timeout
    while time.time() < deadline:
        if _is_listening(host, port):
            return True, f"{name} restarted, serving in {time.time() - t0:.1f}s"
        time.sleep(1.0)
    return False, f"{name} restarted but {host}:{port} never opened within {ready_timeout:.0f}s"


def _write_active(model_id: str) -> None:
    cfg = _load_config()
    cfg["llm_model"] = model_id
    atomic_write_json(CONFIG_PATH, cfg)  # helper already writes 0600


def set_active(model_id: str, verify: bool = True) -> Dict[str, Any]:
    """Switch the chat model, verifying it loads and reverting if it does not.

    Only models discovered locally may be selected — an arbitrary string would
    unload the working model in exchange for a 404.
    """
    previous = get_active()
    cfg = _load_config()
    allow = _visible_ids(cfg)
    known = {m["id"] for m in discover_local()}
    if allow:
        known &= allow            # hidden models are not switchable either
    known |= {previous}
    if model_id not in known:
        return {"ok": False, "error": f"unknown model: {model_id}",
                "active": previous}
    if model_id == previous:
        return {"ok": True, "active": previous, "changed": False,
                "detail": "already active"}

    _write_active(model_id)
    if not verify:
        # No restart either: an unverified switch is the caller asking not to
        # wait, and a restart is the longest part of the wait.
        return {"ok": True, "active": model_id, "previous": previous,
                "changed": True, "detail": "switched (unverified)"}

    # Config is written first so the restarted server preloads the NEW model.
    restarted, restart_detail = restart_server()

    ok, detail = probe(model_id)
    if ok:
        _emit_audit(previous, model_id, True, detail, restarted, restart_detail)
        return {"ok": True, "active": model_id, "previous": previous,
                "changed": True, "detail": detail,
                "restarted": restarted, "restart_detail": restart_detail}

    # Failed to load — the old model is already unloaded, so put it back and
    # warm it, otherwise CODEC answers nothing at all. Restart again on the way
    # back: the process that just failed to load a model is the last one we want
    # to keep serving from.
    _write_active(previous)
    restart_server()
    reverted_ok, revert_detail = probe(previous)
    _emit_audit(previous, model_id, False, detail, restarted, restart_detail)
    return {"ok": False, "active": previous, "attempted": model_id,
            "changed": False,
            "error": f"{model_id} failed to load ({detail}) — reverted to {previous}",
            "reverted": reverted_ok, "revert_detail": revert_detail,
            "restarted": restarted, "restart_detail": restart_detail}


def _emit_audit(previous: str, requested: str, ok: bool, detail: str,
                restarted: Optional[bool] = None,
                restart_detail: str = "") -> None:
    try:
        from codec_audit import audit
        extra = {"previous": previous, "requested": requested,
                 "ok": ok, "detail": detail[:200]}
        if restarted is not None:
            # Recorded so a switch that quietly fell back to in-place — and
            # therefore did NOT reclaim memory — is visible after the fact.
            extra["restarted"] = restarted
            extra["restart_detail"] = restart_detail[:200]
        audit(event="model_switched", source="codec-models",
              outcome="ok" if ok else "error",
              level="info" if ok else "warning",
              message=f"model switch {previous} -> {requested}",
              extra=extra)
    except Exception:
        pass
