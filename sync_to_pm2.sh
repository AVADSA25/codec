#!/bin/bash
# sync_to_pm2.sh — Ensure PM2 is running from ~/codec-repo, restart affected process.
# Run after every code change, before asking Mickael to test.

set -e

REPO_DIR="$HOME/codec-repo"
PM2="/opt/homebrew/bin/pm2"
PROCESS_NAME="${1:-open-codec}"

# 1. Get current PM2 exec_cwd
EXEC_CWD=$($PM2 show "$PROCESS_NAME" --no-color 2>/dev/null | grep -i "exec cwd" | awk -F'│' '{print $(NF-1)}' | xargs)

if [ -z "$EXEC_CWD" ]; then
    echo "❌ Process '$PROCESS_NAME' not found in PM2"
    exit 1
fi

echo "📍 PM2 exec_cwd: $EXEC_CWD"
echo "📍 Repo dir:     $REPO_DIR"

# 2. If PM2 is running from a worktree, rsync repo files there
if [ "$EXEC_CWD" != "$REPO_DIR" ]; then
    echo "⚠️  PM2 is running from a different directory — syncing..."
    rsync -av --exclude='.git' --exclude='__pycache__' --exclude='.claude' \
        "$REPO_DIR/" "$EXEC_CWD/"
    echo "✅ Files synced to $EXEC_CWD"
else
    echo "✅ PM2 already running from repo dir"
fi

# 3. Restart the process
echo "🔄 Restarting $PROCESS_NAME..."
$PM2 restart "$PROCESS_NAME" --update-env
echo "✅ $PROCESS_NAME restarted"

# 4. Brief pause then show status
sleep 1
$PM2 show "$PROCESS_NAME" --no-color | head -25
echo ""
echo "Done. Run: python3 $REPO_DIR/codec_smoke_test.py"
