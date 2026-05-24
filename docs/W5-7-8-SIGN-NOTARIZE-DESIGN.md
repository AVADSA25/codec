# W5-7 / W5-8 — code signing + notarization pipeline (design)

> Wave 5 (Audit E). Closes **E-2** (code signing) + **E-3** (notarization).
> Depends on W5-1 (entitlements), W5-2 (bundle), W5-4 (embedded Python).
> Reference: `docs/audits/PHASE-1-APPLE-APP.md`.

## Why these are author-now / validate-on-your-Mac

Signing needs a **Developer ID Application** identity; notarization needs an
**App Store Connect API key**. Neither lives in this repo or CI (correctly — they
must not). So this PR ships the *scripts* + validates their **logic** via
`--dry-run` (enumeration + command plan). Mickael runs them once on the build
Mac with the cert. This split is the standard way to land a signing pipeline.

## sign_app.sh (E-2)

Hardened-runtime signing of the whole bundle, **inside-out** (nested code first,
the `.app` last — codesign requires inner code be valid before the outer seal):

1. Enumerate inner Mach-O: every `*.dylib` / `*.so` under `Contents/` (the
   embedded Python's hundreds of extension modules + numpy/mlx/PyObjC dylibs),
   plus Mach-O executables (`Contents/Frameworks/python/bin/python3*`, the
   launcher `Contents/MacOS/codec`, any bundled `cloudflared`, the Touch ID
   helper). Sign each:
   `codesign --force --options runtime --timestamp --entitlements <ent> --sign <id>`
2. Sign nested `.framework`/`.app` if present.
3. Sign the **main `.app` last** with the same flags + entitlements
   (`packaging/macos/codec.entitlements` from W5-1).
4. Verify: `codesign --verify --deep --strict --verbose=2` (note: `spctl
   --assess` only passes *after* notarization + staple — W5-8).

Flags: `--app`, `--identity` (or `$CODEC_SIGN_IDENTITY`), `--entitlements`,
`--dry-run`, `--verify-only`.

## notarize_app.sh (E-3)

1. Package: `ditto -c -k --keepParent "<app>" "<zip>"` (notarytool takes a zip).
2. Submit + wait: `xcrun notarytool submit <zip> --keychain-profile <profile>
   --wait` (or `--apple-id/--team-id/--password`).
3. On accepted: `xcrun stapler staple "<app>"` then `stapler validate` +
   `spctl --assess --type execute -vv` (now passes).

Flags: `--app`, `--keychain-profile` (or Apple-ID trio), `--dry-run`,
`--staple-only`.

**Triage (documented in the script):** Apple most often rejects an embedded
`.dylib` that's unsigned or lacks hardened runtime — almost always from
`mlx`/`numpy`/`PyObjC` wheels. Fix: `xcrun notarytool log <submission-id>` to get
the offending path, then re-run `sign_app.sh` (it signs *all* `.dylib`), resubmit.

## Release sequence

```
build_app.sh --with-python --clean        # W5-2 + W5-4
sign_app.sh   --app "dist/….app" --identity "Developer ID Application: … (TEAMID)"
notarize_app.sh --app "dist/….app" --keychain-profile codec-notary
# → DMG packaging is the installer step (later)
```

## Test plan (`tests/test_signing.py`)

- Both scripts exist, executable, shebang.
- `sign_app.sh`: references `codesign`, `--options runtime`, `--timestamp`,
  `--entitlements`, signs inside-out, has `--dry-run` + a verify step.
- `notarize_app.sh`: references `notarytool`, `stapler`, `ditto`, `--dry-run`.
- **dry-run enumeration** (portable, `find`-based): build a skeleton `.app` +
  inject a fake nested `*.dylib`; `sign_app.sh --dry-run` lists the nested
  dylib **before** the `.app` (inside-out) and never invokes `codesign`.

Wired into the CI packaging step. Real signing/notarization = Mickael's Mac.

## Rollback

Net-new scripts under `packaging/macos/` + one test + CI line + audit/handoff
notes. No runtime/daemon code; `git revert`.
