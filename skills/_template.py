"""
CODEC Skill Template
====================
Create a new .py file in ~/.codec/skills/ with this structure.
CODEC will auto-load it on startup.

Required:
  SKILL_NAME     - short identifier
  SKILL_TRIGGERS - list of phrases that activate this skill
  run(task, app, ctx) - function that returns a string response

The run() function receives:
  task  = what the user said/typed
  app   = which app was focused
  ctx   = screen context (if available)
"""
SKILL_NAME = "my_skill"
SKILL_DESCRIPTION = "Describe what this skill does"
SKILL_TRIGGERS = ["trigger phrase one", "trigger phrase two"]

def run(task, app="", ctx=""):
    """Process the task and return a response string"""
    return "Skill response here"
