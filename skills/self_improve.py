"""self_improve skill — wraps codec_self_improve for MCP/autopilot."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from codec_self_improve import run_once

SKILL_NAME = "self_improve"
SKILL_DESCRIPTION = "Analyze yesterday's CODEC audit log and draft proposals for new skills to close capability gaps; proposals are staged for review, never auto-deployed."
SKILL_MCP_EXPOSE = True


def run(task: str = "", context: str = "") -> str:
    # Allow "self_improve 2026-04-14" to target a specific day
    date = None
    for tok in (task or "").split():
        if len(tok) == 10 and tok[4] == "-" and tok[7] == "-":
            date = tok
            break
    return run_once(date)
