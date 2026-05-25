#!/bin/bash
# Bundle a self-contained, sha256-pinned relocatable Python into the .app (W5-4, E-6).
#
# Source: python-build-standalone (astral-sh) 'install_only' build — relocatable
# and signable, unlike python.org's Python.framework. Which Python is pinned in
# packaging/macos/python-runtime.json (single source of truth).
#
# Steps: resolve arch -> download the pinned tarball -> VERIFY sha256 (refuse on
# mismatch) -> extract to <App>/Contents/Resources/python -> pip install the
# project requirements into it.
#
# Usage:
#   bundle_python.sh --app "dist/Sovereign AI Workstation.app" [--arch aarch64|x86_64]
#                    [--requirements requirements.txt] [--skip-pip] [--dry-run]
# See docs/W5-4-PYTHON-BUNDLE-DESIGN.md.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
MANIFEST="$HERE/python-runtime.json"

APP=""
ARCH=""
REQS="$REPO/requirements.txt"
SKIP_PIP=0
DRY_RUN=0

while [ $# -gt 0 ]; do
    case "$1" in
        --app) APP="$2"; shift 2 ;;
        --arch) ARCH="$2"; shift 2 ;;
        --requirements) REQS="$2"; shift 2 ;;
        --manifest) MANIFEST="$2"; shift 2 ;;
        --skip-pip) SKIP_PIP=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "bundle_python.sh: unknown arg: $1" >&2; exit 2 ;;
    esac
done

[ -f "$MANIFEST" ] || { echo "bundle_python.sh: manifest not found: $MANIFEST" >&2; exit 1; }

# Detect arch if not given (uname -m: arm64 -> aarch64).
if [ -z "$ARCH" ]; then
    case "$(uname -m)" in
        arm64|aarch64) ARCH="aarch64" ;;
        x86_64) ARCH="x86_64" ;;
        *) echo "bundle_python.sh: unsupported arch $(uname -m)" >&2; exit 1 ;;
    esac
fi

# Resolve version / release / url / sha from the manifest (stdlib json via python3).
read_url_sha() {
    python3 - "$MANIFEST" "$ARCH" <<'PY'
import json, sys
m = json.load(open(sys.argv[1])); arch = sys.argv[2]
url = m["url_template"].format(release=m["pbs_release"], python_version=m["python_version"], arch=arch)
print(url)
print(m["assets"][arch]["sha256"])
print(m["python_version"])
PY
}
{ read -r URL; read -r SHA; read -r PYVER; } < <(read_url_sha)

# Resources/, NOT Frameworks/. codesign treats every Frameworks/ entry as a
# nested *bundle* and chokes on a bare interpreter tree ("bundle format
# unrecognized … In subcomponent: …/lib/python3.x"), leaving the outer app
# unsigned → Gatekeeper rejects. Resources/ is sealed as data; nested Mach-O
# are still signed inside-out by sign_app.sh. (Same layout py2app/briefcase use.)
DEST="$APP/Contents/Resources/python"

if [ "$DRY_RUN" -eq 1 ]; then
    echo "[dry-run] Python ${PYVER} (${ARCH})"
    echo "[dry-run] download: ${URL}"
    echo "[dry-run] sha256:   ${SHA}"
    echo "[dry-run] install into: ${DEST}"
    echo "[dry-run] then: ${DEST}/bin/python3 -m pip install -r ${REQS}"
    exit 0
fi

[ -n "$APP" ] || { echo "bundle_python.sh: --app is required (path to the .app)" >&2; exit 2; }
[ -d "$APP/Contents" ] || { echo "bundle_python.sh: not an .app bundle: $APP (run build_app.sh first)" >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
TARBALL="$TMP/python.tar.gz"

echo "==> downloading Python ${PYVER} (${ARCH}) from python-build-standalone"
curl -fL --retry 3 --max-time 300 -o "$TARBALL" "$URL"

echo "==> verifying sha256"
ACTUAL="$(shasum -a 256 "$TARBALL" | awk '{print $1}')"
if [ "$ACTUAL" != "$SHA" ]; then
    echo "FATAL: sha256 mismatch for $URL" >&2
    echo "  expected $SHA" >&2
    echo "  actual   $ACTUAL" >&2
    exit 1
fi
echo "    ok ($ACTUAL)"

echo "==> extracting into $DEST"
tar -xzf "$TARBALL" -C "$TMP"          # yields $TMP/python/
[ -x "$TMP/python/bin/python3" ] || { echo "FATAL: unexpected tarball layout (no python/bin/python3)" >&2; exit 1; }
rm -rf "$DEST"
mkdir -p "$(dirname "$DEST")"
mv "$TMP/python" "$DEST"

BUNDLED_PY="$DEST/bin/python3"
echo "==> bundled interpreter: $("$BUNDLED_PY" --version 2>&1)"

if [ "$SKIP_PIP" -eq 0 ]; then
    [ -f "$REQS" ] || { echo "FATAL: requirements not found: $REQS" >&2; exit 1; }
    echo "==> pip install -r $REQS into the bundle"
    "$BUNDLED_PY" -m pip install --upgrade pip >/dev/null
    "$BUNDLED_PY" -m pip install -r "$REQS"
fi

echo "==> done. Bundled Python at $DEST"
echo "    NOTE: code-signing the interpreter + dylibs is W5-7; notarization W5-8."
