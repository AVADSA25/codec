"""CODEC Skill: System Info"""
SKILL_NAME = "system"
SKILL_DESCRIPTION = "Quick system information (CPU, memory, disk, uptime)"
SKILL_MCP_EXPOSE = True
SKILL_TRIGGERS = ["system info", "cpu usage", "memory usage", "disk space", "uptime", "how much ram", "how much storage"]

def run(task, app="", ctx=""):
    """Get system stats"""
    import subprocess
    parts = []
    try:
        # Uptime
        r = subprocess.run(["uptime"], capture_output=True, text=True, timeout=5)
        up = r.stdout.strip()
        parts.append("Uptime: " + up.split("up")[1].split(",")[0].strip() if "up" in up else up)
    except: pass
    try:
        # Memory
        r = subprocess.run(["bash", "-c", "vm_stat | head -5"], capture_output=True, text=True, timeout=5)
        parts.append("Memory stats available")
    except: pass
    try:
        # Disk
        r = subprocess.run(["df", "-h", "/"], capture_output=True, text=True, timeout=5)
        lines = r.stdout.strip().split("\n")
        if len(lines) > 1:
            cols = lines[1].split()
            parts.append(f"Disk: {cols[2]} used of {cols[1]} ({cols[4]} full)")
    except: pass
    try:
        # CPU load
        r = subprocess.run(["bash", "-c", "sysctl -n vm.loadavg"], capture_output=True, text=True, timeout=5)
        parts.append("Load: " + r.stdout.strip())
    except: pass

    return " | ".join(parts) if parts else "Couldn't get system info."
