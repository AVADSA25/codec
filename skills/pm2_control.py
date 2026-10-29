"""CODEC Skill: PM2 Service Management"""
SKILL_NAME = "pm2_control"
SKILL_DESCRIPTION = "Check status, restart, or view logs of PM2 services"
SKILL_TRIGGERS = ["pm2 status", "pm2 restart", "pm2 list", "pm2 logs",
                   "restart service", "service status", "check services",
                   "list services", "show services", "restart pm2",
                   "pm2 info", "running services"]
SKILL_MCP_EXPOSE = False  # Local management only

import json
import os
import subprocess
import re

CONFIG_PATH = os.path.expanduser("~/.codec/config.json")

# Services a chat or voice sentence must never restart. The owner adds his own
# in ~/.codec/config.json:pm2_protected_services; these defaults always apply.
DEFAULT_PROTECTED = ("codec-dashboard", "codec-mcp-http", "cloudflared")

_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]*")


def _protected_services():
    names = set(DEFAULT_PROTECTED)
    try:
        with open(CONFIG_PATH) as f:
            extra = json.load(f).get("pm2_protected_services") or []
        names.update(str(n).lower() for n in extra if isinstance(n, str))
    except Exception:
        pass
    return names


def _restart_refusal(name):
    """Why `pm2 restart <name>` must not run from a sentence, or None."""
    hint = f"Restart it yourself in the terminal: pm2 restart {name}"
    if name == "all":
        return "I don't restart every service at once. Name one service."
    if not _NAME_RE.fullmatch(name):
        return f"'{name}' is not a service name I can restart."
    if name.lower() in _protected_services():
        return (f"'{name}' is a protected service, so I don't restart it "
                f"from chat or voice. {hint}")
    return None


def run(task, app="", ctx=""):
    t = task.lower()

    # ── Restart a specific service ──
    if "restart" in t:
        # Extract service name after "restart"
        match = re.search(r'restart\s+(?:service\s+)?(\S+)', t)
        if match:
            name = match.group(1).strip()
            refusal = _restart_refusal(name)
            if refusal:
                return refusal
            try:
                r = subprocess.run(["pm2", "restart", name],
                                   capture_output=True, text=True, timeout=15)
                out = r.stdout.strip() or r.stderr.strip()
                if r.returncode == 0:
                    return f"Restarted '{name}'.\n{out[:300]}"
                return f"Failed to restart '{name}': {out[:300]}"
            except FileNotFoundError:
                return "pm2 not found. Is it installed globally? (npm i -g pm2)"
            except subprocess.TimeoutExpired:
                return "pm2 restart timed out (15s)."
            except Exception as e:
                return f"Error: {e}"
        else:
            return "Which service should I restart? Say: restart <service-name>"

    # ── Logs for a specific service ──
    if "logs" in t or "log" in t:
        match = re.search(r'logs?\s+(?:for\s+)?(\S+)', t)
        name = match.group(1).strip() if match else "--lines 20"
        try:
            r = subprocess.run(["pm2", "logs", name, "--nostream", "--lines", "15"],
                               capture_output=True, text=True, timeout=10)
            out = r.stdout.strip() or r.stderr.strip() or "No logs."
            return out[:800]
        except FileNotFoundError:
            return "pm2 not found."
        except Exception as e:
            return f"Error: {e}"

    # ── Default: list all services ──
    try:
        r = subprocess.run(["pm2", "jlist"], capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return f"pm2 error: {r.stderr.strip()[:200]}"
        procs = json.loads(r.stdout)
        if not procs:
            return "No PM2 processes running."
        lines = [f"PM2 Services ({len(procs)}):"]
        for p in procs:
            name = p.get("name", "?")
            status = p.get("pm2_env", {}).get("status", "?")
            cpu = p.get("monit", {}).get("cpu", 0)
            mem_mb = round(p.get("monit", {}).get("memory", 0) / 1024 / 1024, 1)
            restarts = p.get("pm2_env", {}).get("restart_time", 0)
            emoji = "G" if status == "online" else "R" if status == "errored" else "Y"
            lines.append(f"  [{emoji}] {name} — {status} | CPU: {cpu}% | MEM: {mem_mb}MB | restarts: {restarts}")
        return "\n".join(lines)
    except FileNotFoundError:
        return "pm2 not found. Is it installed globally? (npm i -g pm2)"
    except Exception as e:
        return f"Error listing services: {e}"
