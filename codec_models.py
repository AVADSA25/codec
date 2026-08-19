"""Runtime LLM model switching.

CODEC serves every local model from ONE `mlx_vlm.server` on :8083. That server
resolves the model per request (`get_cached_model(openai_request.model)`), so
switching needs no second server and no restart — only a different `model` field.

Two consequences shape this module:

1. The server holds ONE model at a time. A switch UNLOADS the current model and
   loads the new one, so a switch costs a load pause (~20-60s for a 15-20 GB
   model) and two models can never be resident at once. Attempting to keep both
   loaded thrashes swap and roughly halves generation speed — measured.

2. If the target fails to load, the previous model is already gone and CODEC has
   NO brain. That is why `set_active()` probes after switching and reverts to the
   previous model when the probe fails. A bad switch must never leave the box
   mute.

The chat handler re-reads ~/.codec/config.json on every request, so writing
`llm_model` there takes effect on the next message with no restart.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from codec_jsonstore import atomic_write_json

CONFIG_PATH = os.path.expanduser("~/.codec/config.json")
HF_CACHE = os.path.expanduser("~/.cache/huggingface/hub")

DEFAULT_MODEL = "mlx-community/Qwen3.6-35B-A3B-4bit"
DEFAULT_BASE_URL = "http://localhost:8083/v1"

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
        return {"ok": True, "active": model_id, "previous": previous,
                "changed": True, "detail": "switched (unverified)"}

    ok, detail = probe(model_id)
    if ok:
        _emit_audit(previous, model_id, True, detail)
        return {"ok": True, "active": model_id, "previous": previous,
                "changed": True, "detail": detail}

    # Failed to load — the old model is already unloaded, so put it back and
    # warm it, otherwise CODEC answers nothing at all.
    _write_active(previous)
    reverted_ok, revert_detail = probe(previous)
    _emit_audit(previous, model_id, False, detail)
    return {"ok": False, "active": previous, "attempted": model_id,
            "changed": False,
            "error": f"{model_id} failed to load ({detail}) — reverted to {previous}",
            "reverted": reverted_ok, "revert_detail": revert_detail}


def _emit_audit(previous: str, requested: str, ok: bool, detail: str) -> None:
    try:
        from codec_audit import audit
        audit(event="model_switched", source="codec-models",
              outcome="ok" if ok else "error",
              level="info" if ok else "warning",
              message=f"model switch {previous} -> {requested}",
              extra={"previous": previous, "requested": requested,
                     "ok": ok, "detail": detail[:200]})
    except Exception:
        pass
