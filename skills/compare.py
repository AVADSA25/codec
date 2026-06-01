"""CODEC Skill: Compare one prompt across model tiers (labeled or blind)."""
import re

from codec_compare import compare

SKILL_NAME = "compare"
SKILL_DESCRIPTION = (
    "Send one prompt to multiple model tiers at once — the local Qwen, the "
    "cloud tiers (via AVA), and any model Cookbook is currently serving — and "
    "show their answers side by side. Add 'blind' to hide which model is which."
)
SKILL_TAGS = ["compare", "models", "eval", "llm", "cookbook"]
SKILL_TRIGGERS = [
    "compare models", "blind compare", "compare across models", "ask all models",
    "compare llms", "model compare", "compare prompt",
]
SKILL_MCP_EXPOSE = True  # query skill (no process/file mutation; same cost profile as chat)

# Longest-first so "compare across models" wins over "compare".
_PREFIXES = (
    "blind compare across models", "compare across models", "blind compare",
    "compare models", "ask all models", "compare llms", "model compare",
    "compare prompt", "compare",
)


def _extract_prompt(task: str) -> str:
    t = (task or "").strip()
    low = t.lower()
    for p in sorted(_PREFIXES, key=len, reverse=True):
        if low.startswith(p):
            t = t[len(p):].strip(" :->\n\t")
            break
    # drop a leading 'blind' keyword if it leaked into the prompt
    return re.sub(r"^blind\s+", "", t, flags=re.I).strip()


def run(task, app="", ctx=""):
    blind = bool(re.search(r"\bblind\b", (task or "").lower()))
    prompt = _extract_prompt(task)
    if not prompt:
        return ("What should I compare? e.g. "
                "'compare models: explain quantum tunneling in one paragraph' "
                "(prefix with 'blind' to hide the model identities).")

    res = compare(prompt, blind=blind)
    results = res.get("results", [])
    if not results:
        return f"No model endpoints available to compare ({res.get('note', 'none configured')})."

    head = (f"Compared {len(results)} model{'s' if len(results) != 1 else ''}"
            + (" — blind" if blind else "") + f" on: {res['prompt']}")
    lines = [head]
    for r in results:
        label = r.get("display") or r.get("label")
        meta = f"{r.get('elapsed_ms')}ms" + ("" if blind else f", {r.get('tier')}")
        if r.get("ok"):
            lines.append(f"\n### {label}  ({meta})\n{r.get('response', '')}")
        else:
            lines.append(f"\n### {label}  — ✗ {r.get('error', 'failed')} ({meta})")
    if blind and res.get("mapping"):
        key = "   ".join(f"{k} = {v}" for k, v in res["mapping"].items())
        lines.append(f"\n— Key (judge the answers first, then peek) —\n{key}")
    return "\n".join(lines)
