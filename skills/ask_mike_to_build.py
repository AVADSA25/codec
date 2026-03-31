"""CODEC Skill: Build Something"""
SKILL_NAME = "ask_codec_to_build"
SKILL_DESCRIPTION = "Instructs CODEC to construct or create a specified item or project."
SKILL_TRIGGERS = ["ask codec to build", "codec build something", "have codec create", "codec construct"]

import os, json

def run(task, app="", ctx=""):
    try:
        if not task:
            return "Error: No specific item or project was mentioned to build."

        # Simulate the core logic of instructing CODEC to build the requested item
        # In a real scenario, this would interface with CODEC's build system or API
        project_name = task.strip()

        if not project_name:
            return "Error: Please specify what CODEC should build."

        return f"CODEC has been instructed to build: {project_name}. Process initiated."

    except Exception as e:
        return f"Error during build instruction: {str(e)}"