#!/usr/bin/env bash
# Nightly memory.db backup (launchd com.codec.memory-backup, 03:15).
# It runs the same writer as the heartbeat, codec_backup.backup_memory
# (docs/BACKUP-RETENTION-DESIGN.md): an online copy checked with PRAGMA
# quick_check, 0600, the newest 30 days kept. A copy that fails the check is
# thrown away, the older copies stay and the owner gets one alert. When the
# heartbeat already made today's checked copy, this does nothing.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY=/usr/local/bin/python3.13
[ -x "$PY" ] || PY="$(command -v python3)"

cd "$REPO"
exec "$PY" -m codec_backup memory
