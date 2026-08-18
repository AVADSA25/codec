"""Switch the local LLM CODEC answers with, by voice or chat.

All local models are served by ONE mlx_vlm.server, which holds a single model at
a time — so a switch unloads the current model and loads the new one. That takes
20-60s for a 15-20 GB checkpoint, and it is why this skill reports the load time
rather than pretending the change is instant.

If the requested model fails to load, codec_models.set_active restores and
reloads the previous one before returning, so a bad request can never leave
CODEC unable to answer.
"""
import codec_models

SKILL_NAME = "model_switch"
SKILL_DESCRIPTION = (
    "Switch which local LLM CODEC uses, or report the current one. "
    "Say 'switch to the coding model', 'use the fast model', or 'which model are you using'."
)
SKILL_TRIGGERS = [
    "switch model", "switch to model", "change model", "use model",
    "switch to the coding model", "switch to coding model", "coding model",
    "switch to the fast model", "use the fast model", "everyday model",
    "which model", "what model", "current model", "list models",
    "available models", "model list",
]
SKILL_MCP_EXPOSE = False  # swapping the brain is a local-operator action

# Spoken shorthand -> substring matched against available model ids.
_ALIASES = {
    "coding": ["3.8", "coder"],
    "code": ["3.8", "coder"],
    "fast": ["a3b"],
    "everyday": ["a3b"],
    "daily": ["a3b"],
    "default": ["a3b"],
}


def _fmt(models, active):
    lines = []
    for m in models:
        mark = "->" if m["id"] == active else "  "
        role = f" — {m['role']}" if m.get("role") else ""
        size = f" ({m['size_gb']}GB)" if m.get("size_gb") else ""
        lines.append(f"{mark} {m['label']}{size}{role}")
    return "\n".join(lines)


def _pick(cands, active):
    """Choose among several matches: the active model wins, else the highest
    version (so 'fast' lands on Qwen3.6, not the older 3.5 that sorts first)."""
    if not cands:
        return None
    if active in cands:
        return active
    return sorted(cands)[-1]


def _resolve(want, models, active=""):
    """Map free text to one model id, or None."""
    want = (want or "").lower().strip()
    if not want:
        return None
    ids = [m["id"] for m in models]
    # exact id first
    for mid in ids:
        if want == mid.lower():
            return mid
    # alias keywords
    for key, needles in _ALIASES.items():
        if key in want:
            hits = [mid for mid in ids
                    if any(n in mid.lower() for n in needles)]
            picked = _pick(hits, active)
            if picked:
                return picked
    # loose token match against the model name
    hits = []
    for mid in ids:
        name = mid.split("/")[-1].lower()
        if want in name or name in want:
            hits.append(mid); continue
        core = name.replace("-4bit", "")
        if any(tok and tok in core for tok in want.split()):
            hits.append(mid)
    return _pick(hits, active)


def run(task, app="", ctx=""):
    t = (task or "").lower()
    data = codec_models.list_models()
    models, active = data["models"], data["active"]

    # Pure query — never switch on an ambiguous question.
    if any(p in t for p in ("which model", "what model", "current model",
                            "list model", "available model", "model list")):
        cur = next((m for m in models if m["id"] == active), None)
        head = f"Currently using {cur['label']}" if cur else f"Currently using {active}"
        return f"{head}.\n\nAvailable locally:\n{_fmt(models, active)}"

    # Strip the command words so only the model name is left.
    want = t
    for phrase in ("switch to the", "switch to", "switch", "change to the",
                   "change to", "change", "use the", "use", "model"):
        want = want.replace(phrase, " ")
    target = _resolve(want, models, active)

    if not target:
        return ("I could not tell which model you meant.\n\n"
                f"Available locally:\n{_fmt(models, active)}\n\n"
                "Try: \"switch to the coding model\".")

    if target == active:
        label = next((m["label"] for m in models if m["id"] == target), target)
        return f"Already using {label}."

    result = codec_models.set_active(target)
    label = next((m["label"] for m in models if m["id"] == target), target)
    if result.get("ok"):
        return f"Switched to {label} ({result.get('detail', '')}). It is answering the next message."
    return (f"Could not switch to {label}. {result.get('error', '')}\n"
            f"Still using {result.get('active')}.")
