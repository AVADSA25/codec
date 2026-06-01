"""CODEC Skill: Cookbook model recommendation (read-only)."""
from codec_cookbook import args, catalog, fit, probe

SKILL_NAME = "cookbook_recommend"
SKILL_DESCRIPTION = (
    "Recommend which catalog models fit this Mac's current unified-memory "
    "headroom, ranked biggest-that-fits first. Optionally filter by role "
    "(chat/reason/code/max/fast/tiny). Read-only."
)
SKILL_TAGS = ["cookbook", "models", "recommend", "fit", "local-llm"]
SKILL_TRIGGERS = [
    "cookbook recommend", "recommend a model", "which model fits", "what model can i run",
    "cookbook suggest", "best model for",
]
SKILL_MCP_EXPOSE = True  # read-only, safe to expose


def run(task, app="", ctx=""):
    ctx_len = args.parse_context(task)
    role = args.parse_role(task)
    entries = catalog.by_role(role) if role else catalog.all_entries()
    if not entries:
        return f"No catalog models with role '{role}'. Roles: chat, reason, code, max, fast, tiny."
    avail = probe.available_gb()
    ranked = fit.recommend(entries, avail, ctx_len)
    header = (f"Models for ~{round(avail, 1)} GB headroom @ {ctx_len}-token context"
              + (f" (role: {role})" if role else "") + ":")
    lines = [header]
    for r in ranked:
        e = r["entry"]
        mark = "✅" if r["fits"] else "✗"
        lines.append(
            f"  {mark} {e['id']:<16} ~{r['need_gb']} GB  "
            f"(headroom {r['headroom_gb']:+} GB)  [{','.join(e.get('roles', []))}]"
        )
    top = next((r for r in ranked if r["fits"]), None)
    if top:
        lines.append(f"\n→ Best fit: {top['entry']['id']}. "
                     f"Serve it with: cookbook serve {top['entry']['id']}")
    else:
        lines.append("\n→ Nothing fits the current headroom. Free memory or pick a smaller context.")
    return "\n".join(lines)
