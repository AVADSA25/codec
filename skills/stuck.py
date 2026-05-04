"""LLM-self-recognized stuck — companion shim for codec_agents auto-detect.

Per docs/PHASE1-STEP3-DESIGN.md §2.4. Auto-detection lives in
codec_agents.Agent._handle_stuck_post_tool (the safety net). This skill
is the LLM-callable path: when the model self-recognises a loop, it
emits TOOL: stuck and we route to ask_user with a context summary.

The LLM-side stuck path doesn't have access to the agent's own
_recent_calls ring buffer — it sees only the conversation history.
That's fine: ask_user is the user-facing escalation, and the user can
read the recent context themselves before answering.

SKILL_MCP_EXPOSE = True so claude.ai over MCP can also self-invoke.
The MCP smoke test (tests/test_mcp_all_tools.py) skips this skill —
it would block on threading.Event waiting for an answer the smoke
test will never provide.
"""
SKILL_NAME = "stuck"
SKILL_DESCRIPTION = (
    "Diagnose when stuck in a loop. Pause and ask the user how to proceed. "
    "Use when you've tried the same approach multiple times without progress, "
    "or when you can't tell which of several paths the user intended."
)
SKILL_TRIGGERS = ["i'm stuck", "i think i'm looping", "this isn't working",
                  "stuck on this", "going in circles"]
SKILL_MCP_EXPOSE = True


def run(task: str, context: str = "") -> str:
    """task = optional reason / context summary. Routes to ask_user with
    Continue / Abandon / New approach options. Returns the user's
    directive or "(no answer — timed out)" on deadline."""
    from codec_ask_user import ask
    reason = task.strip() if task else "I think I'm stuck"
    question = (
        f"I'm stuck — {reason}. Want me to: (a) try a different approach, "
        f"(b) abandon the task, or (c) continue anyway?"
    )
    return ask(
        question=question,
        options=["Try different approach", "Abandon", "Continue anyway"],
        asked_from="crew",
    )
