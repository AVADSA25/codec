"""Run a quick terminal command and return output"""
SKILL_NAME = "terminal"
SKILL_TRIGGERS = ["run command", "terminal", "execute", "shell", "bash command"]
SKILL_DESCRIPTION = "Run a terminal command and return the output"
SKILL_MCP_EXPOSE = False  # Too dangerous for remote MCP access

import subprocess

def run(task, app="", ctx=""):
    t = task.lower()
    for w in ["run command", "run", "terminal", "execute", "shell", "bash command", "bash"]:
        t = t.replace(w, "").strip()
    if not t:
        return "What command should I run?"
    BLOCKED = ["rm -rf", "sudo", "shutdown", "reboot", "killall", "mkfs", "dd if="]
    if any(b in t.lower() for b in BLOCKED):
        return f"Blocked for safety: {t[:50]}"
    try:
        r = subprocess.run(["bash", "-c", t], capture_output=True, text=True, timeout=15)
        out = r.stdout.strip() or r.stderr.strip() or "Done (no output)"
        return out[:500]
    except subprocess.TimeoutExpired:
        return "Command timed out (15s limit)"
    except Exception as e:
        return f"Error: {e}"
