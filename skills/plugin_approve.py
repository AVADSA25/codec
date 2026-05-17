"""CODEC Skill: Approve a plugin file by adding its SHA-256 to the allowlist (PR-2F, D-18).

Operator-only (SKILL_MCP_EXPOSE=False). claude.ai over MCP cannot approve
plugins — only the local operator can extend the plugin trust boundary.

Usage:
    "approve plugin <filename>"
    "approve plugin self_improve.py"
    "trust plugin newhook.py"

The skill computes the SHA-256 of the file currently at
`~/.codec/plugins/<filename>`, writes/updates the entry in
`~/.codec/plugins.allowlist`, and reports the hash's last 8 chars
for operator verification. On the next plugin fire after approval,
the plugin loads normally.
"""
SKILL_NAME = "plugin_approve"
SKILL_DESCRIPTION = (
    "Approve a plugin file by adding its SHA-256 to the allowlist. "
    "Operator-only — required after dropping a new plugin into ~/.codec/plugins/."
)
SKILL_TRIGGERS = [
    "approve plugin", "plugin approve", "trust plugin",
]
SKILL_MCP_EXPOSE = False  # Operator-only; never expose plugin trust over MCP


def _extract_filename(task: str) -> str:
    """Pull a plugin filename out of the task text. Accepts:
        "approve plugin self_improve.py"
        "trust plugin newhook.py"
        "plugin approve foo.py"
    """
    import re
    t = task.strip()
    # Strip trigger phrases
    for trigger in ("approve plugin", "trust plugin", "plugin approve"):
        if t.lower().startswith(trigger):
            t = t[len(trigger):].strip()
            break
    # First .py token
    m = re.search(r"([\w.-]+\.py)\b", t)
    if m:
        return m.group(1)
    return t


def run(task: str = "", app: str = "", ctx: str = "") -> str:
    """Approve a plugin filename. Returns operator-readable confirmation."""
    filename = _extract_filename(task)
    if not filename or not filename.endswith(".py"):
        return (
            "Usage: 'approve plugin <filename>.py' — e.g. "
            "'approve plugin self_improve.py'."
        )

    try:
        from codec_hooks import approve_plugin
    except Exception as e:
        return f"Plugin approve unavailable: codec_hooks import failed: {e}"

    result = approve_plugin(filename, approved_by="operator")
    if not result.get("ok"):
        return f"❌ Refused to approve {filename}: {result.get('reason', 'unknown')}"
    return (
        f"✅ Plugin {filename} approved. SHA-256 …{result['last8']}. "
        f"Plugin will load on next hook fire."
    )
