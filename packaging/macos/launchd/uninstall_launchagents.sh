#!/bin/bash
# Uninstall the CODEC launchd LaunchAgents (W5-3, E-7).
#
# Boots out every ai.avadigital.codec.* agent from the user's GUI domain and
# removes its plist. --dry-run prints the plan and touches nothing.
#
# Usage: uninstall_launchagents.sh [--dry-run]
set -euo pipefail

AGENTS_DIR="$HOME/Library/LaunchAgents"
DRY_RUN=0

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "uninstall_launchagents.sh: unknown arg: $1" >&2; exit 2 ;;
    esac
done

shopt -s nullglob
plists=("$AGENTS_DIR"/ai.avadigital.codec.*.plist)
if [ ${#plists[@]} -eq 0 ]; then
    echo "no ai.avadigital.codec.* LaunchAgents found in $AGENTS_DIR"
    exit 0
fi

for plist in "${plists[@]}"; do
    base="$(basename "$plist")"
    label="${base%.plist}"
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "[dry-run] would: launchctl bootout gui/$(id -u)/$label && rm $plist"
        continue
    fi
    launchctl bootout "gui/$(id -u)/$label" 2>/dev/null \
        || launchctl unload -w "$plist" 2>/dev/null \
        || echo "  WARN: could not bootout $label (may not be loaded)" >&2
    rm -f "$plist"
    echo "  removed $base"
done
[ "$DRY_RUN" -eq 1 ] && echo "[dry-run] nothing changed" || echo "==> done."
