"""First-run connection setup: give CODEC a brain.

WHY THIS EXISTS (2026-09-04)
---------------------------
A fresh paid install could not talk to any model at all. `fetch_models.py`
downloads Qwen2.5-7B-Instruct-4bit; the chat handler defaulted to
Qwen3.6-35B-A3B-4bit, which was never downloaded; the installer wrote neither
`llm_model` nor `llm_base_url`. So the first message a buyer sent failed, and
there was no UI anywhere to fix it. Nobody noticed because the developer's
machine has every model and a hand-tuned config.

THE CONTRACT
------------
Three ways to connect, all ending in the same place — `llm_base_url` +
`llm_model` in config.json, with any secret in the Keychain:

  local  : an MLX model served by the bundled mlx_vlm server on :8083.
  ava    : AVA Digital's proxy, authorised by the buyer's own licence key.
           No API key to find — this is the zero-friction path.
  custom : ANY OpenAI-compatible endpoint (Ollama, LM Studio, OpenRouter,
           Together, a self-hosted vLLM). One base URL + one optional key,
           because enumerating providers is a treadmill and this covers them
           all through the same `/chat/completions` shape codec_llm already
           speaks.

`verify()` is the load-bearing part. Setting a provider proves nothing; the
screen only clears once a real request has come back from a real endpoint. A
setup flow that says "connected" without asking the model anything is how the
current install shipped broken.
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
MODELS_DIR = os.path.expanduser("~/.codec/models")
HF_CACHE = os.path.expanduser("~/.cache/huggingface/hub")

# The model fetch_models.py actually downloads for a fresh install. The old
# default named a 20 GB model nobody had — the mismatch that made every first
# chat fail. Keep these two in step: packaging/macos/models.json is the source.
BUNDLED_LOCAL_MODEL = "mlx-community/Qwen2.5-7B-Instruct-4bit"
LOCAL_BASE_URL = "http://localhost:8083/v1"

AVA_PROXY_URL = "https://ava-proxy.lucyvpa.com"
AVA_DEFAULT_MODEL = "gemini-2.5-flash-lite"

PROVIDERS = ("local", "ava", "custom")

# The bearer for llm_base_url lives in ONE Keychain slot: "llm_api_key", which
# is what codec_dashboard already reads via codec_config.get_llm_api_key().
# The first version stored custom keys under a separate slot; verify() added
# the key itself and went green, then every real chat sent no key and got 401.
# A false green from the setup screen is the exact failure it exists to prevent,
# so setup and chat now read the same slot by construction.
KEY_SLOT = "llm_api_key"


def _load_config() -> Dict[str, Any]:
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_config(cfg: Dict[str, Any]) -> None:
    atomic_write_json(CONFIG_PATH, cfg)


# ── What is on this machine ──────────────────────────────────────────────────

def _dir_has_weights(path: str, min_bytes: int = 500 * 1024 * 1024) -> bool:
    """True when a directory holds real weights, not a metadata stub.

    A HF cache entry exists as soon as anything is fetched, including a few KB
    of metadata, so presence of the directory means nothing.
    """
    total = 0
    for root, _dirs, files in os.walk(path):
        for fn in files:
            if fn.endswith((".safetensors", ".gguf", ".bin")):
                try:
                    total += os.path.getsize(os.path.join(root, fn))
                except OSError:
                    pass
            if total >= min_bytes:
                return True
    return False


def discover_local_models() -> List[Dict[str, Any]]:
    """Chat-capable local models with real weights, from both model roots."""
    found: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for entry in sorted(os.listdir(HF_CACHE)) if os.path.isdir(HF_CACHE) else []:
        if not entry.startswith("models--"):
            continue
        model_id = entry[len("models--"):].replace("--", "/")
        low = model_id.lower()
        if any(t in low for t in ("whisper", "kokoro", "embed", "clip", "rerank", "bge")):
            continue
        path = os.path.join(HF_CACHE, entry)
        if not _dir_has_weights(path):
            continue
        if model_id not in seen:
            seen.add(model_id)
            found.append({"id": model_id, "source": "hf_cache"})

    if os.path.isdir(MODELS_DIR):
        for entry in sorted(os.listdir(MODELS_DIR)):
            path = os.path.join(MODELS_DIR, entry)
            if os.path.isdir(path) and _dir_has_weights(path) and entry not in seen:
                seen.add(entry)
                found.append({"id": path, "source": "codec_models_dir"})
    return found


# ── Reading and writing the choice ───────────────────────────────────────────

def get_provider(cfg: Optional[Dict[str, Any]] = None) -> str:
    c = cfg if cfg is not None else _load_config()
    p = c.get("llm_provider_mode")
    return p if p in PROVIDERS else ""


def set_provider(mode: str, *, model: str = "", base_url: str = "",
                 api_key: str = "") -> Dict[str, Any]:
    """Record a provider choice. Does NOT mark it connected — verify() does."""
    if mode not in PROVIDERS:
        return {"ok": False, "error": f"unknown provider: {mode}"}

    cfg = _load_config()
    cfg["llm_provider_mode"] = mode

    if mode == "local":
        chosen = model or BUNDLED_LOCAL_MODEL
        cfg["llm_base_url"] = LOCAL_BASE_URL
        cfg["llm_model"] = chosen
        cfg["llm_provider"] = "mlx"
        # The local server needs no bearer; a stale cloud key must not be sent
        # to it, and the model server must actually be serving THIS model —
        # codec_models.restart_server() makes the launcher preload it.
        _store_key("")
    elif mode == "ava":
        ava = cfg.get("ava") if isinstance(cfg.get("ava"), dict) else {}
        cfg["llm_base_url"] = (ava.get("proxy_url") or AVA_PROXY_URL).rstrip("/") + "/v1"
        cfg["llm_model"] = model or ava.get("default_cloud_model") or AVA_DEFAULT_MODEL
        cfg["llm_provider"] = "ava"
        # The proxy is authorised by the licence key, so THAT is the bearer the
        # chat path must send. Put it where the chat path looks.
        _store_key(ava.get("license_key", ""))
    else:  # custom
        if not base_url:
            return {"ok": False, "error": "a base URL is required"}
        cfg["llm_base_url"] = base_url.rstrip("/")
        cfg["llm_model"] = model or ""
        cfg["llm_provider"] = "custom"
        if api_key:
            _store_key(api_key)
    # Never on disk, whatever an older build left behind.
    cfg.pop("llm_api_key", None)

    # A new choice is unproven until verify() says otherwise.
    cfg["llm_verified_at"] = ""
    _save_config(cfg)
    if mode == "local":
        try:
            import codec_models
            codec_models.restart_server()   # preload the chosen model; best effort
        except Exception:
            pass
    return {"ok": True, "provider": mode,
            "model": cfg.get("llm_model", ""), "base_url": cfg.get("llm_base_url", "")}


def _store_key(key: str) -> None:
    """Keychain only, in the slot the chat path reads. config.json is backed up
    and pasted into support threads."""
    try:
        from codec_keychain import keychain_set
        keychain_set(KEY_SLOT, key)
    except Exception:
        try:
            from codec_keychain import _fallback_set  # type: ignore
            _fallback_set(KEY_SLOT, key)
        except Exception:
            pass
    # codec_config caches secrets for 30s; a chat sent right after setup must
    # not read a stale empty value.
    try:
        import codec_config
        cache = getattr(codec_config, "_SECRET_CACHE", None)
        if isinstance(cache, dict):
            cache.pop(KEY_SLOT, None)
    except Exception:
        pass


def get_key() -> str:
    """Read the SAME slot the chat path reads — so verify() proves what chat
    will actually send, not what setup happens to remember."""
    try:
        from codec_keychain import keychain_get
        return keychain_get(KEY_SLOT) or ""
    except Exception:
        return ""


# ── Proving it works ─────────────────────────────────────────────────────────

def verify(timeout: float = 45.0) -> Dict[str, Any]:
    """Ask the configured endpoint a real question.

    Returns {ok, detail, model, base_url}. On success, stamps
    `llm_verified_at` — the only thing that clears the first-run screen.

    Every failure reports WHY in the user's terms. "Something went wrong" is
    what made the original breakage take a day to find.
    """
    cfg = _load_config()
    base_url = cfg.get("llm_base_url", "")
    model = cfg.get("llm_model", "")
    mode = get_provider(cfg)

    if not mode:
        return {"ok": False, "detail": "No provider chosen yet."}
    if not base_url:
        return {"ok": False, "detail": "No base URL configured."}
    if not model:
        return {"ok": False, "detail": "No model name configured."}

    if mode == "local":
        local = {m["id"] for m in discover_local_models()}
        if model not in local:
            return {"ok": False, "model": model, "base_url": base_url,
                    "detail": (f"{model} is not downloaded on this Mac. "
                               f"Download it, or pick one of: "
                               f"{', '.join(sorted(local)) or 'none found'}.")}

    # Read the key exactly as chat will: from the shared slot, never from a
    # private copy — otherwise verify() can pass while chat fails.
    key = get_key() if mode in ("ava", "custom") else ""
    if mode == "ava" and not key:
        return {"ok": False, "detail": "No licence key — AVA cloud needs the key from your welcome email."}

    body = json.dumps({"model": model,
                       "messages": [{"role": "user", "content": "Reply with the single word: READY"}],
                       "max_tokens": 8, "temperature": 0}).encode()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions",
                                 data=body, headers=headers)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        return {"ok": False, "model": model, "base_url": base_url,
                "detail": _explain_http(e.code, detail, mode)}
    except urllib.error.URLError as e:
        return {"ok": False, "model": model, "base_url": base_url,
                "detail": (f"Could not reach {base_url} ({e.reason}). "
                           + ("Is CODEC's model server running?" if mode == "local"
                              else "Check the URL and your internet connection."))}
    except Exception as e:
        return {"ok": False, "model": model, "base_url": base_url,
                "detail": f"{type(e).__name__}: {e}"}

    try:
        reply = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return {"ok": False, "model": model, "base_url": base_url,
                "detail": ("The endpoint answered, but not in OpenAI chat format. "
                           "Check the base URL ends at /v1.")}

    cfg["llm_verified_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _save_config(cfg)
    return {"ok": True, "model": model, "base_url": base_url,
            "detail": f"Answered in {time.time() - t0:.1f}s",
            "reply": (reply or "").strip()[:80]}


def _explain_http(code: int, detail: str, mode: str) -> str:
    if code in (401, 403):
        if mode == "ava":
            return "Your licence key was rejected. Check it is the key from your welcome email."
        return "The API key was rejected (401). Check the key and that it matches this endpoint."
    if code == 404:
        return f"The endpoint returned 404 — the model name may be wrong, or the URL should end at /v1. ({detail[:120]})"
    if code == 429:
        return "Rate-limited by the provider (429). Wait a moment and try again."
    if 500 <= code < 600:
        return f"The provider returned a server error ({code}). {detail[:120]}"
    return f"HTTP {code}: {detail[:160]}"


def _local_server_listening(timeout: float = 1.0) -> bool:
    import socket
    from urllib.parse import urlparse
    u = urlparse(LOCAL_BASE_URL)
    try:
        with socket.create_connection((u.hostname or "localhost", u.port or 8083), timeout=timeout):
            return True
    except OSError:
        return False


def status() -> Dict[str, Any]:
    """What the first-run screen asks: is there a working brain?"""
    cfg = _load_config()
    mode = get_provider(cfg)
    local_up = _local_server_listening()
    return {
        # A buyer's build cannot serve a local model unless the model server
        # is part of the fleet AND running. Say so instead of offering an option
        # that fails after a 4 GB download.
        "local_available": local_up,
        "local_reason": "" if local_up else "CODEC's local model server is not running on this Mac.",
        "connected": bool(mode and cfg.get("llm_verified_at")),
        "provider": mode,
        "model": cfg.get("llm_model", ""),
        "base_url": cfg.get("llm_base_url", ""),
        "verified_at": cfg.get("llm_verified_at", ""),
        "local_models": discover_local_models(),
        "bundled_model": BUNDLED_LOCAL_MODEL,
        "has_license": bool((cfg.get("ava") or {}).get("license_key")),
    }
