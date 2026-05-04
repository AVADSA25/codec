"""CODEC Skill: Time & Date"""
SKILL_NAME = "time"
SKILL_DESCRIPTION = "Get current time and date"
SKILL_MCP_EXPOSE = True
SKILL_TRIGGERS = ["what time", "current time", "what date", "today's date", "what day"]

def run(task, app="", ctx=""):
    """Get current time/date"""
    from datetime import datetime
    now = datetime.now()
    return now.strftime("It's %A, %B %d, %Y at %I:%M %p")
