"""CODEC Compare — fan one prompt out across model tiers, collect, return
labeled or blind.

Sits directly on top of the rest of the stack — it reuses the canonical
callers rather than re-implementing HTTP:
  * OpenAI-compatible endpoints (local Qwen @ 8083, every Cookbook-served
    model on its 811x port) → `codec_llm.call`
  * cloud tiers (Gemini/Claude/GPT via the AVA proxy) → `codec_ava_client`

Endpoint set = three canonical tiers + anything Cookbook is currently serving:
  1. local          — the local Qwen (config llm_base_url / llm_model)
  2. cloud-balanced  — a mid cloud model via AVA (default gemini-2.5-flash)
  3. cloud-pro       — a top cloud model via AVA (default gemini-2.5-pro)
  + cookbook-<id>    — each healthy model from codec_cookbook.serve.list_served()

The two cloud tiers + their model ids are overridable in
~/.codec/config.json:compare.cloud_tiers (a list of {label, model}); the
defaults above are grounded in codec_ava_client.choose_model's fast/balanced/pro
map. The fan-out is concurrent, per-endpoint timed, and never lets one
endpoint's failure sink the others.
"""
from __future__ import annotations

import json
import logging
import os
import string
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

log = logging.getLogger("codec_compare")

_CONFIG_PATH = os.path.expanduser("~/.codec/config.json")
_DEFAULT_CLOUD_TIERS = [
    {"label": "cloud-balanced", "model": "gemini-2.5-flash"},
    {"label": "cloud-pro", "model": "gemini-2.5-pro"},
]
_MAX_TOKENS = 1024
_PER_ENDPOINT_TIMEOUT_S = 60
_MAX_WORKERS = 6


def _load_cfg() -> dict:
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _cloud_tiers(cfg: dict) -> list[dict]:
    tiers = (cfg.get("compare") or {}).get("cloud_tiers")
    if isinstance(tiers, list) and tiers:
        return [t for t in tiers if t.get("model")]
    return list(_DEFAULT_CLOUD_TIERS)


def _cookbook_endpoints() -> list[dict]:
    """Every healthy Cookbook-served model as an OpenAI endpoint. Best-effort —
    if Cookbook isn't installed / nothing is served, returns []."""
    eps = []
    try:
        from codec_cookbook import serve
        for r in serve.list_served():
            port = r.get("port")
            if not port:
                continue
            if r.get("pm2_status") not in (None, "online") or r.get("healthy") is False:
                continue  # skip stopped/unhealthy
            eps.append({
                "label": f"cookbook-{r.get('id', port)}",
                "kind": "openai",
                "model": r.get("hf_repo") or str(r.get("id")),
                "base_url": f"http://127.0.0.1:{port}/v1",
                "tier": "cookbook",
            })
    except Exception as e:
        log.debug("cookbook endpoint discovery skipped: %s", e)
    return eps


def default_endpoints() -> list[dict]:
    """The canonical comparison set: local + available cloud tiers + Cookbook."""
    cfg = _load_cfg()
    eps: list[dict] = [{
        "label": "local",
        "kind": "openai",
        "model": cfg.get("llm_model", "local-qwen"),
        "base_url": cfg.get("llm_base_url", "http://localhost:8083/v1"),
        "tier": "local",
    }]
    # cloud tiers via AVA — only when the license/proxy is actually ready
    try:
        import codec_ava_client
        ava = codec_ava_client.load_config()
        if ava and ava.is_ready():
            for t in _cloud_tiers(cfg):
                eps.append({"label": t["label"], "kind": "ava",
                            "model": t["model"], "tier": "cloud"})
    except Exception as e:
        log.debug("AVA cloud tiers unavailable: %s", e)
    eps.extend(_cookbook_endpoints())
    return eps


def _query_one(ep: dict, prompt: str, system: Optional[str], timeout: int) -> dict:
    """Query a single endpoint. Never raises — failures are captured as
    {ok: False, error}. Returns the endpoint dict enriched with the result."""
    t0 = time.monotonic()
    base = {k: ep.get(k) for k in ("label", "model", "tier")}
    try:
        if ep["kind"] == "ava":
            import codec_ava_client
            text = codec_ava_client.ava_chat_simple(
                prompt, system=system, model=ep["model"],
                max_tokens=_MAX_TOKENS, timeout=timeout)
        else:  # openai-compatible (local + cookbook)
            import codec_llm
            messages = ([{"role": "system", "content": system}] if system else []) \
                + [{"role": "user", "content": prompt}]
            text = codec_llm.call(
                messages, base_url=ep["base_url"], model=ep["model"],
                max_tokens=_MAX_TOKENS, timeout=timeout, raise_on_error=True)
        return {**base, "ok": True, "response": (text or "").strip(),
                "elapsed_ms": round((time.monotonic() - t0) * 1000)}
    except Exception as e:
        return {**base, "ok": False, "error": str(e)[:300],
                "elapsed_ms": round((time.monotonic() - t0) * 1000)}


def compare(prompt: str, *, endpoints: Optional[list[dict]] = None,
            blind: bool = False, system: Optional[str] = None,
            timeout: int = _PER_ENDPOINT_TIMEOUT_S,
            max_workers: int = _MAX_WORKERS) -> dict:
    """Fan `prompt` out across `endpoints` (default: default_endpoints())
    concurrently and collect every reply.

    Returns {prompt, blind, results:[{label|display, model, tier, ok, response|error,
    elapsed_ms}], mapping?}. In blind mode each result's display label is
    anonymized (Model A/B/…) and a `mapping` of anon→real is returned separately
    so the caller decides whether/when to reveal it.
    """
    if not prompt or not prompt.strip():
        return {"prompt": "", "blind": blind, "results": [], "note": "empty prompt"}
    eps = endpoints if endpoints is not None else default_endpoints()
    if not eps:
        return {"prompt": prompt[:200], "blind": blind, "results": [],
                "note": "no endpoints available"}

    workers = max(1, min(max_workers, len(eps)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        # ex.map preserves input order
        results = list(ex.map(lambda e: _query_one(e, prompt, system, timeout), eps))

    out = {"prompt": prompt[:200], "blind": blind, "results": results}
    if blind:
        anon = {}
        for i, r in enumerate(results):
            tag = f"Model {string.ascii_uppercase[i % 26]}"
            anon[tag] = r["label"]
            r["display"] = tag
        out["mapping"] = anon
    else:
        for r in results:
            r["display"] = r["label"]
    return out
