"""CODEC Skill: List Cookbook-served models (read-only)."""
from codec_cookbook import serve

SKILL_NAME = "cookbook_list"
SKILL_DESCRIPTION = (
    "List the local models Cookbook is currently serving (port, PM2 name, "
    "live status, health). Only shows Cookbook-managed processes. Read-only."
)
SKILL_TAGS = ["cookbook", "models", "list", "local-llm"]
SKILL_TRIGGERS = [
    "cookbook list", "list served models", "cookbook models", "what models are running",
    "cookbook ps",
]
SKILL_MCP_EXPOSE = True  # read-only, safe to expose


def run(task, app="", ctx=""):
    served = serve.list_served()
    if not served:
        return "Cookbook isn't serving any models. Use 'cookbook serve <id>' to start one."
    lines = [f"Cookbook-served models ({len(served)}):"]
    for r in served:
        health = "healthy" if r.get("healthy") else "no response"
        lines.append(
            f"  • {r.get('id', '?'):<16} port {r.get('port')}  "
            f"[{r.get('pm2_status', '?')}/{health}]  {r.get('pm2_name')}  "
            f"(ctx {r.get('context')}, {r.get('backend')})"
        )
    return "\n".join(lines)
