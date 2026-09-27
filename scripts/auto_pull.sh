#!/bin/bash
# CODEC safe auto-pull: fast-forward the repo checkout to origin/main.
#
# Replaces the blind daily cron (`git fetch && git reset --hard origin/main`).
#   - mkdir lock (macOS has no flock / timeout binaries; perl alarm caps time)
#   - skips when tracked files are modified or the checkout is not on main
#   - `git merge --ff-only` only; never reset --hard, never discards local work
#   - after a pull, import-smokes codec_dashboard + codec_mcp_http; if that
#     fails, rolls back to the previous HEAD with `git reset --keep`
#   - never restarts services (that is the owner's action); posts a CODEC
#     notification saying what to restart, or why it failed
#   - always appends one proof-of-execution line to ~/.codec/logs/auto_pull.log
#
# Crontab line (replaces the old reset --hard line):
#   0 6 * * * /bin/bash "$HOME/codec-repo/scripts/auto_pull.sh" >/dev/null 2>&1
#
# Manual run: bash scripts/auto_pull.sh   (exit 0 = pulled/up to date/skipped)
#
# Env overrides (tests, other machines):
#   CODEC_REPO               checkout to update   (default: this script's repo)
#   CODEC_STATE              CODEC state dir      (default: ~/.codec)
#   CODEC_PY                 python for the smoke (default: /usr/local/bin/python3.13)
#   AUTO_PULL_BRANCH         branch to track      (default: main)
#   AUTO_PULL_SMOKE_TIMEOUT  smoke cap, seconds   (default: 60)

main() {
    set -u
    export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:${PATH:-}"

    REPO="${CODEC_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
    STATE="${CODEC_STATE:-$HOME/.codec}"
    PY="${CODEC_PY:-/usr/local/bin/python3.13}"
    BRANCH="${AUTO_PULL_BRANCH:-main}"
    SMOKE_TIMEOUT="${AUTO_PULL_SMOKE_TIMEOUT:-60}"
    LOG="$STATE/logs/auto_pull.log"
    LOCK="$STATE/auto_pull.lock"

    OLD="-"; NEW="-"; COMMITS=0; OUTCOME="aborted"; DETAIL=""; NOTIFY="none"; HAVE_LOCK=0
    mkdir -p "$STATE/logs"
    trap finish EXIT

    if ! cd "$REPO" 2>/dev/null || ! git rev-parse --git-dir >/dev/null 2>&1; then
        OUTCOME="error"; DETAIL="not a git checkout: $REPO"
        notify error "Auto-pull failed" "$DETAIL"
        exit 1
    fi
    OLD="$(git rev-parse --short HEAD)"; NEW="$OLD"   # logged even if the lock is busy

    take_lock || exit 0
    OLD_FULL="$(git rev-parse HEAD)"                  # re-read under the lock
    OLD="$(git rev-parse --short HEAD)"; NEW="$OLD"

    local out rc
    out="$(capped 120 git fetch --quiet origin 2>&1)"; rc=$?
    if [ "$rc" -ne 0 ]; then
        OUTCOME="fetch_failed"; DETAIL="git fetch $(why "$rc" 120 "$out")"
        notify error "Auto-pull failed" "Could not fetch origin: $DETAIL"
        exit 1
    fi

    local upstream="origin/$BRANCH" branch behind
    if ! git rev-parse --verify --quiet "$upstream" >/dev/null; then
        OUTCOME="error"; DETAIL="$upstream does not exist"
        notify error "Auto-pull failed" "$DETAIL"
        exit 1
    fi
    branch="$(git symbolic-ref --quiet --short HEAD || true)"
    if [ "$branch" != "$BRANCH" ]; then
        behind="$(git rev-list --count "$BRANCH..$upstream" 2>/dev/null || echo 0)"
        OUTCOME="skipped_branch"; DETAIL="checkout is on '${branch:-detached HEAD}', not $BRANCH"
        [ "$behind" -gt 0 ] && notify warning "Auto-pull skipped" \
            "$behind new commit(s) on $upstream not pulled: $DETAIL."
        exit 0
    fi

    behind="$(git rev-list --count "HEAD..$upstream")"
    if [ -n "$(git status --porcelain -uno)" ]; then
        OUTCOME="skipped_dirty"
        DETAIL="$(git status --porcelain -uno | wc -l | tr -d ' ') tracked file(s) modified"
        [ "$behind" -gt 0 ] && notify warning "Auto-pull skipped" \
            "$behind new commit(s) on $upstream not pulled: $DETAIL in $REPO. Run scripts/auto_pull.sh again once the tree is clean."
        exit 0
    fi
    if [ "$behind" -eq 0 ]; then
        OUTCOME="up_to_date"
        exit 0
    fi
    if ! git merge-base --is-ancestor HEAD "$upstream"; then
        OUTCOME="diverged"; DETAIL="local $BRANCH has commits not on $upstream; fast-forward impossible"
        notify error "Auto-pull failed" "$DETAIL. Nothing was changed."
        exit 1
    fi

    out="$(capped 120 git merge --ff-only --quiet "$upstream" 2>&1)"; rc=$?
    if [ "$rc" -ne 0 ]; then
        OUTCOME="ff_failed"; DETAIL="git merge --ff-only $(why "$rc" 120 "$out")"
        NEW="$(git rev-parse --short HEAD)"
        notify error "Auto-pull failed" "$DETAIL. Nothing was changed."
        exit 1
    fi
    NEW="$(git rev-parse --short HEAD)"
    COMMITS="$(git rev-list --count "$OLD_FULL..HEAD")"

    out="$(capped "$SMOKE_TIMEOUT" "$PY" -c "import codec_dashboard, codec_mcp_http" 2>&1)"; rc=$?
    if [ "$rc" -ne 0 ]; then
        local smoke="import smoke $(why "$rc" "$SMOKE_TIMEOUT" "$out")"
        if git reset --quiet --keep "$OLD_FULL" 2>/dev/null; then
            OUTCOME="rolled_back"; NEW="$(git rev-parse --short HEAD)"
            DETAIL="rejected $(git rev-parse --short "$upstream"): $smoke"
            notify error "Auto-pull rolled back" \
                "$COMMITS new commit(s) ($OLD..$(git rev-parse --short "$upstream")) failed the import smoke and were rolled back to $OLD. $smoke"
        else
            OUTCOME="rollback_failed"; NEW="$(git rev-parse --short HEAD)"
            DETAIL="$smoke; git reset --keep $OLD failed"
            notify error "Auto-pull needs attention" \
                "Pulled $COMMITS commit(s) to $NEW, the import smoke failed and the rollback to $OLD failed. Do not restart services until fixed. $smoke"
        fi
        exit 1
    fi

    OUTCOME="pulled"; DETAIL="$COMMITS commit(s)"
    notify success "Auto-pull: $COMMITS new commit(s)" \
        "$COMMITS new commits pulled ($OLD..$NEW) — restart codec-dashboard/open-codec/codec-mcp-http to apply"
    exit 0
}

# Run a command with a wall-clock cap (SIGALRM survives exec).
capped() {
    local secs="$1"; shift
    perl -e 'alarm shift @ARGV; exec @ARGV or die "exec failed: $!\n"' "$secs" "$@"
}

# Most useful line of command output: git's first fatal/error line, else the
# last non-empty line (a Python traceback's exception line).
last_line() {
    local lines
    lines="$(printf '%s' "$1" | tr -d '\r' | awk 'NF')"
    { printf '%s\n' "$lines" | grep -m1 -E '^(fatal|error):' \
        || printf '%s\n' "$lines" | tail -n 1; } | cut -c1-200
}

# why <exit code> <cap seconds> <output>: short failure reason for logs.
why() {
    if [ "$1" -eq 142 ]; then
        echo "timed out after ${2}s"
    else
        echo "failed (exit $1): $(last_line "$3")"
    fi
}

take_lock() {
    if mkdir "$LOCK" 2>/dev/null; then
        echo $$ > "$LOCK/pid"; HAVE_LOCK=1; return 0
    fi
    local pid stale=0
    pid="$(cat "$LOCK/pid" 2>/dev/null || true)"
    if [ -n "$pid" ]; then
        ps -p "$pid" >/dev/null 2>&1 || stale=1
    elif [ -n "$(find "$LOCK" -maxdepth 0 -mmin +60 2>/dev/null)" ]; then
        stale=1
    fi
    if [ "$stale" -eq 1 ] && rm -rf "$LOCK" && mkdir "$LOCK" 2>/dev/null; then
        echo $$ > "$LOCK/pid"; HAVE_LOCK=1; return 0
    fi
    OUTCOME="skipped_locked"; DETAIL="another run holds $LOCK (pid ${pid:-unknown})"
    return 1
}

# Post to ~/.codec/notifications.json in the same shape codec_heartbeat
# uses, under codec_jsonstore's cross-process lock + atomic write.
notify() {
    local status="$1" title="$2" body="$3"
    if (cd "$REPO" 2>/dev/null && NOTIF_PATH="$STATE/notifications.json" NOTIF_STATUS="$status" \
        NOTIF_TITLE="$title" NOTIF_BODY="$body" capped 30 "$PY" -c '
import os, uuid
from datetime import datetime
import codec_jsonstore
def add(notifs):
    notifs = notifs if isinstance(notifs, list) else []
    notifs.insert(0, {
        "id": f"notif_{uuid.uuid4().hex[:10]}",
        "type": "task_report",
        "title": os.environ["NOTIF_TITLE"],
        "body": os.environ["NOTIF_BODY"][:2000],
        "status": os.environ["NOTIF_STATUS"],
        "created": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "read": False,
        "schedule_id": "auto_pull",
    })
    return notifs
codec_jsonstore.read_modify_write(os.environ["NOTIF_PATH"], add, default_factory=list)
') >/dev/null 2>&1; then
        NOTIFY="ok"
    else
        NOTIFY="failed"
    fi
}

# One proof-of-execution line per run, whatever happened.
finish() {
    local detail
    detail="$(printf '%s' "$DETAIL" | tr '\n"' "  " | cut -c1-240)"
    printf '%s outcome=%s old=%s new=%s commits=%s notify=%s detail="%s"\n' \
        "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$OUTCOME" "$OLD" "$NEW" "$COMMITS" "$NOTIFY" "$detail" \
        >> "$LOG" 2>/dev/null
    [ "$HAVE_LOCK" -eq 1 ] && rm -rf "$LOCK"
}

main "$@"
