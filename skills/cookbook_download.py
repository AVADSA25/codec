"""CODEC Skill: Download a catalog model from Hugging Face (background job)."""
from codec_cookbook import args, catalog, download

SKILL_NAME = "cookbook_download"
SKILL_DESCRIPTION = (
    "Download a Cookbook catalog model from Hugging Face into the local cache "
    "as a background job, or report an in-flight download's status."
)
SKILL_TAGS = ["cookbook", "models", "download", "huggingface", "local-llm"]
SKILL_TRIGGERS = [
    "cookbook download", "download the model", "fetch a model", "download model status",
    "cookbook fetch",
]
SKILL_MCP_EXPOSE = False  # spawns network download jobs — local/dashboard/voice only


def run(task, app="", ctx=""):
    model_id = args.parse_model_id(task)
    if not model_id:
        return ("Which model? Say e.g. 'cookbook download qwen3-coder-30b'. Known: "
                + ", ".join(catalog.ids()))
    try:
        entry = catalog.get(model_id)
    except KeyError as e:
        return str(e)
    repo = entry["hf_repo"]

    # "status" / "progress" → report only; otherwise start (idempotent).
    want_status = args.parse_flag(task, "status") or "progress" in (task or "").lower()
    res = download.status(repo) if want_status else download.start(repo)

    state = res.get("state")
    if state == "not_started":
        return f"No download in progress for {model_id}. Say 'cookbook download {model_id}' to start."
    if state in ("starting", "running"):
        return f"⏳ Downloading {model_id} ({repo}) — state: {state} (pid {res.get('pid')})."
    if state == "done":
        return f"✅ {model_id} downloaded → {res.get('path', 'HF cache')}."
    if state == "interrupted":
        return f"⚠ Download of {model_id} was interrupted. Re-run 'cookbook download {model_id}'."
    if state == "error":
        return f"❌ Download of {model_id} failed: {res.get('error', 'unknown')}"
    return f"{model_id}: {state}"
