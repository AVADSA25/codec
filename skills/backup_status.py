"""backup_status skill — CODEC backup status, and a manual memory backup on request.

Status shows the last backups and their check result, the state tar, the retention settings and the
recent deletion counts (docs/BACKUP-RETENTION-DESIGN.md). 'backup now' runs codec_backup.backup_memory,
the one memory.db writer: a checked copy that never replaces a good one with a bad one. File names,
sizes and counts only: no content. Restoring is not here (this skill is MCP-exposed): it is
tools/codec_restore.py on the Mac, behind the owner's 'restore' answer.
"""
import sys
import os
import re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SKILL_NAME = "backup_status"
SKILL_DESCRIPTION = ("Check CODEC backup status: last backups and their check result, the settings backup, "
                     "how long chats, clipboard and photos are kept, and recent deletions. "
                     "Pass 'run' to make a checked memory backup now.")
SKILL_TRIGGERS = [
    "backup status", "check backups", "list backups", "my backups",
    "database backup", "memory backup", "when was last backup",
    "run backup", "manual backup", "backup now"
]
SKILL_MCP_EXPOSE = True

_RUN = re.compile(r"\b(run|now|manual|trigger)\b")


def run(task: str = "", context: str = "") -> str:
    """Show backup status, or make a checked memory backup now."""
    import codec_backup
    if _RUN.search((task or "").lower()):
        try:
            return codec_backup.describe_backup(codec_backup.backup_memory(force=True))
        except Exception as e:
            return f"Backup failed: {type(e).__name__}. The older copies are kept."
    try:
        return codec_backup.status_report()
    except Exception as e:
        return f"Could not read backup status: {type(e).__name__}"
