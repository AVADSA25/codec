"""Google Keep skill for CODEC — note: Keep has no official API, delegate to Lucy"""
import json, os

SKILL_TRIGGERS = ["google keep", "my notes keep", "keep notes", "show keep"]
SKILL_DESCRIPTION = "Google Keep notes (delegates to Lucy for full access)"

def run(task, context=None):
    return ("\u26a0\ufe0f Google Keep does not have an official REST API. "
            "Try: \\"Ask Lucy to check my notes\\" \u2014 Lucy can access Keep via her tools. "
            "Or use the **notes** skill for Apple Notes: just say \\"my notes\\" or \\"save a note\\".")
