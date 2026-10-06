"""Model compare from a reply (UI phase 3, P3.10; docs/P3.10-DESIGN.md).

The question behind a Chat reply goes through codec_compare to the local model
and one other target: a model Cookbook serves, or a registered cloud model
(codec_cloud_models) after an explicit per-compare opt-in. Every leg goes
through codec_llm.call, so for a cloud model the licence gate, its key, the
monthly cap and the spend ledger apply as for any cloud reply. The AVA tiers the
compare skill also uses are not offered here: they do not pass that capped path.
"""
from __future__ import annotations

import json
import os

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

router = APIRouter()

CONFIG_PATH = os.path.expanduser("~/.codec/config.json")
MAX_PROMPT = 8000
_RESULT_KEYS = ("label", "model", "tier", "ok", "response", "error", "elapsed_ms", "tokens", "tokens_estimated", "tok_s")


def _config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


def _label(model) -> str:
    s = str(model or "").split("/")[-1].replace("-4bit", "").replace("-", " ").strip()
    return s or "Local model"


def local_endpoint(cfg: dict) -> dict:
    """The local model: Chat's model when it is local, else the one a switch back restores."""
    import codec_cloud_models as ccm
    base, model = ccm.local_only(str(cfg.get("llm_base_url") or ccm.DEFAULT_LOCAL_BASE_URL),
                                 str(cfg.get("llm_model") or ""), cfg)
    return {"id": "local", "label": _label(model) + " (this Mac)", "kind": "openai", "model": model,
            "base_url": base, "tier": "local"}


def targets(cfg: dict) -> list:
    """Model B choices: each healthy Cookbook model (local), then each registered cloud model."""
    import codec_cloud_models as ccm
    import codec_compare
    out = []
    for ep in codec_compare._cookbook_endpoints():
        out.append({"id": ep["label"], "label": _label(ep.get("model")) + " (Cookbook)", "kind": "local",
                    "endpoint": ep})
    for e in ccm.entries(cfg):
        name = str(e.get("label") or e["id"])
        out.append({"id": "cloud:" + e["id"], "label": name, "kind": "cloud",
                    "spent": round(ccm.spent_usd(e["id"]), 2), "cap": ccm.cap_usd(e),
                    "endpoint": {"label": name, "kind": "openai", "model": e["id"],
                                 "base_url": str(e["base_url"]).strip().rstrip("/"), "tier": "cloud"}})
    return out


@router.get("/api/compare/targets")
def compare_targets():
    cfg = _config()
    loc = local_endpoint(cfg)
    return {"local": {k: loc[k] for k in ("id", "label", "model")},
            "targets": [{k: v for k, v in t.items() if k != "endpoint"} for t in targets(cfg)]}


@router.post("/api/compare")
async def compare_run(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = None
    body = body if isinstance(body, dict) else {}
    prompt, target = body.get("prompt"), body.get("target")
    if not isinstance(prompt, str) or not prompt.strip():
        return JSONResponse({"error": "There is nothing to compare."}, status_code=400)
    if len(prompt) > MAX_PROMPT:
        return JSONResponse({"error": f"The question is too long to compare (over {MAX_PROMPT} characters)."},
                            status_code=400)
    cfg = _config()
    t = next((x for x in targets(cfg) if x["id"] == target), None)
    if t is None:
        return JSONResponse({"error": "Pick a model to compare with."}, status_code=400)
    if t["kind"] == "cloud" and body.get("cloud_ok") is not True:
        return JSONResponse({"error": "This compare would use a cloud model: tick the cloud box first."},
                            status_code=403)
    import codec_compare
    local = local_endpoint(cfg)
    res = await run_in_threadpool(codec_compare.compare, prompt.strip(), endpoints=[local, t["endpoint"]])
    results = [{k: r.get(k) for k in _RESULT_KEYS} for r in (res.get("results") or [])]
    for r, label in zip(results, (local["label"], t["label"])):
        r["label"] = label
    try:
        from codec_audit import log_event
        log_event("compare_run", "codec-dashboard", "Model compare from a reply",
                  extra={"target": t["id"], "kind": t["kind"], "ok": [bool(r.get("ok")) for r in results]})
    except Exception:
        pass
    return {"results": results}
