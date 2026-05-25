#!/bin/bash
# One-command macOS release pipeline (W5 capstone): build -> sign -> notarize ->
# staple -> DMG. Sequences the W5-2/4/7/8 + make_dmg scripts. See
# docs/W5-RELEASE-DMG-DESIGN.md.
#
# Real signing/notarization need the Developer ID cert + App Store Connect key
# (NOT in this repo) — see docs/HANDOFF-MICKAEL.md §1. --dry-run prints the plan.
#
# Usage:
#   release_macos.sh --identity "Developer ID Application: NAME (TEAMID)" \
#                    --keychain-profile ava-codec [--version 3.1.0] [--arch aarch64]
#   release_macos.sh --identity "…" --skip-notarize --skip-dmg --dry-run
#
# Version defaults to the repo-root VERSION file (single source of truth), so a
# real release produces "Sovereign-AI-Workstation-<VERSION>.dmg" built from THIS
# checkout — i.e. the paid app == codec-repo at the same version as the OSS engine.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
IDENTITY="${CODEC_SIGN_IDENTITY:-}"
# Default notary profile: the real, Apple-Accepted profile this repo's signer
# uses (set up via `xcrun notarytool store-credentials ava-codec …`). Override
# with --keychain-profile or $CODEC_NOTARY_PROFILE for other signers.
PROFILE="${CODEC_NOTARY_PROFILE:-ava-codec}"
APPLE_ID=""
TEAM_ID=""
PASSWORD=""
OUT="dist"
APP_NAME="Sovereign AI Workstation"
ARCH=""
# Version is the F-5 single source of truth: repo-root VERSION file. The paid
# build is therefore always codec-repo@<VERSION> — same code, same number as the
# OSS engine. Override with --version only for one-off test builds.
VERSION="$(cat "$HERE/../../VERSION" 2>/dev/null || echo dev)"
SKIP_NOTARIZE=0
SKIP_DMG=0
DRY_RUN=0

while [ $# -gt 0 ]; do
    case "$1" in
        --identity) IDENTITY="$2"; shift 2 ;;
        --keychain-profile) PROFILE="$2"; shift 2 ;;
        --apple-id) APPLE_ID="$2"; shift 2 ;;
        --team-id) TEAM_ID="$2"; shift 2 ;;
        --password) PASSWORD="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        --app-name) APP_NAME="$2"; shift 2 ;;
        --arch) ARCH="$2"; shift 2 ;;
        --version) VERSION="$2"; shift 2 ;;
        --skip-notarize) SKIP_NOTARIZE=1; shift ;;
        --skip-dmg) SKIP_DMG=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "release_macos.sh: unknown arg: $1" >&2; exit 2 ;;
    esac
done

APP="$OUT/$APP_NAME.app"
DMG="$OUT/${APP_NAME// /-}-${VERSION}.dmg"

run() {
    if [ "$DRY_RUN" -eq 1 ]; then echo "  [dry-run] $*"; else "$@"; fi
}

notary_creds() {
    if [ -n "$PROFILE" ]; then printf '%s\n' "--keychain-profile" "$PROFILE"
    elif [ -n "$APPLE_ID" ] && [ -n "$TEAM_ID" ] && [ -n "$PASSWORD" ]; then
        printf '%s\n' "--apple-id" "$APPLE_ID" "--team-id" "$TEAM_ID" "--password" "$PASSWORD"
    fi
}

echo "==> CODEC macOS release pipeline (out=$OUT, version=$VERSION$([ "$DRY_RUN" -eq 1 ] && echo ', DRY RUN'))"

echo "-- 1/4 build --"
BUILD=(bash "$HERE/build_app.sh" --with-python --clean --out "$OUT" --app-name "$APP_NAME")
[ -n "$ARCH" ] && BUILD+=(--arch "$ARCH")
run "${BUILD[@]}"

echo "-- 2/4 sign --"
run bash "$HERE/sign_app.sh" --app "$APP" --identity "$IDENTITY"

if [ "$SKIP_NOTARIZE" -eq 0 ]; then
    echo "-- 3/4 notarize + staple --"
    # shellcheck disable=SC2046
    run bash "$HERE/notarize_app.sh" --app "$APP" $(notary_creds)
else
    echo "-- 3/4 notarize -- (skipped)"
fi

if [ "$SKIP_DMG" -eq 0 ]; then
    echo "-- 4/4 dmg --"
    run bash "$HERE/make_dmg.sh" --app "$APP" --out "$DMG" --volname "$APP_NAME"
else
    echo "-- 4/4 dmg -- (skipped)"
fi

echo "==> release pipeline complete${DRY_RUN:+ (dry run)}"
[ "$DRY_RUN" -eq 0 ] && [ "$SKIP_DMG" -eq 0 ] && echo "    artifact: $DMG"
exit 0
