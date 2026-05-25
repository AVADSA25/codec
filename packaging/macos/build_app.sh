#!/bin/bash
# CODEC / Sovereign AI Workstation — assemble the (unsigned) .app bundle (W5-2).
#
# Pure mkdir/cp assembly — no macOS-only tools required to build, so it runs in
# CI too. `--validate` (macOS only) runs plutil on the copied Info.plist.
#
# Scope (W5-2): produces the bundle skeleton from the repo + W5-1 metadata.
#   - Python.framework bundling   -> W5-4 (Frameworks/ is created empty here)
#   - code signing / hardened RT  -> W5-7 (bundle is unsigned)
#   - notarization / staple / DMG -> W5-8 / installer
# See docs/W5-2-APP-BUNDLE-DESIGN.md.
set -euo pipefail

APP_NAME="Sovereign AI Workstation"
OUT_DIR="dist"
CLEAN=0
VALIDATE=0
WITH_PYTHON=0
PY_ARCH=""

usage() {
    cat <<'USAGE'
Usage: build_app.sh [--out DIR] [--app-name NAME] [--clean] [--validate]
                    [--with-python] [--arch aarch64|x86_64]
  --out DIR        output directory for the .app (default: dist)
  --app-name NAME  bundle display name (default: "Sovereign AI Workstation")
  --clean          remove any existing <out>/<name>.app first
  --validate       run plutil on the copied Info.plist (macOS only)
  --with-python    bundle the relocatable Python runtime (W5-4) via bundle_python.sh
  --arch ARCH      target arch for --with-python (default: host arch)
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --out) OUT_DIR="$2"; shift 2 ;;
        --app-name) APP_NAME="$2"; shift 2 ;;
        --clean) CLEAN=1; shift ;;
        --validate) VALIDATE=1; shift ;;
        --with-python) WITH_PYTHON=1; shift ;;
        --arch) PY_ARCH="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "build_app.sh: unknown arg: $1" >&2; usage; exit 2 ;;
    esac
done

# packaging/macos/build_app.sh  ->  repo root is two dirs up.
PKG_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$PKG_DIR/../.." && pwd)"

INFO_PLIST="$PKG_DIR/Info.plist"
LAUNCHER_SRC="$PKG_DIR/launcher/codec"
ENTRY_SRC="$PKG_DIR/launcher/codec_app_main.py"

for f in "$INFO_PLIST" "$LAUNCHER_SRC" "$ENTRY_SRC"; do
    [ -f "$f" ] || { echo "build_app.sh: required input missing: $f" >&2; exit 1; }
done

APP="$OUT_DIR/$APP_NAME.app"
CONTENTS="$APP/Contents"

if [ "$CLEAN" -eq 1 ] && [ -d "$APP" ]; then
    rm -rf "$APP"
fi

echo "==> assembling $APP"
mkdir -p "$CONTENTS/MacOS" "$CONTENTS/Resources/app" "$CONTENTS/Frameworks"

# --- bundle identity (from W5-1) ------------------------------------------
cp "$INFO_PLIST" "$CONTENTS/Info.plist"
# Inject the F-5 single source of truth (repo-root VERSION) into the bundle's
# CFBundleShortVersionString so it never drifts from VERSION. Sparkle's
# generate_appcast reads THIS value for the appcast's shortVersionString — a
# stale plist would publish an update under the wrong version number and users
# would never be offered it. macOS-only (plutil); on non-macOS the static plist
# value stands (real release builds run on macOS).
APP_VERSION="$(tr -d '[:space:]' < "$REPO/VERSION" 2>/dev/null || true)"
if [ -n "$APP_VERSION" ] && command -v plutil >/dev/null 2>&1; then
    plutil -replace CFBundleShortVersionString -string "$APP_VERSION" "$CONTENTS/Info.plist"
    echo "==> bundle CFBundleShortVersionString = $APP_VERSION (from VERSION)"
fi
printf 'APPL????' > "$CONTENTS/PkgInfo"

# --- launcher + entry point -----------------------------------------------
cp "$LAUNCHER_SRC" "$CONTENTS/MacOS/codec"          # CFBundleExecutable = codec
chmod +x "$CONTENTS/MacOS/codec"
cp "$ENTRY_SRC" "$CONTENTS/Resources/codec_app_main.py"

# --- CODEC Python application surface --------------------------------------
# Curated source set copied into Resources/app/. (W5-4 refines exactly what
# ships alongside the bundled interpreter.)
echo "==> copying CODEC sources"
# top-level Python modules
find "$REPO" -maxdepth 1 -name '*.py' -exec cp {} "$CONTENTS/Resources/app/" \;
# package directories that exist
for d in routes skills; do
    [ -d "$REPO/$d" ] && cp -R "$REPO/$d" "$CONTENTS/Resources/app/$d"
done
# runtime assets the app needs
for f in requirements.txt ecosystem.config.js codec_dashboard.html VERSION; do
    [ -f "$REPO/$f" ] && cp "$REPO/$f" "$CONTENTS/Resources/app/$f"
done
# any other top-level dashboard/PWA html
find "$REPO" -maxdepth 1 -name '*.html' -exec cp {} "$CONTENTS/Resources/app/" \; 2>/dev/null || true

# --- optional validation ---------------------------------------------------
if [ "$VALIDATE" -eq 1 ]; then
    if command -v plutil >/dev/null 2>&1; then
        plutil -lint "$CONTENTS/Info.plist"
    else
        echo "build_app.sh: --validate requested but plutil not available (non-macOS); skipping" >&2
    fi
fi

# --- bundle the relocatable Python runtime (W5-4) --------------------------
if [ "$WITH_PYTHON" -eq 1 ]; then
    echo "==> bundling Python runtime (W5-4)"
    BP_ARGS=(--app "$APP")
    [ -n "$PY_ARCH" ] && BP_ARGS+=(--arch "$PY_ARCH")
    bash "$PKG_DIR/bundle_python.sh" "${BP_ARGS[@]}"
fi

echo "==> done: $APP"
if [ "$WITH_PYTHON" -eq 1 ]; then
    echo "    NOTE: Python bundled. codesign=W5-7, notarize=W5-8."
else
    echo "    NOTE: unsigned skeleton, no Python (use --with-python for W5-4). codesign=W5-7, notarize=W5-8."
fi
