"""CODEC Skill: Build Something"""
SKILL_NAME = "ask_mike_to_build"
SKILL_DESCRIPTION = "Instructs Mike to construct or create a specified item or project."
SKILL_TRIGGERS = ["ask mike to build", "mike build something", "have mike create", "mike construct"]

import os, json

def run(task, app="", ctx=""):
    try:
        if not task:
            return "Error: No specific item or project was mentioned to build."
        
        # Simulate the core logic of instructing Mike to build the requested item
        # In a real scenario, this would interface with Mike's build system or API
        project_name = task.strip()
        
        if not project_name:
            return "Error: Please specify what Mike should build."
            
        return f"Mike has been instructed to build: {project_name}. Process initiated."
        
    except Exception as e:
        return f"Error during build instruction: {str(e)}"