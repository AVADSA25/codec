"""backup_status skill — check CODEC memory backup status and trigger manual backup."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SKILL_NAME = "backup_status"
SKILL_DESCRIPTION = "Check CODEC memory database backup status: list recent backups, sizes, and ages. Pass 'run' to trigger a manual backup now."
SKILL_TRIGGERS = [
    "backup status", "check backups", "list backups", "my backups",
    "database backup", "memory backup", "when was last backup",
    "run backup", "manual backup", "backup now"
]
SKILL_MCP_EXPOSE = True


def run(task: str = "", context: str = "") -> str:
    """Check backup status or trigger manual backup."""
    from datetime import datetime
    import shutil

    backup_dir = os.path.expanduser("~/.codec/backups")
    db_path = os.path.expanduser("~/.codec/memory.db")

    task_lower = task.lower().strip()

    if "run" in task_lower or "now" in task_lower or "manual" in task_lower or "trigger" in task_lower:
        # Trigger a manual backup
        try:
            os.makedirs(backup_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y-%m-%d")
            dest = os.path.join(backup_dir, f"memory-{ts}.db")
            if os.path.exists(db_path):
                shutil.copy2(db_path, dest)
                size_mb = os.path.getsize(dest) / (1024 * 1024)
                return f"Backup created: {dest} ({size_mb:.1f} MB)"
            else:
                return f"Error: memory database not found at {db_path}"
        except Exception as e:
            return f"Backup failed: {e}"

    # Default: show backup status
    if not os.path.exists(backup_dir):
        return "No backup directory found at ~/.codec/backups/. No backups have been created."

    backups = sorted(os.listdir(backup_dir), reverse=True)
    backups = [b for b in backups if b.endswith(".db")]

    if not backups:
        return "Backup directory exists but contains no .db files."

    lines = [f"CODEC Memory Backups ({len(backups)} found):\n"]
    for b in backups[:10]:
        path = os.path.join(backup_dir, b)
        size_mb = os.path.getsize(path) / (1024 * 1024)
        mtime = datetime.fromtimestamp(os.path.getmtime(path))
        age = datetime.now() - mtime
        age_str = f"{age.days}d ago" if age.days > 0 else f"{age.seconds // 3600}h ago"
        lines.append(f"  {b} — {size_mb:.1f} MB — {age_str}")

    # DB size
    if os.path.exists(db_path):
        db_size = os.path.getsize(db_path) / (1024 * 1024)
        lines.append(f"\nCurrent DB: {db_size:.1f} MB")

    return "\n".join(lines)
