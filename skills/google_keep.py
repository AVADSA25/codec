"""Google Keep skill for CODEC — note: Keep has no official API, delegate to CODEC workflow"""

SKILL_NAME = "google_keep"
SKILL_TRIGGERS = ["google keep", "my notes keep", "keep notes", "show keep"]
SKILL_DESCRIPTION = "Google Keep notes (delegates to CODEC workflow for full access)"

def run(task, app="", ctx=""):
    return (
        "\u26a0\ufe0f Google Keep does not have an official REST API. "
        "Try: \"Ask CODEC to check my notes\" \u2014 CODEC can access Keep via workflow tools. "
        "Or use the **notes** skill for Apple Notes: just say \"my notes\" or \"save a note\"."
    )
