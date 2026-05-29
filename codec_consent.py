"""codec_consent — strict-consent gate for the chat + MCP skill paths.

Re-audit (red-team CHAIN-001/002/006): the Step-3 consent gate
(docs/PHASE1-STEP3-DESIGN.md §1.7) was wired ONLY into codec_agent_runner. The
chat ([SKILL:] tag + pre-LLM hijack → codec_dispatch.run_skill) and MCP
(codec_mcp.tool_fn) paths could reach high-power skills with only the
`is_dangerous` heuristic / path blocklists. This module is the shared
classifier + per-transport policy:

  - MCP  → hard-refuse destructive skills (claude.ai can't consent at the
           operator tier; consistent with the _HTTP_BLOCKED principle).
  - chat → require explicit confirmation (the handler returns consent_required;
           the user confirms; re-dispatch carries a token).
  - voice/agent → existing ask_user announce-and-listen (unchanged).

A skill is "destructive" if it declares `SKILL_DESTRUCTIVE = True`
(registry-AST-extracted — the extensible per-skill path, Decision C), OR is in
`codec_config._HTTP_BLOCKED`, OR is one of the known high-power built-ins below
(so coverage doesn't depend on regenerating the hash-pinned skill manifest).

Kill switch: `CONSENT_GATE_ENABLED=false`.
"""
import os

__all__ = ["gate_enabled", "is_destructive_skill", "chat_consent_ok", "mcp_refuse_message"]

# Known high-power built-ins that are destructive but NOT in _HTTP_BLOCKED.
# (terminal / python_exec / process_manager / pm2_control / ax_control are
# already covered by the _HTTP_BLOCKED backstop.)
_DESTRUCTIVE_BUILTINS = frozenset({
    "file_ops",       # write/append/delete to the filesystem
    "file_write",     # writes files
    "imessage_send",  # sends messages as the user
    "pilot",          # drives a real browser session
    "skill_forge",    # writes a skill to disk (no review gate)
})


def gate_enabled() -> bool:
    """Consent gate on by default; CONSENT_GATE_ENABLED=false disables it."""
    return os.environ.get("CONSENT_GATE_ENABLED", "true").lower() != "false"


def is_destructive_skill(tool_name, registry=None) -> bool:
    """True if `tool_name` is a high-power/destructive skill needing consent
    (chat) or refusal (MCP). Never raises."""
    if not tool_name:
        return False
    # 1) per-skill SKILL_DESTRUCTIVE flag (extensible — user skills opt in)
    try:
        reg = registry
        if reg is None:
            from codec_dispatch import registry as reg  # the singleton
        if reg is not None and reg.get_destructive(tool_name):
            return True
    except Exception:
        pass
    # 2) _HTTP_BLOCKED backstop (terminal, python_exec, process_manager, …)
    try:
        from codec_config import _HTTP_BLOCKED
        if tool_name in _HTTP_BLOCKED:
            return True
    except Exception:
        pass
    # 3) known high-power built-ins
    return tool_name in _DESTRUCTIVE_BUILTINS


def chat_consent_ok(tool_name, query, *, registry=None) -> bool:
    """Chat path (A2): a destructive skill requires explicit consent via the
    existing AskUserQuestion PWA panel (Phase 1 Step 3 §1.7 — literal verb-match;
    generic yes/ok rejected). Returns True if the skill may run (non-destructive,
    gate disabled, or consent granted); False if blocked (declined / timeout /
    ask_user unavailable). BLOCKS the worker thread on ask_user until answered —
    the chat handler invokes this via asyncio.to_thread, so the event loop isn't
    blocked. Fail-closed: any error → False (a destructive skill never
    auto-runs)."""
    if not gate_enabled() or not is_destructive_skill(tool_name, registry=registry):
        return True
    try:
        import codec_ask_user
        answer = codec_ask_user.ask(
            f"CODEC wants to run the '{tool_name}' skill — a destructive / "
            f"high-power operation — for: {(query or '')[:200]}",
            destructive=True,
            asked_from="chat",
            tool_name=tool_name,
        )
        return answer not in (
            codec_ask_user.TIMEOUT_SENTINEL,
            codec_ask_user.DISABLED_SENTINEL,
        )
    except Exception:
        return False


def mcp_refuse_message(tool_name) -> str:
    return (
        f"Skill '{tool_name}' is a destructive/high-power operation and is not "
        "permitted over MCP. Run it locally (chat or voice), where the operator "
        "can confirm it."
    )
