"""LLM-facing shim for the AskUserQuestion tool.

Per docs/PHASE1-STEP3-DESIGN.md §1.9. Discovery happens via the
codec_skill_registry AST parse — this file just exposes the SKILL_*
metadata + a thin run() that routes to codec_ask_user.ask().

The LLM emits one of:
    TOOL: ask_user
    INPUT: How big should I make the modal?
or:
    TOOL: ask_user
    INPUT: {"question": "Approve refund of $400?",
            "options": ["Approve", "Reject", "Modify"],
            "destructive": true,
            "destructive_verb": "approve",
            "timeout": 600}

parse_skill_input() in codec_ask_user normalises both shapes.
"""
SKILL_NAME = "ask_user"
SKILL_DESCRIPTION = (
    "Pause and ask the user a clarifying question. Use when ambiguous about "
    "user intent, file path, or destructive action. Pass a string for a "
    "free-text question, or a JSON object {\"question\":\"...\",\"options\":[...]} "
    "for structured choices. Add \"destructive\":true and \"destructive_verb\":\"<verb>\" "
    "for irreversible actions (require literal verb-match for consent)."
)
SKILL_TRIGGERS = ["ask user", "clarify with user", "confirm with"]
SKILL_MCP_EXPOSE = True


def run(task: str, context: str = "") -> str:
    """Route the LLM-emitted request to ``codec_ask_user.ask()``.

    Returns the user's answer string, or ``"(no answer — timed out)"`` on
    deadline / strict-consent two-strike timeout, or ``"(skill disabled)"``
    if ``ASKUSER_ENABLED=false``.
    """
    from codec_ask_user import ask, parse_skill_input
    parsed = parse_skill_input(task)
    return ask(
        question=parsed.get("question") or "",
        options=parsed.get("options"),
        timeout=parsed.get("timeout"),
        destructive=parsed.get("destructive", False),
        destructive_verb=parsed.get("destructive_verb"),
        # Caller agent / crew context isn't directly visible here — the
        # core ask() reads correlation_id from the wrapping operation's
        # contextvar (codec_agents._correlation_id_var or codec_voice's),
        # so attribution still works in audit emits.
    )
