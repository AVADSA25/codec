"""CODEC Skill: Serve a local model on a dedicated Cookbook port (8110-8119)."""
from codec_cookbook import args, catalog, fit, probe, serve

SKILL_NAME = "cookbook_serve"
SKILL_DESCRIPTION = (
    "Serve a local MLX or GGUF model on a dedicated Cookbook port (8110-8119) "
    "under PM2, after a unified-memory fit check. Never touches existing services."
)
SKILL_TAGS = ["cookbook", "models", "serve", "mlx", "local-llm"]
SKILL_TRIGGERS = [
    "cookbook serve", "serve the model", "spin up a local model", "load model on cookbook",
    "start a local model",
]
SKILL_MCP_EXPOSE = False  # starts PM2 processes — local/dashboard/voice only (cf. pm2_control)


def run(task, app="", ctx=""):
    model_id = args.parse_model_id(task)
    if not model_id:
        return ("Which model? Say e.g. 'cookbook serve qwen3-30b-a3b'. Known: "
                + ", ".join(catalog.ids()))
    try:
        entry = catalog.get(model_id)
    except KeyError as e:
        return str(e)

    context_length = args.parse_context(task)
    force = args.parse_flag(task, "force")

    # Fit check (conservative over-estimate vs. live headroom).
    need = fit.estimate_footprint_gb(entry["hf_repo"], context_length, entry.get("anchor_gb"))
    avail = probe.available_gb()
    ok, headroom = fit.fits(need, avail)
    if not ok and not force:
        return (f"⚠ Refused to serve {model_id}: insufficient memory.\n"
                f"   need ~{round(need, 1)} GB, headroom {round(headroom, 1)} GB "
                f"(margin {fit.DEFAULT_MARGIN_GB} GB).\n"
                f"   Free memory, lower the context, or re-run with 'force' to override.")

    res = serve.launch(entry, context_length)
    status = res.get("status")
    if status == "serving":
        return (f"✅ Serving {model_id} on port {res['port']} "
                f"(pm2: {res['pm2_name']}, ctx {context_length}, ~{round(need, 1)} GB). "
                f"OpenAI-compatible at http://127.0.0.1:{res['port']}/v1")
    if status == "started_unhealthy":
        return (f"⏳ Started {res['pm2_name']} on port {res['port']} but it didn't pass the "
                f"/v1/models health check in time. Check: pm2 logs {res['pm2_name']}")
    return f"❌ Could not serve {model_id}: {res.get('reason')} {res.get('detail', '')}".strip()
