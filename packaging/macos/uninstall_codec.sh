#!/bin/bash
# Uninstall CODEC / Sovereign AI Workstation cleanly (W5-12, E-14).
#
# SAFE BY DEFAULT: with no --yes this is a dry-run (lists, deletes nothing).
# User data (~/.codec) is double-gated behind --yes AND --purge-data.
#
# Auto-removes (CODEC-owned): launchd agents, the .app, ~/Library/Logs/CODEC,
# Keychain items (real $HOME only), and ~/.codec (only with --purge-data).
# Prints guided manual steps for residue Apple/sharing rules make unsafe to
# auto-delete (TCC grants, ~/.cloudflared, model cache, source checkout).
#
# Usage:
#   uninstall_codec.sh [--app PATH] [--dry-run] [--yes] [--purge-data] [--home DIR]
# See docs/W5-12-UNINSTALLER-DESIGN.md.
set -euo pipefail

HOME_DIR="$HOME"
APP="/Applications/Sovereign AI Workstation.app"
ACT=0            # 0 = dry-run (default); 1 = actually delete (--yes)
PURGE=0          # remove ~/.codec (requires --yes)

while [ $# -gt 0 ]; do
    case "$1" in
        --app) APP="$2"; shift 2 ;;
        --home) HOME_DIR="$2"; shift 2 ;;
        --dry-run) ACT=0; shift ;;
        --yes) ACT=1; shift ;;
        --purge-data) PURGE=1; shift ;;
        -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "uninstall_codec.sh: unknown arg: $1" >&2; exit 2 ;;
    esac
done

REAL_HOME=0; [ "$HOME_DIR" = "$HOME" ] && REAL_HOME=1
KC_SERVICES="audit_hmac_secret internal_token oauth_state dashboard_token llm_api_key gemini_api_key pexels_api_key serper_api_key telegram_bot_token license"

# Guarded recursive remove: refuses empty / "/" / the home base itself.
safe_rm() {
    local p="$1"
    if [ -z "$p" ] || [ "$p" = "/" ] || [ "$p" = "$HOME_DIR" ] || [ "$p" = "$HOME" ]; then
        echo "  SKIP (guard): refusing to remove '$p'" >&2; return 0
    fi
    if [ ! -e "$p" ]; then echo "  (absent)  $p"; return 0; fi
    if [ "$ACT" -eq 0 ]; then echo "  [dry-run] would remove: $p"; else rm -rf "$p"; echo "  removed:  $p"; fi
}

if [ "$ACT" -eq 0 ]; then
    echo "=== CODEC uninstaller — DRY RUN (nothing will be deleted; pass --yes to act) ==="
else
    echo "=== CODEC uninstaller — LIVE (--yes) ==="
fi
echo "home=$HOME_DIR  app=$APP  purge-data=$PURGE"
echo

# 1) launchd LaunchAgents -----------------------------------------------------
echo "-- launchd agents (ai.avadigital.codec.*) --"
shopt -s nullglob
for plist in "$HOME_DIR/Library/LaunchAgents"/ai.avadigital.codec.*.plist; do
    label="$(basename "$plist" .plist)"
    if [ "$ACT" -eq 1 ] && [ "$REAL_HOME" -eq 1 ]; then
        launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || launchctl unload -w "$plist" 2>/dev/null || true
    fi
    safe_rm "$plist"
done
shopt -u nullglob

# 2) the .app bundle ----------------------------------------------------------
echo "-- application bundle --"
safe_rm "$APP"

# 3) logs ---------------------------------------------------------------------
echo "-- logs (~/Library/Logs/CODEC) --"
safe_rm "$HOME_DIR/Library/Logs/CODEC"

# 4) Keychain (real $HOME only — never touched in test/sandbox mode) ----------
echo "-- Keychain items (ai.avadigital.codec.*) --"
if [ "$REAL_HOME" -eq 1 ]; then
    for svc in $KC_SERVICES; do
        full="ai.avadigital.codec.$svc"
        if [ "$ACT" -eq 1 ]; then
            security delete-generic-password -s "$full" >/dev/null 2>&1 && echo "  removed:  $full" || echo "  (absent)  $full"
        else
            echo "  [dry-run] would delete keychain item: $full"
        fi
    done
else
    echo "  [skipped] non-default --home (sandbox/test): Keychain left untouched"
fi

# 5) user data (~/.codec) — double-gated -------------------------------------
echo "-- user data (~/.codec) --"
if [ "$PURGE" -eq 1 ]; then
    safe_rm "$HOME_DIR/.codec"
else
    echo "  KEEPING $HOME_DIR/.codec (config, memory, skills). Use --purge-data to remove it too."
fi

# 6) manual residue (cannot/should-not be auto-removed) ----------------------
cat <<MANUAL

-- manual steps (please review) --
  * Privacy grants (TCC): macOS does NOT let an app revoke its own permissions.
    Open System Settings > Privacy & Security and remove "Sovereign AI Workstation"
    from: Accessibility, Microphone, Screen Recording, Automation, Input Monitoring.
  * Cloudflare tunnel: review ~/.cloudflared/config.yml (may contain non-CODEC tunnels).
  * Model cache: large model files (HuggingFace / ~/.codec/models) are removed only with --purge-data.
  * Source checkout (~/codec-repo) is never touched by this uninstaller.
MANUAL

echo
[ "$ACT" -eq 0 ] && echo "=== dry run complete — re-run with --yes to uninstall ===" || echo "=== uninstall complete ==="
