"""Run a quick terminal command and return output"""
SKILL_NAME = "terminal"
SKILL_TRIGGERS = ["run command", "terminal", "execute", "shell", "bash command"]
SKILL_DESCRIPTION = "Run a terminal command and return the output"
SKILL_MCP_EXPOSE = False  # Too dangerous for remote MCP access

import subprocess, os, sys

# Use centralized dangerous pattern check from codec_config (single source of truth)
try:
    _repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _repo_dir not in sys.path:
        sys.path.insert(0, _repo_dir)
    from codec_config import is_dangerous
except ImportError:
    # Fallback: conservative blocklist if codec_config not available
    def is_dangerous(cmd):
        BLOCKED = ["rm -rf", "sudo", "shutdown", "reboot", "killall", "mkfs", "dd if=",
                    "chmod 777", "| bash", "| sh", "curl | bash", "wget | sh"]
        return any(b in cmd.lower() for b in BLOCKED)

def run(task, app="", ctx=""):
    t = task.lower()
    for w in ["run command", "run", "terminal", "execute", "shell", "bash command", "bash"]:
        t = t.replace(w, "").strip()
    if not t:
        return "What command should I run?"
    if is_dangerous(t):
        return f"Blocked for safety: {t[:50]}"
    try:
        r = subprocess.run(["bash", "-c", t], capture_output=True, text=True, timeout=15)
        out = r.stdout.strip() or r.stderr.strip() or "Done (no output)"
        return out[:500]
    except subprocess.TimeoutExpired:
        return "Command timed out (15s limit)"
    except Exception as e:
        return f"Error: {e}"
