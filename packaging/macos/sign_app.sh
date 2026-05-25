#!/bin/bash
# Code-sign the Sovereign AI Workstation .app with hardened runtime (W5-7, E-2).
#
# Signs INSIDE-OUT: every nested Mach-O (the embedded Python's .dylib/.so + its
# python3 binary, the launcher, any bundled cloudflared / Touch ID helper) is
# signed before the outer .app seal — codesign requires inner code be valid
# first. Hardened runtime + the W5-1 entitlements are applied so the result can
# be notarized (W5-8).
#
# Needs a Developer ID Application identity (NOT in this repo). Provide via
# --identity or $CODEC_SIGN_IDENTITY. --dry-run prints the plan and signs nothing.
#
# Usage:
#   sign_app.sh --app "dist/Sovereign AI Workstation.app" \
#               --identity "Developer ID Application: NAME (TEAMID)" [--dry-run]
#   sign_app.sh --app "…" --verify-only
# See docs/W5-7-8-SIGN-NOTARIZE-DESIGN.md.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
APP=""
IDENTITY="${CODEC_SIGN_IDENTITY:-}"
ENTITLEMENTS="$HERE/codec.entitlements"
DRY_RUN=0
VERIFY_ONLY=0

while [ $# -gt 0 ]; do
    case "$1" in
        --app) APP="$2"; shift 2 ;;
        --identity) IDENTITY="$2"; shift 2 ;;
        --entitlements) ENTITLEMENTS="$2"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        --verify-only) VERIFY_ONLY=1; shift ;;
        -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "sign_app.sh: unknown arg: $1" >&2; exit 2 ;;
    esac
done

[ -n "$APP" ] || { echo "sign_app.sh: --app is required" >&2; exit 2; }
[ -d "$APP/Contents" ] || { echo "sign_app.sh: not an .app bundle: $APP" >&2; exit 1; }

if [ "$VERIFY_ONLY" -eq 1 ]; then
    echo "==> verifying signature on $APP"
    codesign --verify --deep --strict --verbose=2 "$APP"
    spctl --assess --type execute -vv "$APP" || echo "  (spctl passes only after notarization + staple — W5-8)"
    exit 0
fi

[ -f "$ENTITLEMENTS" ] || { echo "sign_app.sh: entitlements not found: $ENTITLEMENTS" >&2; exit 1; }
if [ "$DRY_RUN" -eq 0 ] && [ -z "$IDENTITY" ]; then
    echo "sign_app.sh: no signing identity (pass --identity or set CODEC_SIGN_IDENTITY)" >&2
    echo "  list yours with: security find-identity -v -p codesigning" >&2
    exit 1
fi

# Ambiguity guard: codesign fails cryptically ("ambiguous … matches A and B") when
# the keychain has >1 identity with the same Common Name. If --identity was given
# as a CN (not a 40-hex SHA-1) and matches multiple identities, fail clearly and
# tell the operator to pass the specific SHA-1 instead.
if [ "$DRY_RUN" -eq 0 ] && ! printf '%s' "$IDENTITY" | grep -Eq '^[0-9A-Fa-f]{40}$'; then
    _matches="$(security find-identity -v -p codesigning 2>/dev/null \
                | grep -F "$IDENTITY" || true)"
    _n="$(printf '%s\n' "$_matches" | grep -c . || true)"
    if [ "${_n:-0}" -gt 1 ]; then
        echo "sign_app.sh: signing identity '$IDENTITY' is AMBIGUOUS — $_n certs share that name:" >&2
        printf '%s\n' "$_matches" | sed 's/^/    /' >&2
        echo "  Re-run with the specific SHA-1, e.g.:" >&2
        echo "    --identity $(printf '%s\n' "$_matches" | head -1 | awk '{print $2}')" >&2
        exit 1
    fi
fi

codesign_one() {
    local target="$1"
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "  [dry-run] codesign --force --options runtime --timestamp --entitlements \"$ENTITLEMENTS\" --sign \"$IDENTITY\" \"$target\""
    else
        codesign --force --options runtime --timestamp --entitlements "$ENTITLEMENTS" --sign "$IDENTITY" "$target"
        echo "  signed: $target"
    fi
}

# Pre-sign cleanup: the bundled Python's bin/ contains pip console-script shims
# (jsonschema, httpx, fastmcp, mcp, normalizer, …) — text executables CODEC never
# invokes (it imports the libraries; `python3 -m pip` still works without bin/pip).
# As unsigned text executables in a non-standard bundle location they break the
# final seal with "code object is not signed at all". Strip everything in bin/
# except the interpreter + Mach-O binaries (the standard py2app/briefcase fix).
PYBIN="$APP/Contents/Resources/python/bin"
if [ "$DRY_RUN" -eq 0 ] && [ -d "$PYBIN" ]; then
    _stripped=0
    for f in "$PYBIN"/* "$PYBIN"/.[!.]*; do
        [ -e "$f" ] || continue
        base="$(basename "$f")"
        case "$base" in
            python*) continue ;;                       # keep the interpreter (+ symlinks)
        esac
        if file "$f" 2>/dev/null | grep -q "Mach-O"; then
            continue                                   # keep Mach-O (signed below)
        fi
        rm -f "$f" && _stripped=$((_stripped+1))
    done
    echo "-- stripped $_stripped non-interpreter console-script shim(s) from bundled python/bin --"
fi

echo "==> signing $APP inside-out (identity: ${IDENTITY:-<dry-run>})"

echo "-- nested libraries (*.dylib, *.so) --"
while IFS= read -r lib; do
    [ -n "$lib" ] && codesign_one "$lib"
done < <(find "$APP/Contents" -type f \( -name '*.dylib' -o -name '*.so' \) | sort)

echo "-- Mach-O executables --"
while IFS= read -r exe; do
    [ -n "$exe" ] || continue
    if file "$exe" 2>/dev/null | grep -q "Mach-O"; then
        codesign_one "$exe"
    fi
done < <(find "$APP/Contents" -type f -perm -u+x | sort)

echo "==> finally sign the app bundle: $APP"
codesign_one "$APP"

if [ "$DRY_RUN" -eq 0 ]; then
    echo "==> verifying"
    codesign --verify --deep --strict --verbose=2 "$APP"
    spctl --assess --type execute -vv "$APP" || echo "  (spctl passes only after notarization + staple — W5-8)"
    echo "==> signed. Next: notarize_app.sh --app \"$APP\""
else
    echo "[dry-run] nothing signed."
fi
