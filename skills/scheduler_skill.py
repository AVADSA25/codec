"""CODEC Scheduler Skill — voice control for scheduled agent runs"""
import sys, os
sys.path.insert(0, os.path.expanduser("~/codec-repo"))

SKILL_NAME = "scheduler"
SKILL_TRIGGERS = [
    "schedule agent", "schedule crew", "run every morning",
    "run every monday", "schedule daily", "run daily briefing automatically",
    "set up schedule", "every morning at", "schedule competitor analysis",
    "list schedules", "show schedules", "remove schedule",
]
SKILL_DESCRIPTION = "Schedule CODEC agent crews to run automatically (daily briefing at 8am, competitor analysis every Monday, etc.)"

def run(task: str, context: str = "") -> str:
    from codec_scheduler import run as scheduler_run
    return scheduler_run(task, context)
