"""CODEC Skill: System Info"""
import os
import re

SKILL_NAME = "system"
SKILL_DESCRIPTION = "Quick system information (CPU, memory, disk, uptime)"
SKILL_MCP_EXPOSE = True
SKILL_TRIGGERS = ["system info", "cpu usage", "memory usage", "disk space", "uptime", "how much ram", "how much storage"]

# P-097: on macOS "/" is the sealed system snapshot (always about 2% used); the
# user's files live on the Data volume. Elsewhere (Linux CI) "/" is the disk.
_DATA_VOLUME = "/System/Volumes/Data"
_DF_ROW = re.compile(r"^(.*?)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+%)\s+(.+)$")


def _size(n, unit):
    """Bytes as GB (or TB from 1000 GB); unit is 1e9 for disks (as Finder shows)
    and 2**30 for memory (as Activity Monitor shows)."""
    gb = n / unit
    return f"{gb / 1000:.1f} TB" if gb >= 1000 else f"{gb:.1f} GB"


def _disk_line(df_out, data_volume):
    """`df -P -k` output -> 'Disk ...: X used of Y (Z full)', or None."""
    rows = [_DF_ROW.match(ln) for ln in (df_out or "").strip().splitlines()[1:]]
    rows = [m for m in rows if m]
    if not rows:
        return None
    m = rows[-1]
    total, used = int(m.group(2)) * 1024, int(m.group(3)) * 1024
    label = "Disk (Data volume)" if data_volume else "Disk"
    return f"{label}: {_size(used, 1e9)} used of {_size(total, 1e9)} ({m.group(5)} full)"


def _memory_line(vm_stat_out, memsize_out):
    """`vm_stat` + `sysctl -n hw.memsize` -> 'Memory: X used of Y', or None.
    Used = app memory (anonymous minus purgeable pages; active pages when an older
    macOS has no anonymous count) + wired + compressed, as Activity Monitor adds it."""
    try:
        total = int((memsize_out or "").strip())
    except ValueError:
        return None
    page = re.search(r"page size of (\d+) bytes", vm_stat_out or "")
    if total <= 0 or not page:
        return None
    pages = {k.strip().strip('"').lower(): int(v)
             for k, v in re.findall(r"^([^:\n]+):\s+(\d+)\.?\s*$", vm_stat_out, re.M)}
    if "anonymous pages" in pages:
        app = pages["anonymous pages"] - pages.get("pages purgeable", 0)
    else:
        app = pages.get("pages active")
    wired = pages.get("pages wired down")
    compressed = pages.get("pages occupied by compressor")
    if app is None or wired is None or compressed is None:
        return None
    used = min((max(app, 0) + wired + compressed) * int(page.group(1)), total)
    return f"Memory: {_size(used, 2**30)} used of {_size(total, 2**30)}"


def run(task, app="", ctx=""):
    """Get system stats"""
    import subprocess
    parts = []
    try:
        # Uptime
        r = subprocess.run(["uptime"], capture_output=True, text=True, timeout=5)
        up = r.stdout.strip()
        parts.append("Uptime: " + up.split("up")[1].split(",")[0].strip() if "up" in up else up)
    except Exception: pass
    try:
        # Memory: a real line or none (P-097)
        vm = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5)
        size = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5)
        line = _memory_line(vm.stdout, size.stdout)
        if line:
            parts.append(line)
    except Exception: pass
    try:
        # Disk: the Data volume where it exists (P-097)
        data_volume = os.path.isdir(_DATA_VOLUME)
        r = subprocess.run(["df", "-P", "-k", _DATA_VOLUME if data_volume else "/"],
                           capture_output=True, text=True, timeout=5)
        line = _disk_line(r.stdout, data_volume)
        if line:
            parts.append(line)
    except Exception: pass
    try:
        # CPU load
        r = subprocess.run(["bash", "-c", "sysctl -n vm.loadavg"], capture_output=True, text=True, timeout=5)
        parts.append("Load: " + r.stdout.strip())
    except Exception: pass

    return " | ".join(parts) if parts else "Couldn't get system info."
