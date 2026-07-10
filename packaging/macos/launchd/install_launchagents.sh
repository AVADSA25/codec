#!/bin/bash
# Install the CODEC fleet as launchd LaunchAgents (W5-3, E-7). OPT-IN.
#
# Generates ai.avadigital.codec.<svc>.plist from the service list, drops them in
# ~/Library/LaunchAgents/, and bootstraps them into the user's GUI domain.
#
# Service list, in priority order:
#   1. --services-json PATH        (explicit)
#   2. <script dir>/../services.json  (shipped in the .app — see build_app.sh)
#   3. <repo>/ecosystem.config.js  (dev machines; requires `node`)
#
# (2) exists because a BUYER'S Mac has no node and no PM2: reading the PM2
# ecosystem there is impossible. build_app.sh dumps the ecosystem to services.json
# at build time, when node IS present, and ships that.
#
# Safety:
#   * --dry-run prints the plan and touches nothing.
#   * REFUSES if the PM2 codec fleet is currently running (so it can't double-run
#     the services on a dev machine) unless --force is given.
#
# Usage: install_launchagents.sh [--dry-run] [--force] [--interpreter PATH]
#                                [--workdir DIR] [--services-json PATH]
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
GEN="$HERE/generate_launchagents.py"
AGENTS_DIR="$HOME/Library/LaunchAgents"

DRY_RUN=0
FORCE=0
INTERP=""
WORKDIR=""
SERVICES_JSON=""

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --force) FORCE=1; shift ;;
        --interpreter) INTERP="$2"; shift 2 ;;
        --workdir) WORKDIR="$2"; shift 2 ;;
        --services-json) SERVICES_JSON="$2"; shift 2 ;;
        -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "install_launchagents.sh: unknown arg: $1" >&2; exit 2 ;;
    esac
done

# Resolve the service list without requiring node wherever possible.
if [ -z "$SERVICES_JSON" ] && [ -f "$HERE/../services.json" ]; then
    SERVICES_JSON="$HERE/../services.json"
fi

# --- guard: don't double-run on top of a live PM2 fleet --------------------
if command -v pm2 >/dev/null 2>&1; then
    if pm2 jlist 2>/dev/null | grep -q '"name":"codec'; then
        if [ "$FORCE" -ne 1 ]; then
            echo "REFUSING: the PM2 codec fleet appears to be running." >&2
            echo "  launchd + PM2 would double-run the services. Stop PM2 first:" >&2
            echo "    pm2 delete ecosystem.config.js" >&2
            echo "  or re-run with --force if you know what you're doing." >&2
            exit 1
        fi
        echo "WARNING: PM2 codec fleet is running; --force given, continuing." >&2
    fi
fi

if [ -n "$SERVICES_JSON" ]; then
    [ -f "$SERVICES_JSON" ] || { echo "services JSON not found: $SERVICES_JSON" >&2; exit 1; }
    GEN_ARGS=(--from-json "$SERVICES_JSON" --out "$AGENTS_DIR")
else
    REPO="$(cd "$HERE/../../.." && pwd)"
    ECOSYSTEM="$REPO/ecosystem.config.js"
    [ -f "$ECOSYSTEM" ] || {
        echo "no service list: pass --services-json, ship services.json, or run from the repo" >&2
        exit 1
    }
    command -v node >/dev/null 2>&1 || {
        echo "node is required to read $ECOSYSTEM. On a machine without node, use" >&2
        echo "  --services-json PATH  (build_app.sh ships one inside the .app)" >&2
        exit 1
    }
    GEN_ARGS=(--from-ecosystem "$ECOSYSTEM" --out "$AGENTS_DIR")
fi
[ -n "$INTERP" ] && GEN_ARGS+=(--interpreter "python3=$INTERP" --interpreter "/usr/local/bin/python3.13=$INTERP")
[ -n "$WORKDIR" ] && GEN_ARGS+=(--workdir "$WORKDIR")

# Inside the .app the only interpreter is the bundled one; python3 may not be on PATH.
PY="${INTERP:-$(command -v python3 || true)}"
[ -n "$PY" ] || { echo "no python3 found (pass --interpreter)" >&2; exit 1; }

if [ "$DRY_RUN" -eq 1 ]; then
    echo "[dry-run] would generate LaunchAgents into $AGENTS_DIR:"
    "$PY" "$GEN" "${GEN_ARGS[@]}" --dry-run
    echo "[dry-run] would then: launchctl bootstrap gui/$(id -u) <each plist>"
    exit 0
fi

mkdir -p "$AGENTS_DIR" "$HOME/Library/Logs/CODEC"
"$PY" "$GEN" "${GEN_ARGS[@]}"

echo "==> bootstrapping LaunchAgents into gui/$(id -u)"
for plist in "$AGENTS_DIR"/ai.avadigital.codec.*.plist; do
    [ -f "$plist" ] || continue
    launchctl bootstrap "gui/$(id -u)" "$plist" 2>/dev/null \
        || launchctl load -w "$plist" 2>/dev/null \
        || echo "  WARN: could not bootstrap $(basename "$plist")" >&2
    echo "  loaded $(basename "$plist")"
done
echo "==> done. Check: launchctl list | grep ai.avadigital.codec"
