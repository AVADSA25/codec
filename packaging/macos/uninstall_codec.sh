#!/bin/bash
# Uninstall CODEC / Sovereign AI Workstation cleanly (W5-12, E-14, P-019).
#
# SAFE BY DEFAULT: with no --yes this is a dry-run (lists, deletes nothing).
# User data (~/.codec) is double-gated behind --yes AND --purge-data.
#
# Auto-removes (CODEC-owned): launchd agents, the .app, ~/Library/Logs/CODEC,
# CODEC's PM2 logs, the app's Caches / WebKit / HTTPStorages folders, the eight
# Quick Actions the app wrote in ~/Library/Services (only those carrying its marker),
# every ai.avadigital.codec.* Keychain item (real $HOME only), and ~/.codec
# (only with --purge-data). Prints guided manual steps for residue Apple/sharing
# rules make unsafe to auto-delete (TCC grants, ~/.cloudflared, your project
# folders, the Hugging Face cache, Google access, the source checkout).
#
# Usage:
#   uninstall_codec.sh [--app PATH] [--dry-run] [--yes] [--purge-data] [--home DIR]
# Never run --yes on a Mac whose CODEC you still use: it deletes the live
# secrets (audit_hmac_secret, internal_token, ...).
# See docs/W5-12-UNINSTALLER-DESIGN.md and docs/PRIVACY-TRUTH-DESIGN.md.
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
SECURITY=/usr/bin/security
KC_PREFIX="ai.avadigital.codec"
BUNDLE_ID="ai.avadigital.codec"   # CFBundleIdentifier in packaging/macos/Info.plist
# PM2 log prefixes: every app name in ecosystem.config.js is codec-* or one of
# the model servers (tests/test_uninstaller.py checks it).
PM2_LOG_NAMES="codec qwen3.6 whisper-stt kokoro-82m"
# Every Keychain name the code uses: the KEY_* constants in codec_keychain.py,
# KEYCHAIN_KEY in codec_push.py and _TOKEN_SERVICE in skills/mcp_connect.py.
# tests/test_uninstaller.py fails when this list and the code disagree. A real
# run also finds any other $KC_PREFIX.* item (a key_slot you named for a cloud
# model or the delegate webhook) with `security dump-keychain`.
KC_SERVICES="audit_hmac_secret dashboard_token gemini_api_key internal_token license_key llm_api_key mcp_tokens oauth_state pexels_api_key serper_api_key telegram_bot_token vapid_private_key"

# Guarded recursive remove: refuses empty / "/" / the home base itself.
safe_rm() {
    local p="$1"
    if [ -z "$p" ] || [ "$p" = "/" ] || [ "$p" = "$HOME_DIR" ] || [ "$p" = "$HOME" ]; then
        echo "  SKIP (guard): refusing to remove '$p'" >&2; return 0
    fi
    if [ ! -e "$p" ]; then echo "  (absent)  $p"; return 0; fi
    if [ "$ACT" -eq 0 ]; then echo "  [dry-run] would remove: $p"; else rm -rf "$p"; echo "  removed:  $p"; fi
}

# Every Keychain service to delete: the code's names, then any other item whose
# service starts with $KC_PREFIX. dump-keychain without -d prints attributes
# only: it reads no secret value and shows no prompt.
kc_scan() {
    [ -x "$SECURITY" ] || return 0
    { "$SECURITY" dump-keychain 2>/dev/null || true; } \
        | sed -n 's/^.*"svce"<blob>="\('"$KC_PREFIX"'\.[A-Za-z0-9_.-]*\)".*$/\1/p'
}
kc_all_services() {
    local svc
    for svc in $KC_SERVICES; do echo "$KC_PREFIX.$svc"; done
    kc_scan
}

# One delete-generic-password call removes one item; a service can hold
# several (mcp_tokens keeps one per connector), so repeat until none is left.
kc_delete_service() {
    local svc="$1" n=0
    while [ "$n" -lt 100 ] && "$SECURITY" delete-generic-password -s "$svc" </dev/null >/dev/null 2>&1; do
        n=$((n + 1))
    done
    if [ "$n" -gt 0 ]; then echo "  removed keychain item: $svc ($n)"; else echo "  (absent)  keychain item: $svc"; fi
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
echo "-- PM2 logs (~/.pm2/logs, source installs) --"
shopt -s nullglob
pm2_logs=()
for name in $PM2_LOG_NAMES; do pm2_logs+=("$HOME_DIR/.pm2/logs/$name"-*); done
shopt -u nullglob
if [ "${#pm2_logs[@]}" -eq 0 ]; then
    echo "  (absent)  $HOME_DIR/.pm2/logs/{$(echo $PM2_LOG_NAMES | tr ' ' ',')}-*"
else
    for f in "${pm2_logs[@]}"; do safe_rm "$f"; done
fi

# 4) app caches and web data (named after the bundle id) ----------------------
echo "-- app caches and web data ($BUNDLE_ID) --"
for d in "Library/Caches" "Library/WebKit" "Library/HTTPStorages"; do
    safe_rm "$HOME_DIR/$d/$BUNDLE_ID"
done

# 4b) Quick Actions the app wrote (packaging/macos/quick_actions.py, P-029) ----
# Only a folder whose Info.plist carries the app's marker is removed; a hand-made
# workflow of the same name (a source install's) is listed and kept.
echo "-- Quick Actions (~/Library/Services) --"
QA_MARKER="<key>CODECQuickAction</key>"
qa_removed=0
for title in Elevate Explain Prompt Proofread "Read Aloud" Reply Save Translate; do
    wf="$HOME_DIR/Library/Services/CODEC $title.workflow"
    if [ -e "$wf" ] && ! grep -qF "$QA_MARKER" "$wf/Contents/Info.plist" 2>/dev/null; then
        echo "  kept (not written by the app): $wf"
        continue
    fi
    if [ -e "$wf" ]; then qa_removed=1; fi
    safe_rm "$wf"
done
if [ "$ACT" -eq 1 ] && [ "$REAL_HOME" -eq 1 ] && [ "$qa_removed" -eq 1 ] && [ -x /System/Library/CoreServices/pbs ]; then
    /System/Library/CoreServices/pbs -update >/dev/null 2>&1 || true   # drop them from the Services menu now
fi

# 5) Keychain (real $HOME only — never touched in test/sandbox mode) ----------
echo "-- Keychain items ($KC_PREFIX.*) --"
if [ "$REAL_HOME" -eq 1 ]; then
    kc_all_services | awk '!seen[$0]++' | while IFS= read -r svc; do
        [ -n "$svc" ] || continue
        if [ "$ACT" -eq 1 ]; then
            kc_delete_service "$svc"
        else
            echo "  [dry-run] would delete keychain item: $svc"
        fi
    done
    # Check, do not trust: a locked keychain or a refused delete leaves items.
    if [ "$ACT" -eq 1 ]; then
        left="$(kc_scan | sort -u)"
        if [ -n "$left" ]; then
            echo "  STILL PRESENT (keychain locked, or a delete was refused):" >&2
            echo "$left" | sed 's/^/    /' >&2
        fi
    fi
else
    echo "  [skipped] non-default --home (sandbox/test): Keychain left untouched. A real run deletes:"
    for svc in $KC_SERVICES; do echo "  [sandbox, not touched] keychain item: $KC_PREFIX.$svc"; done
    echo "  [sandbox, not touched] plus any other $KC_PREFIX.* item found in your keychains"
fi

# 6) user data (~/.codec) — double-gated -------------------------------------
echo "-- user data (~/.codec) --"
echo "  $HOME_DIR/.codec holds (removed only with --yes --purge-data):"
while IFS='|' read -r name what; do
    if [ -e "$HOME_DIR/.codec/$name" ]; then state="present"; else state="absent"; fi
    printf '    %-24s %-8s %s\n' "$name" "$state" "$what"
done <<'ITEMS'
memory.db|conversations and remembered facts
qchat.db|quick-chat history
vibe.db|Vibe chat history
clipboard.db|clipboard history
notes|CODEC Notes (deleted ones in notes/.trash)
agents|Project-mode agents: plans, grants, messages
google_token.json|Google sign-in token
google_credentials.json|Google OAuth client
config.json|settings
mcp_servers.json|connectors you added (and any API-key header you typed)
backups|checked copies of memory.db and a state archive
audit.log|audit log (and dated audit.log.YYYY-MM-DD files)
photos|webcam photos
images|pictures made with Create image
skills|your own skills
plugins|your own plugins
models|downloaded models
secrets.enc.json|secret store used when no Keychain is available
ITEMS
echo "    ...and everything else under $HOME_DIR/.codec"
if [ "$PURGE" -eq 1 ]; then
    safe_rm "$HOME_DIR/.codec"
else
    echo "  KEEPING $HOME_DIR/.codec. Use --purge-data to remove it too."
fi

# 7) manual residue (cannot/should-not be auto-removed) ----------------------
cat <<MANUAL

-- manual steps (please review) --
  * Privacy grants (TCC): macOS does NOT let an app revoke its own permissions.
    Open System Settings > Privacy & Security and remove "Sovereign AI Workstation"
    from: Accessibility, Microphone, Camera, Screen Recording, Automation, Input Monitoring.
  * Source installs under PM2: stop the codec apps first (pm2 delete <name>); this script never touches PM2.
  * Google: remove CODEC's access at https://myaccount.google.com/permissions.
  * Your project files are kept: ~/codec-projects (Project mode) and ~/codec-workspace (Vibe).
  * Model cache: ~/.codec/models goes with --purge-data; the Hugging Face cache
    (~/.cache/huggingface/hub) is shared with other apps, so review it by hand.
  * Cloudflare tunnel: review ~/.cloudflared/config.yml (may contain non-CODEC tunnels).
  * Source checkout (~/codec-repo) is never touched by this uninstaller.
MANUAL

echo
[ "$ACT" -eq 0 ] && echo "=== dry run complete — re-run with --yes to uninstall ===" || echo "=== uninstall complete ==="
