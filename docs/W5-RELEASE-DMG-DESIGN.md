# W5 capstone — release orchestrator + DMG installer (design)

> Wave 5 (Audit E). Ties the whole chain into one command and adds the missing
> **`.dmg` build target** (E-3 notes "No `.dmg` or `.pkg` build target exists").
> Builds on W5-2 (build), W5-4 (python), W5-7 (sign), W5-8 (notarize).

## What ships

- **`packaging/macos/make_dmg.sh`** — package a (signed, notarized, stapled)
  `.app` into a drag-to-install `.dmg`: stage the app + a symlink to
  `/Applications`, then `hdiutil create -format UDZO`. Flags: `--app`, `--out`,
  `--volname`, `--dry-run`.
- **`packaging/macos/release_macos.sh`** — the one-command pipeline:
  ```
  build_app.sh --with-python --clean
  sign_app.sh   --app <app> --identity <id>
  notarize_app.sh --app <app> <creds>        # unless --skip-notarize
  make_dmg.sh   --app <app> --out <dmg>       # unless --skip-dmg
  ```
  Flags: `--identity`, `--keychain-profile` (or `--apple-id/--team-id/--password`),
  `--version` (DMG label), `--out`, `--app-name`, `--arch`, `--skip-notarize`,
  `--skip-dmg`, `--dry-run` (propagated to every sub-step).

## Notarization + DMG order

The `.app` is signed → notarized → **stapled** first, so the app inside the DMG
already carries its ticket (launches offline). The DMG wraps the stapled app.
(Notarizing/stapling the DMG itself is an optional extra step, noted for later;
stapling the app is sufficient for Gatekeeper.)

## Test plan (`tests/test_release.py`)

- Both scripts exist, executable, shebang.
- `make_dmg.sh`: references `hdiutil`, stages an `/Applications` symlink, has
  `--dry-run`. **darwin smoke:** build a tiny fixture dir, `make_dmg.sh` it,
  assert a non-empty `.dmg` is produced (hdiutil on a few KB is ~1 s).
- `release_macos.sh`: `--dry-run` names all four stages in order and **honors
  `--skip-notarize` / `--skip-dmg`**; references each sub-script.

Wired into the CI packaging step (dry-run + contract are portable; the hdiutil
smoke is darwin-gated).

## Scope

- Fancy DMG (background image, window layout) is cosmetic polish — later.
- `.pkg` (for MDM/enterprise push) is a separate future target.
- Real signing/notarization still needs the cert/keys (handoff) — the
  orchestrator just sequences the existing scripts.

## Rollback

Net-new `packaging/macos/{make_dmg.sh,release_macos.sh}` + one test + CI line +
audit/handoff notes. No runtime/daemon code; `git revert`.
