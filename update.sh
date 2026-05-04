#!/bin/bash
# CODEC Update — pull latest code, deploy skills, sync to PM2, restart
# Run after any upstream changes:
#   ./update.sh

set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"

echo "═══════════════════════════════════"
echo "  CODEC Update"
echo "═══════════════════════════════════"

# 1. Pull latest
echo ""
echo "→ Pulling latest code..."
git pull --ff-only || { echo "Pull failed — resolve conflicts first."; exit 1; }

# 2. Deploy skills
echo ""
echo "→ Deploying skills..."
./deploy_skills.sh

# 3. Sync to PM2 (handles worktree blindspot)
echo ""
echo "→ Syncing to PM2..."
./sync_to_pm2.sh

# 4. Run smoke test
echo ""
echo "→ Running smoke test..."
python3.13 codec_smoke_test.py || echo "⚠  Some smoke checks failed — review above."

echo ""
echo "═══════════════════════════════════"
echo "  Update complete"
echo "═══════════════════════════════════"
