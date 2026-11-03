# Buyer app contents: Cookbook, Touch ID helper, no stdio MCP service (P-012)

Owner approval: "go go build i trust ur judgment here" (3 Nov 2026) for the audit plan items.
Status: built and tested on an unsigned build; the signed, notarised app needs the owner's unlocked screen.

## Finding
`packaging/macos/build_app.sh` copies the top-level `*.py` files and the `routes/`, `skills/` and `static/`
folders into `Resources/app/`, and nothing else from the repo. Three gaps followed:
1. **No `codec_cookbook/`.** `codec_compare` and the six `skills/cookbook_*.py` skills import it, so in the
   buyer app Compare and every Cookbook skill fail with `ModuleNotFoundError`.
2. **No Touch ID helper.** `routes/_shared.AUTH_BINARY` is `Resources/app/codec_auth/codec_auth`; it is
   missing, so `GET /api/auth/check` always answers `touchid_available: false`.
3. **`codec-mcp` runs as a background service.** It is the stdio MCP server: the MCP client that talks to it
   starts it. As a LaunchAgent it reads end of file on stdin at once, exits, and launchd starts it again,
   for ever (BO-3). `codec-mcp-http` (the claude.ai connector) is a real server and must keep shipping.

## Decision on the helper (Q63): compile it in the build, never ship the checked-in binary
The repo holds a prebuilt `codec_auth/codec_auth` (used by the Studio's own checkout). It is not shipped:
its `minos` is 26.0 (it would not start on the macOS 14 floor, D-34) and nothing ties it to the source
(GP09-1). Building on the buyer's Mac is not possible either: a buyer has no `swiftc`, and a file written
into the app after signing breaks its seal.

So `build_app.sh` compiles `codec_auth/main.swift` the same way it compiles the launcher:
`swiftc -O -target <arch>-apple-macos<floor> -framework LocalAuthentication -framework Foundation`, into
`Resources/app/codec_auth/codec_auth`, and fails the build if the result is not Mach-O. The pin is the
source: the binary always comes from the `main.swift` of the commit being released, `macho_minos.py`
checks its floor, and `sign_app.sh` signs it inside-out with hardened runtime before the outer seal (it
already signs every executable Mach-O under `Contents/`), so notarisation covers it. No code path changes.

## Change
- `build_app.sh`:
  - copies `codec_cookbook/` (then removes any `__pycache__`);
  - compiles the helper as above;
  - adds `codec-mcp` to the `DEV_ONLY` set, next to `codec-watchdog`.
  `ecosystem.config.js` and the Studio's PM2 are not touched.
- `packaging/macos/verify/app_contents.py` (new, stdlib only) checks a built app and prints
  `APP-CONTENTS-PASS`:
  - the Cookbook package is there;
  - the bundle's own Python, run with `-B` and `PYTHONDONTWRITEBYTECODE=1` from a temp folder, imports
    `codec_cookbook` and its modules and `codec_compare`, and loads `skills/cookbook_scan.py`;
  - the helper is an executable Mach-O; with `--touchid` it runs `--check` and must print the JSON;
  - `services.json` has no `codec-mcp` or `codec-watchdog` and still has `codec-mcp-http`;
  - with `--signed`, `codesign --verify --strict` passes on the helper and, after the import probe,
    `codesign --verify --deep --strict` still passes on the whole app.
- `release_macos.sh` runs that check with `--signed` right after signing, before notarisation, so a release
  missing any of this stops there.
- `optional_imports.json`: the `mlx_lm` reason no longer says the Cookbook is left out of the app.
- ax_bridge is not part of this: `skills/ax_control.py` reads it from a fixed repo path, so shipping the
  binary alone would change nothing.

## Tests
- `tests/test_app_contents.py`: the build script's copy and compile lines; the checker against small fake
  apps (passes on a good one, fails when the Cookbook, the helper, or the `services.json` rule is wrong);
  a macOS build into a temp folder that the checker passes, with the helper at the floor's `minos`.
- `tests/test_fleet_start.py`: the build's own node dump leaves out `codec-mcp` and keeps `codec-mcp-http`.
- `tests/test_launchd.py`: LaunchAgents generated from that dump have no `codec-mcp` label.
- `tests/test_release.py`: the release plan runs the contents check between signing and notarisation.

## Upgrade note
An app installed before this change keeps `~/Library/LaunchAgents/ai.avadigital.codec.codec-mcp.plist`:
the installer loads every `ai.avadigital.codec.*` plist and removes none. No buyer has installed yet; on
the Mac Air test install, run `uninstall_codec.sh` before installing the new build.

## Rollback
Revert the PR and rebuild: the app goes back to shipping without the Cookbook and the helper, with
`codec-mcp` as a service. No buyer install exists, so nothing in the field changes.
