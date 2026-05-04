"""Pretty-print and validate JSON"""
SKILL_NAME = "json_formatter"
SKILL_TRIGGERS = ["format json", "pretty print json", "validate json", "prettify json"]
SKILL_DESCRIPTION = "Pretty-print and validate JSON text from clipboard"
SKILL_MCP_EXPOSE = True

import json, subprocess

def run(task, app="", ctx=""):
    try:
        clip = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout.strip()
        if not clip:
            return "Clipboard is empty. Copy some JSON first."
        parsed = json.loads(clip)
        pretty = json.dumps(parsed, indent=2)
        subprocess.run(["pbcopy"], input=pretty.encode(), check=True)
        return f"JSON is valid. Formatted and copied to clipboard ({len(pretty)} chars)."
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"
