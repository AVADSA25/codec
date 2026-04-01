#!/bin/bash
# CODEC Skill Deploy — sync repo skills to runtime directory
# Run after git pull or any skill changes:
#   ./deploy_skills.sh
#
# What it does:
#   ~/codec-repo/skills/ → ~/.codec/skills/  (one-way sync)

set -e

REPO_SKILLS="$(dirname "$0")/skills"
RUNTIME_SKILLS="$HOME/.codec/skills"

if [ ! -d "$REPO_SKILLS" ]; then
    echo "Error: $REPO_SKILLS not found"
    exit 1
fi

mkdir -p "$RUNTIME_SKILLS"

echo "Deploying skills: $REPO_SKILLS → $RUNTIME_SKILLS"
rsync -av "$REPO_SKILLS/" "$RUNTIME_SKILLS/"

COUNT=$(ls "$RUNTIME_SKILLS"/*.py 2>/dev/null | wc -l | tr -d ' ')
echo ""
echo "Done. $COUNT skills deployed to $RUNTIME_SKILLS"
echo "Restart CODEC to load changes: pm2 restart codec"
