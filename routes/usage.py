"""Settings > Usage (UI phase 3, P3.11; docs/P3.11-DESIGN.md).

This month's spend against the cap for each registered cloud model (straight
from codec_cloud_models), whether CODEC is on the cloud right now and why, the
automatic cloud fallback picker (only registered cloud entries, or off), local
against cloud replies and the busiest skills over 7 days, and the reply speed
over 14 days (codec_usage's per-reply log).
"""
from __future__ import annotations

import json
import os
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()

CONFIG_PATH = os.path.expanduser("~/.codec/config.json")
FALLBACK_NOTE = ("When the local model server stops answering, CODEC moves to this cloud model before the next chat "
                 "or voice reply, and back to the local model once it answers twice in a row. Its replies count toward "
                 "that model's monthly cap; at the cap it stops answering.")


def _config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


def _month_label(month: str) -> str:
    try:
        return datetime.strptime(month, "%Y-%m").strftime("%B %Y")
    except ValueError:
        return month


@router.get("/api/usage")
def usage():
    import codec_cloud_models as ccm
    import codec_usage
    cfg = _config()
    ledger = ccm._read_spend()
    months = sorted((m for m in ledger if isinstance(ledger.get(m), dict)), reverse=True)[:6]
    cloud = []
    for e in ccm.entries(cfg):
        cloud.append({"id": e["id"], "label": str(e.get("label") or e["id"]),
                      "spent": round(ccm.spent_usd(e["id"]), 2), "cap": ccm.cap_usd(e),
                      "months": [{"month": m, "label": _month_label(m),
                                  "usd": round(float(((ledger[m].get(e["id"]) or {}).get("usd") or 0)), 2)}
                                 for m in months if isinstance(ledger[m].get(e["id"]), dict)]})
    active = ccm.active_entry(cfg)
    flag = cfg.get("llm_auto_fallback_active")
    flag = flag if isinstance(flag, dict) else None
    now = {"cloud": active is not None, "label": str(active.get("label") or active["id"]) if active else None,
           "auto": bool(active and flag),
           "since": datetime.fromtimestamp(flag["at"]).isoformat(timespec="seconds")
           if flag and isinstance(flag.get("at"), (int, float)) else None}
    choice = cfg.get("llm_auto_fallback")
    fallback = {"value": choice if ccm.entry_for_id(choice, cfg) else None,
                "choices": [{"id": e["id"], "label": str(e.get("label") or e["id"])} for e in ccm.entries(cfg)],
                "note": FALLBACK_NOTE}
    return {"cloud": cloud, "now": now, "fallback": fallback,
            "replies": codec_usage.reply_summary(days=7, speed_days=14),
            "skills": codec_usage.busiest_skills(days=7)}


@router.put("/api/usage/auto_fallback")
async def set_auto_fallback(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = None
    value = (body or {}).get("value") if isinstance(body, dict) else None
    import codec_cloud_models as ccm
    import codec_jsonstore
    if value is not None and (not isinstance(value, str) or ccm.entry_for_id(value, _config()) is None):
        return JSONResponse({"error": "Pick one of the registered cloud models, or off."}, status_code=400)
    with codec_jsonstore.file_lock(CONFIG_PATH):
        cfg = _config()
        if value is None:
            cfg.pop("llm_auto_fallback", None)
        else:
            cfg["llm_auto_fallback"] = value
        codec_jsonstore.atomic_write_json(CONFIG_PATH, cfg)
    try:
        from codec_audit import log_event
        log_event("auto_fallback_set", "codec-dashboard",
                  f"Automatic cloud fallback {'set to ' + value if value else 'off'}", extra={"value": value})
    except Exception:
        pass
    return {"value": value}
