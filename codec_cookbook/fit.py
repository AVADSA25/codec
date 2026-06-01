"""Cookbook unified-memory fit math.

Weight size comes from the model's ACTUAL Hugging Face file sizes; KV-cache
geometry from its real `config.json`. No fabricated architecture constants —
the only tunables are the conservative over-estimate (×1.10 + 1.5 GB) and the
deployment safety figures (os_reserve=24, margin=8), which are policy, not
architecture.

MoE note: footprint = TOTAL params (every expert is resident in unified
memory); the active-param count affects speed, not memory. So the anchors are
the full-model resident sizes (Qwen3-30B-A3B-4bit ≈ 17.2 GB,
Qwen3-Next-80B-A3B-4bit ≈ 42 GB).

Network boundary: weight_gb_from_hub() and load_config() touch the Hub.
estimate_footprint_gb() accepts `anchor_gb` (skip the weight call) and `cfg`
(skip the config fetch) so callers — and tests — can run fully offline.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

log = logging.getLogger("codec_cookbook.fit")

WEIGHT_EXT = (".safetensors", ".gguf", ".npz")
DEFAULT_OS_RESERVE_GB = 24
DEFAULT_MARGIN_GB = 8
_OVERHEAD_MULT = 1.10   # +10% for activations / fragmentation
_OVERHEAD_FLAT_GB = 1.5  # runtime / framework baseline


def weight_gb_from_hub(repo: str) -> float:
    """Sum of the model's weight-file sizes (GB) from the Hub. Network."""
    from huggingface_hub import HfApi
    info = HfApi().model_info(repo, files_metadata=True)
    total = sum((f.size or 0) for f in (info.siblings or [])
                if f.rfilename.endswith(WEIGHT_EXT))
    return total / 1e9


def load_config(repo: str) -> dict:
    """Fetch + parse the model's config.json from the Hub. Network."""
    from huggingface_hub import hf_hub_download
    path = hf_hub_download(repo, "config.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def kv_cache_gb(cfg: dict, ctx: int) -> float:
    """fp16 K+V cache size (GB) for `ctx` tokens, from real config geometry.

    GQA-aware: uses num_key_value_heads when present (falls back to
    num_attention_heads for MHA models). head_dim falls back to
    hidden_size / num_attention_heads.
    """
    nl = cfg["num_hidden_layers"]
    nkv = cfg.get("num_key_value_heads", cfg["num_attention_heads"])
    hd = cfg.get("head_dim", cfg["hidden_size"] // cfg["num_attention_heads"])
    return (2 * nl * nkv * hd * ctx * 2) / 1e9  # 2 (K+V) * ... * 2 bytes (fp16)


def estimate_footprint_gb(repo: str, ctx: int,
                          anchor_gb: Optional[float] = None,
                          cfg: Optional[dict] = None) -> float:
    """Conservative resident footprint (GB) = (weight + KV) × 1.10 + 1.5.

    weight: `anchor_gb` if given, else weight_gb_from_hub(repo).
    KV:     from `cfg` if given, else load_config(repo). If the config can't
            be obtained (offline + no anchor cfg), KV is omitted and a warning
            is logged — the estimate then under-counts KV, so callers relying
            on the conservative guarantee should supply anchor_gb + cfg.
    """
    w = anchor_gb if anchor_gb is not None else weight_gb_from_hub(repo)
    kv = 0.0
    try:
        c = cfg if cfg is not None else load_config(repo)
        kv = kv_cache_gb(c, ctx)
    except Exception as e:  # offline / missing config — best-effort
        log.warning("KV geometry unavailable for %s (%s) — footprint omits KV", repo, e)
    return (w + kv) * _OVERHEAD_MULT + _OVERHEAD_FLAT_GB


def available_gb(unified_total_gb: float, resident_gb,
                 os_reserve_gb: int = DEFAULT_OS_RESERVE_GB) -> float:
    """Unified memory free for a new model = total − os_reserve − Σ resident."""
    return unified_total_gb - os_reserve_gb - sum(resident_gb)


def fits(need_gb: float, avail_gb: float,
         margin_gb: int = DEFAULT_MARGIN_GB) -> tuple[bool, float]:
    """(ok, headroom). ok requires headroom ≥ margin_gb. headroom may be
    negative (returned for the refusal message)."""
    headroom = avail_gb - need_gb
    return headroom >= margin_gb, headroom


def quick_need_gb(entry: dict, ctx: int) -> float:
    """Best-effort footprint for a catalog entry, preferring its anchor_gb so
    ranking doesn't require a weight download. KV is added when config is
    reachable (best-effort). Used by the recommend skill."""
    return estimate_footprint_gb(entry["hf_repo"], ctx, anchor_gb=entry.get("anchor_gb"))


def recommend(entries: list[dict], avail_gb: float, ctx: int,
              margin_gb: int = DEFAULT_MARGIN_GB) -> list[dict]:
    """Rank catalog entries against available memory. Returns a list of
    {entry, need_gb, fits, headroom_gb} sorted fit-first then largest-that-fits
    (more capable), so the top recommendation is the biggest model that fits."""
    scored = []
    for e in entries:
        need = quick_need_gb(e, ctx)
        ok, headroom = fits(need, avail_gb, margin_gb)
        scored.append({
            "entry": e,
            "need_gb": round(need, 1),
            "fits": ok,
            "headroom_gb": round(headroom, 1),
        })
    # fits first; within fits, biggest need (most capable) first; within
    # non-fits, smallest need (closest to fitting) first.
    scored.sort(key=lambda s: (not s["fits"],
                               -s["need_gb"] if s["fits"] else s["need_gb"]))
    return scored
