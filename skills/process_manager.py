"""List or kill running processes"""
SKILL_NAME = "process_manager"
SKILL_TRIGGERS = ["kill process", "stop process", "running processes", "what's running", "top processes", "kill app"]
SKILL_DESCRIPTION = "List top processes or kill a specific one"
SKILL_MCP_EXPOSE = False  # Too dangerous for remote MCP access

import subprocess

def run(task, app="", ctx=""):
    t = task.lower()
    if any(w in t for w in ["kill", "stop", "quit", "force quit"]):
        name = t
        for w in ["kill", "stop", "quit", "force quit", "process", "app", "the", "please"]:
            name = name.replace(w, "").strip()
        if not name:
            return "Which process should I kill?"
        r = subprocess.run(["pkill", "-f", name], capture_output=True, text=True)
        return f"Sent kill signal to '{name}'"
    else:
        r = subprocess.run(["ps", "aux", "--sort=-%cpu"], capture_output=True, text=True, timeout=5)
        lines = r.stdout.strip().split("\n")[1:6]
        result = "Top 5 processes by CPU:\n"
        for line in lines:
            parts = line.split()
            cpu, mem, cmd = parts[2], parts[3], " ".join(parts[10:])[:40]
            result += f"• {cmd} — CPU: {cpu}% MEM: {mem}%\n"
        return result
