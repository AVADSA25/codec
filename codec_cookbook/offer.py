"""Offer the best local model that fits this Mac (P-039, docs/MODEL-FIT-OFFER-DESIGN.md).

Settings > Language model reads build(): the largest catalog model that fits
this Mac's memory and whose licence allows commercial use, with its size, its
licence notice and whether it is downloaded or in use. start_download() fetches
it with the Cookbook download job (Hugging Face cache, pinned revision); use()
switches to it through codec_models.set_active, the existing switch, probe and
revert.

Each guard refuses with a sentence the page shows:
  * a model that does not fit this Mac is never downloaded or switched to;
  * a model whose licence is not in catalog.COMMERCIAL_LICENCES is never offered;
  * no switch while an image job holds ~/.codec/image_job.lock (codec_image);
  * no download without its size plus DISK_SPARE_GB free on disk.
The fit uses the RAM rule in fit.os_reserve_for / fit.margin_for, so a 64 GB
Mac ranks exactly as the Cookbook always did.
"""
from __future__ import annotations

import logging
import os
import shutil
from typing import Any, Optional

from . import catalog, download, fit, probe

log = logging.getLogger("codec_cookbook.offer")

CTX = 8192                    # context the fit is computed for (the Cookbook default)
# A chat brain: an entry with only the "code" role is not offered as one.
OFFER_ROLES = frozenset({"chat", "reason", "max", "fast", "tiny"})
DISK_SPARE_GB = 2.0           # left free on disk after a download
_BIGGER_BY_GB = 0.5           # the best fit counts as bigger than the model in use past this


# ── what this Mac has (each one patched in tests) ─────────────────────────────

def _local_sizes() -> dict:
    """{hub id: size_gb} of the chat models with real weights in the HF cache."""
    try:
        import codec_models
        return {m["id"]: m.get("size_gb") for m in codec_models.discover_local()}
    except Exception:
        return {}


def _active_model() -> str:
    try:
        import codec_models
        return codec_models.get_active()
    except Exception:
        return ""


def _image_busy() -> str:
    """codec_image's refusal while an image job holds its lock, else ""."""
    try:
        import codec_image
        return codec_image.busy_message() or ""
    except Exception:
        return ""


def _image_min_free_gb() -> Optional[float]:
    """The free memory an image job needs, when Create image is set up here."""
    try:
        import codec_image
        s = codec_image.settings()
        return None if codec_image.unavailable_reason(s) else float(s["min_free_gb"])
    except Exception:
        return None


def _disk_free_gb(path: str) -> Optional[float]:
    """Free space (GB) on the disk that holds `path` or its nearest existing parent."""
    p = os.path.abspath(path)
    while not os.path.isdir(p):
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    try:
        return shutil.disk_usage(p).free / 1e9
    except OSError:
        return None


# ── ranking ──────────────────────────────────────────────────────────────────

def _general(entry: dict) -> bool:
    return bool(OFFER_ROLES & set(entry.get("roles") or []))


def _memory(total_gb: Optional[float], resident_gb: Optional[float]) -> dict:
    total = probe.unified_total_gb() if total_gb is None else float(total_gb)
    resident = probe.resident_gb_total() if resident_gb is None else float(resident_gb)
    reserve = fit.os_reserve_for(total)
    avail = fit.available_gb(total, [resident], reserve) if total > 0 else 0.0
    return {"total": total, "reserve": reserve, "margin": fit.margin_for(total),
            "avail": avail}


def _offered(entries: list, mem: dict) -> list:
    """fit.recommend rows for the general entries that fit and are licensed."""
    pool = [e for e in entries if _general(e)]
    ranked = fit.recommend(pool, mem["avail"], CTX, mem["margin"])
    return [r for r in ranked if r["fits"] and catalog.licence_ok(r["entry"])]


def _size_gb(entry: dict) -> float:
    return float(entry.get("size_gb") or entry.get("anchor_gb") or 0.0)


def progress(entry: dict) -> dict:
    """Download state of a catalog entry, with bytes on disk and a percent."""
    repo = entry["hf_repo"]
    st = download.status(repo)
    state = st.get("state") or "not_started"
    total_b = int(_size_gb(entry) * 1e9)
    got = download.bytes_on_disk(repo)
    if state == "done":
        pct = 100
    else:
        pct = min(99, int(100 * got / total_b)) if total_b and got else 0
    return {"id": entry["id"], "state": state, "bytes": got, "total_bytes": total_b,
            "percent": pct, "error": str(st.get("error") or "")[:300]}


def _item(row: dict, active: str, local: dict, mem: dict,
          image_min_free: Optional[float]) -> dict:
    e = row["entry"]
    repo = e["hf_repo"]
    item = {"id": e["id"], "label": e.get("label") or e["id"], "repo": repo,
            "size_gb": _size_gb(e), "need_gb": row["need_gb"],
            "headroom_gb": row["headroom_gb"], "roles": list(e.get("roles") or []),
            "licence": catalog.licence(e), "active": repo == active,
            "installed": repo in local or repo == active, "download": None,
            "image_note": ""}
    if not item["installed"]:
        item["download"] = progress(e)
    if image_min_free is not None and mem["total"] - mem["reserve"] - row["need_gb"] < image_min_free:
        item["image_note"] = (f"While this model is loaded, Create image waits: "
                              f"it needs about {image_min_free:g} GB free.")
    return item


def build(*, total_gb: Optional[float] = None, resident_gb: Optional[float] = None,
          entries: Optional[list] = None) -> dict:
    """What Settings shows: this Mac's memory, the best fit and the others that fit."""
    mem = _memory(total_gb, resident_gb)
    rows = _offered(catalog.all_entries() if entries is None else entries, mem)
    active, local = _active_model(), _local_sizes()
    image_min_free = _image_min_free_gb()
    items = [_item(r, active, local, mem, image_min_free) for r in rows]
    best = items[0] if items else None
    active_size = local.get(active)
    upgrade = bool(best and not best["active"] and isinstance(active_size, (int, float))
                   and best["size_gb"] > active_size + _BIGGER_BY_GB)
    return {"ram_gb": round(mem["total"], 1), "reserve_gb": mem["reserve"],
            "margin_gb": mem["margin"], "available_gb": round(max(mem["avail"], 0.0), 1),
            "ctx": CTX, "active": active,
            "active_local": isinstance(active_size, (int, float)),
            "upgrade": upgrade, "best": best, "others": items[1:],
            "image_busy": _image_busy()}


def _check(model_id: str, total_gb: Optional[float] = None,
           resident_gb: Optional[float] = None) -> tuple[Optional[dict], str]:
    """(entry, "") when `model_id` is offered on this Mac, else (None, why not)."""
    entry = catalog.find(model_id)
    if entry is None:
        return None, f"Unknown model: {str(model_id)[:80]}"
    label = entry.get("label") or entry["id"]
    if not catalog.licence_ok(entry):
        return None, f"{label} is not offered: its licence is not one CODEC offers."
    if not _general(entry):
        return None, f"{label} is not offered as CODEC's chat model."
    mem = _memory(total_gb, resident_gb)
    if not _offered([entry], mem):
        need = fit.quick_need_gb(entry, CTX)
        return None, (f"{label} needs about {need:.1f} GB and this Mac has about "
                      f"{max(mem['avail'], 0.0):.1f} GB for a model, so CODEC does not load it here.")
    return entry, ""


def _audit_download(entry: dict) -> None:
    try:
        from codec_audit import audit
        audit(event="model_download_started", source="codec-cookbook", outcome="ok",
              level="info", message=f"model download started: {entry['id']}",
              extra={"model": entry["id"], "repo": entry["hf_repo"],
                     "size_gb": _size_gb(entry)})
    except Exception:
        log.debug("model_download_started audit failed", exc_info=True)


def start_download(model_id: str, *, total_gb: Optional[float] = None,
                   resident_gb: Optional[float] = None) -> dict[str, Any]:
    """Start downloading an offered model (idempotent while one runs)."""
    entry, err = _check(model_id, total_gb, resident_gb)
    if entry is None:
        return {"ok": False, "error": err}
    repo = entry["hf_repo"]
    if repo in _local_sizes() or repo == _active_model():
        return {"ok": False, "error": f"{entry.get('label') or entry['id']} is already on this Mac."}
    if download.status(repo).get("state") in ("starting", "running"):
        return {"ok": True, **progress(entry)}
    size = _size_gb(entry)
    free = _disk_free_gb(download.HF_CACHE)
    if free is None:
        return {"ok": False, "error": "Could not read the free disk space, so the download did not start."}
    if free < size + DISK_SPARE_GB:
        return {"ok": False, "error": (f"The download needs about {size + DISK_SPARE_GB:.1f} GB free "
                                       f"on disk; this Mac has {free:.1f} GB.")}
    res = download.start(repo, entry.get("revision"))
    if res.get("state") == "error":
        return {"ok": False, "error": str(res.get("error") or "the download did not start")[:300]}
    _audit_download(entry)
    return {"ok": True, **progress(entry)}


def download_progress(model_id: str) -> Optional[dict]:
    """progress() for a catalog id, or None when the id is unknown."""
    entry = catalog.find(model_id)
    return progress(entry) if entry else None


def use(model_id: str, *, total_gb: Optional[float] = None,
        resident_gb: Optional[float] = None) -> dict[str, Any]:
    """Switch CODEC's chat model to an offered, downloaded model."""
    busy = _image_busy()
    if busy:
        return {"ok": False, "error": busy}
    entry, err = _check(model_id, total_gb, resident_gb)
    if entry is None:
        return {"ok": False, "error": err}
    repo = entry["hf_repo"]
    if repo == _active_model():
        return {"ok": True, "changed": False, "active": repo, "detail": "already active"}
    if repo not in _local_sizes():
        return {"ok": False, "error": f"{entry.get('label') or entry['id']} is not downloaded yet."}
    import codec_models
    return codec_models.set_active(repo)
