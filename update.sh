#!/bin/bash
# CODEC Update — pull latest code, copy CODEC's data, check, deploy skills, sync to PM2, restart
# Run after any upstream changes:
#   ./update.sh
#
# docs/SAFE-UPDATE-DESIGN.md: the new code is checked with scripts/smoke.py before anything is deployed or
# restarted. A failed copy of ~/.codec or a failed check goes back to the commit recorded before the pull with
# `git reset --keep` (uncommitted work is kept) and exits non-zero, without the "Update complete" banner.
# Env overrides: CODEC_PY (python, default python3.13 or python3), CODEC_STATE (default ~/.codec).
#
# The body is one function, read whole before it runs, and the last line calls it and exits in one line,
# so `git pull` rewriting this file mid-run cannot change what runs.

main() {
    set -e

    REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
    cd "$REPO_DIR"
    PY="${CODEC_PY:-$(command -v python3.13 || command -v python3 || echo python3)}"
    STATE="${CODEC_STATE:-$HOME/.codec}"

    echo "═══════════════════════════════════"
    echo "  CODEC Update"
    echo "═══════════════════════════════════"

    # 1. Pull latest
    ROLLBACK_COMMIT="$(git rev-parse HEAD)"
    echo ""
    echo "  Rollback point: $ROLLBACK_COMMIT"
    echo "→ Pulling latest code..."
    git pull --ff-only || { echo "Pull failed — resolve conflicts first."; exit 1; }

    # 2. Copy CODEC's data before the new code touches it
    if [ "$(git rev-parse HEAD)" != "$ROLLBACK_COMMIT" ]; then
        echo ""
        echo "→ Saving a copy of CODEC's data..."
        if ! "$PY" codec_preupdate.py --src "$STATE" --label "git $ROLLBACK_COMMIT"; then
            echo "⚠  Could not save a copy of CODEC's data. Rolling back..."
            rollback
        fi
    fi

    # 3. Smoke test, before anything is deployed or restarted
    echo ""
    echo "→ Running smoke test..."
    if ! "$PY" scripts/smoke.py; then
        echo "⚠  Some smoke checks failed. Rolling back..."
        rollback
    fi

    # 4. Deploy skills
    echo ""
    echo "→ Deploying skills..."
    ./deploy_skills.sh

    # 5. Sync to PM2 (handles worktree blindspot)
    echo ""
    echo "→ Syncing to PM2..."
    ./sync_to_pm2.sh

    echo ""
    echo "═══════════════════════════════════"
    echo "  Update complete"
    echo "═══════════════════════════════════"
}

# Back to the rollback point, keeping uncommitted work; exit 2 if git refuses (a local edit to a file the
# update changed), with the checkout left at the new commit.
rollback() {
    if git reset --keep "$ROLLBACK_COMMIT"; then
        echo "  Rolled back to $ROLLBACK_COMMIT"
        exit 1
    fi
    echo "Rollback refused: a file the update changed has local edits. The checkout is at $(git rev-parse HEAD)."
    echo "  Save your edits, then run: git reset --keep $ROLLBACK_COMMIT"
    exit 2
}

main "$@"; exit $?
