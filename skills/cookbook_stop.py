"""CODEC Skill: Stop a Cookbook-served model (guarded; confirm required)."""
import re

from codec_cookbook import args, serve

SKILL_NAME = "cookbook_stop"
SKILL_DESCRIPTION = (
    "Stop a model Cookbook started (by id, PM2 name, or port). Refuses anything "
    "outside the cookbook- namespace, any protected/non-cookbook port, and "
    "requires explicit confirmation. NEVER stops an existing service."
)
SKILL_TAGS = ["cookbook", "models", "stop", "local-llm"]
SKILL_TRIGGERS = [
    "cookbook stop", "stop the model", "stop cookbook model", "unload model",
    "shut down local model",
]
SKILL_MCP_EXPOSE = False  # stops PM2 processes — local/dashboard/voice only (cf. pm2_control)


def _resolve_target(task):
    """Figure out what the user means: an explicit cookbook- pm2 name, a Cookbook
    port (8110-8119), or a model id mapped via served.json."""
    # explicit full pm2 name
    m = re.search(r"\bcookbook-[A-Za-z0-9_.\-]+\b", task or "")
    if m:
        return m.group(0), None
    # explicit Cookbook port
    port = args.parse_port(task)
    if port is not None:
        return port, None
    # model id → look up our served record(s)
    model_id = args.parse_model_id(task)
    if model_id:
        matches = [r for r in serve.list_served() if r.get("id") == model_id]
        if len(matches) == 1:
            return matches[0]["pm2_name"], None
        if len(matches) > 1:
            names = ", ".join(r["pm2_name"] for r in matches)
            return None, (f"Multiple {model_id} instances are served ({names}). "
                          f"Specify the port or full pm2 name.")
        return None, (f"Cookbook isn't serving '{model_id}'. "
                      f"Use 'cookbook list' to see what's running.")
    return None, None


def run(task, app="", ctx=""):
    target, msg = _resolve_target(task)
    if msg:
        return msg
    if target is None:
        return ("Which model? Say e.g. 'cookbook stop llama32-3b confirm', "
                "or give a port (8110-8119) or the full cookbook- pm2 name. "
                "Use 'cookbook list' to see running models.")

    confirm = args.parse_flag(task, "confirm")
    res = serve.stop(target, confirm=confirm)
    status = res.get("status")

    if status == "would_stop":
        return (f"About to stop {res['pm2_name']} (port {res['port']}). "
                f"Re-run with 'confirm' to actually stop it: "
                f"cookbook stop {res['pm2_name']} confirm")
    if status == "stopped":
        return f"🛑 Stopped {res['pm2_name']} (port {res['port']}) and freed the Cookbook port."
    if status == "refused":
        reason = res.get("reason")
        if reason == "protected_port":
            return f"⛔ Refused: port {res['port']} is a protected core service — Cookbook never touches it."
        if reason == "not_a_cookbook_process":
            return (f"⛔ Refused: '{res['target']}' isn't a model Cookbook started. "
                    f"Cookbook only stops its own cookbook- processes.")
        if reason == "not_cookbook_namespace":
            return f"⛔ Refused: '{res['pm2_name']}' is not in the cookbook- namespace."
        return f"⛔ Refused: {reason}"
    return f"❌ {res.get('reason', 'unknown error')}: {res.get('detail', '')}".strip()
