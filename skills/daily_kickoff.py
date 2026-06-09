"""CODEC Daybreak — morning kickoff briefing (docs/DAYBREAK-DESIGN.md)."""
SKILL_NAME = "daily_kickoff"
SKILL_TRIGGERS = [
    "good morning",
    "where did we leave off",
    "where did we left off",
    "where we left off",
    "start my day",
    "daybreak",
    "daily kickoff",
    "kick off my day",
]
SKILL_DESCRIPTION = ("Morning kickoff: where we left off yesterday, open working "
                     "threads, today's calendar and weather, follow-ups, and "
                     "suggested priorities.")
SKILL_MCP_EXPOSE = True

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_REPO, os.path.expanduser("~/codec-repo")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)


def run(task, app="", ctx=""):
    try:
        from codec_daybreak import assemble_briefing
        return assemble_briefing(task)
    except Exception as e:
        return f"Daybreak hit a snag: {e}"
