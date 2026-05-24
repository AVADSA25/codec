#!/bin/bash
# Notarize + staple the signed Sovereign AI Workstation .app (W5-8, E-3).
#
# Apple requires downloaded apps be notarized or Gatekeeper blocks first launch
# ("Apple cannot check it for malicious software"). This zips the SIGNED bundle,
# submits to Apple's notary service, waits for the verdict, then staples the
# ticket so the app validates offline.
#
# Needs App Store Connect credentials (NOT in this repo). Easiest: store them
# once with `xcrun notarytool store-credentials codec-notary --apple-id … \
#   --team-id … --password <app-specific-pw>` then pass --keychain-profile codec-notary.
# Or pass --apple-id/--team-id/--password directly. --dry-run prints the plan.
#
# Usage:
#   notarize_app.sh --app "dist/Sovereign AI Workstation.app" --keychain-profile codec-notary
#   notarize_app.sh --app "…" --staple-only         # re-staple an already-accepted app
#
# TRIAGE: if Apple rejects, almost always an embedded .dylib (mlx/numpy/PyObjC)
# is unsigned or lacks hardened runtime. Get the path with
#   xcrun notarytool log <submission-id> --keychain-profile codec-notary
# then re-run sign_app.sh (signs ALL .dylib) and resubmit.
# See docs/W5-7-8-SIGN-NOTARIZE-DESIGN.md.
set -euo pipefail

APP=""
PROFILE="${CODEC_NOTARY_PROFILE:-}"
APPLE_ID=""
TEAM_ID=""
PASSWORD=""
DRY_RUN=0
STAPLE_ONLY=0

while [ $# -gt 0 ]; do
    case "$1" in
        --app) APP="$2"; shift 2 ;;
        --keychain-profile) PROFILE="$2"; shift 2 ;;
        --apple-id) APPLE_ID="$2"; shift 2 ;;
        --team-id) TEAM_ID="$2"; shift 2 ;;
        --password) PASSWORD="$2"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        --staple-only) STAPLE_ONLY=1; shift ;;
        -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "notarize_app.sh: unknown arg: $1" >&2; exit 2 ;;
    esac
done

[ -n "$APP" ] || { echo "notarize_app.sh: --app is required" >&2; exit 2; }
[ -d "$APP/Contents" ] || { echo "notarize_app.sh: not an .app bundle: $APP" >&2; exit 1; }

ZIP="${APP%.app}-notarize.zip"

# Build the notarytool credential args (keychain profile OR apple-id trio).
cred_args() {
    if [ -n "$PROFILE" ]; then
        printf '%s\n' "--keychain-profile" "$PROFILE"
    elif [ -n "$APPLE_ID" ] && [ -n "$TEAM_ID" ] && [ -n "$PASSWORD" ]; then
        printf '%s\n' "--apple-id" "$APPLE_ID" "--team-id" "$TEAM_ID" "--password" "$PASSWORD"
    fi
}

run() {
    if [ "$DRY_RUN" -eq 1 ]; then echo "  [dry-run] $*"; else "$@"; fi
}

if [ "$STAPLE_ONLY" -eq 1 ]; then
    echo "==> stapling ticket to $APP"
    run xcrun stapler staple "$APP"
    run xcrun stapler validate "$APP"
    exit 0
fi

# Credentials required for the real submit.
if [ "$DRY_RUN" -eq 0 ]; then
    if [ -z "$PROFILE" ] && { [ -z "$APPLE_ID" ] || [ -z "$TEAM_ID" ] || [ -z "$PASSWORD" ]; }; then
        echo "notarize_app.sh: need --keychain-profile OR --apple-id + --team-id + --password" >&2
        exit 1
    fi
fi

echo "==> packaging $APP -> $ZIP"
run ditto -c -k --keepParent "$APP" "$ZIP"

echo "==> submitting to Apple notary service (waits for verdict)"
# shellcheck disable=SC2046
run xcrun notarytool submit "$ZIP" $(cred_args) --wait

echo "==> stapling ticket"
run xcrun stapler staple "$APP"
run xcrun stapler validate "$APP"
run spctl --assess --type execute -vv "$APP"

if [ "$DRY_RUN" -eq 1 ]; then
    echo "[dry-run] nothing submitted."
else
    rm -f "$ZIP"
    echo "==> notarized + stapled. The .app now launches cleanly on any Mac."
fi
