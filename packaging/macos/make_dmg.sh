#!/bin/bash
# Package a (signed + notarized + stapled) .app into a drag-to-install .dmg
# (W5 capstone; closes E-3's "no .dmg build target"). See docs/W5-RELEASE-DMG-DESIGN.md.
#
# Usage: make_dmg.sh --app "dist/Sovereign AI Workstation.app" --out "dist/CODEC.dmg" [--volname NAME] [--dry-run]
set -euo pipefail

APP=""
OUT=""
VOLNAME="Sovereign AI Workstation"
DRY_RUN=0

while [ $# -gt 0 ]; do
    case "$1" in
        --app) APP="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        --volname) VOLNAME="$2"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "make_dmg.sh: unknown arg: $1" >&2; exit 2 ;;
    esac
done

[ -n "$APP" ] || { echo "make_dmg.sh: --app is required" >&2; exit 2; }
[ -n "$OUT" ] || { echo "make_dmg.sh: --out is required" >&2; exit 2; }

if [ "$DRY_RUN" -eq 1 ]; then
    echo "[dry-run] stage $APP + an /Applications symlink, then:"
    echo "[dry-run] hdiutil create -volname \"$VOLNAME\" -srcfolder <stage> -ov -format UDZO \"$OUT\""
    exit 0
fi

[ -d "$APP/Contents" ] || { echo "make_dmg.sh: not an .app bundle: $APP" >&2; exit 1; }

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
echo "==> staging $APP for the DMG"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"   # drag-to-install affordance

echo "==> building $OUT"
rm -f "$OUT"
hdiutil create -volname "$VOLNAME" -srcfolder "$STAGE" -ov -format UDZO "$OUT"
echo "==> done: $OUT"
echo "    (optional: codesign + notarize + staple the .dmg itself; stapling the .app already suffices for Gatekeeper)"
