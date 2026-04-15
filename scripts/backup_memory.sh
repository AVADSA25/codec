#!/usr/bin/env bash
# Nightly backup of CODEC memory DB.
# Uses sqlite3's online .backup command (safe even while DB is in use).
# Keeps last 30 days; prunes older.
set -euo pipefail

DB="${HOME}/.codec/memory.db"
DIR="${HOME}/.codec/backups"
mkdir -p "$DIR"

if [ ! -f "$DB" ]; then
  echo "[backup] no DB at $DB — skipping" >&2
  exit 0
fi

STAMP=$(date -u +%Y-%m-%d)
DEST="${DIR}/memory-${STAMP}.db"

sqlite3 "$DB" ".backup '${DEST}'"
chmod 600 "$DEST"

# Retain 30 days
find "$DIR" -name 'memory-*.db' -type f -mtime +30 -delete 2>/dev/null || true

echo "[backup] wrote ${DEST} ($(du -h "$DEST" | cut -f1))"
