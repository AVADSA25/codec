# W5-2 — macOS `.app` bundle wrapper + Python launcher (design)

> Wave 5 (Audit E). Closes **E-1** (no `.app`/bundle) for the *skeleton* milestone
> and consumes the **E-17** entitlements + Info.plist that landed in W5-1.
> Reference: `docs/audits/PHASE-1-APPLE-APP.md`, decisions in `docs/APPLE-DISTRIBUTION.md`.

## What this delivers

A reproducible **build script** that assembles an (unsigned) `.app` bundle from the
repo + the W5-1 metadata, plus the **launcher** that the OS executes when the user
opens the app:

```
dist/Sovereign AI Workstation.app/
  Contents/
    Info.plist                 # copied from packaging/macos/Info.plist (W5-1)
    PkgInfo                     # "APPL????"
    MacOS/
      codec                    # CFBundleExecutable — the launcher (shell)
    Resources/
      app/                     # the CODEC Python sources + skills + routes
      codec_app_main.py        # the Python entry point the launcher execs
      codec.icns               # placeholder (real icon → handoff)
    Frameworks/                # EMPTY in W5-2 — Python.framework lands in W5-4
    _CodeSignature/            # created at signing time (W5-7), not now
```

`CFBundleExecutable` is `codec` (per W5-1 Info.plist), `CFBundleIdentifier`
`ai.avadigital.codec`, display name "Sovereign AI Workstation".

## Scope boundaries (what W5-2 does *not* do)

| Concern | Status | Owner |
|---|---|---|
| Bundle Python.framework | **stub** — launcher falls back to a discovered `python3` with a logged warning; `Frameworks/` is empty | W5-4 |
| launchd fleet orchestration | **stub** — entry point is fleet-aware but does **not** start the 16 services (today they run under PM2) | W5-3 |
| Code signing / hardened runtime | **not done** — bundle is unsigned; `codesign`/entitlements applied later | W5-7 |
| Notarization / staple | not done | W5-8 |
| Real `.icns` icon, DMG | not done (placeholder icon) | W5-11/installer |

This is the *foundational wrapper*. Everything downstream (sign → notarize →
staple → DMG) operates on the bundle this script produces.

## Launcher design (`Contents/MacOS/codec`)

A POSIX shell launcher (exec bit set). On `open`:
1. Resolve `APP=<.app>/Contents` from `$0`.
2. Pick the interpreter: prefer the bundled
   `$APP/Frameworks/Python.framework/Versions/Current/bin/python3` (W5-4); else
   fall back to `python3` on `PATH` and log a warning to
   `~/Library/Logs/CODEC/launch.log`.
3. `exec "$PY" "$APP/Resources/codec_app_main.py" "$@"`.

**Hardened-runtime note:** a shell-script `CFBundleExecutable` is fine for the
unsigned skeleton + `open`. W5-7 will likely replace it with the bundled
`python3` Mach-O as the executable (or a tiny compiled stub) so `codesign
--options runtime` has a Mach-O main image to sign. Documented, not done here.

## Entry point (`codec_app_main.py`)

Thin, dependency-light bootstrap (stdlib only, so it runs even before the venv is
wired):
- Ensures `~/.codec/` and `~/Library/Logs/CODEC/` exist; writes a launch line.
- Detects whether it's running from inside an `.app` (bundle paths) vs the repo.
- `--selftest`: validate environment (paths resolve, Info.plist found, Python
  version ≥ 3.11) and exit 0 **without starting anything** — this is what CI/tests
  and a developer can run safely on any machine.
- Normal launch (W5-2): logs "fleet start deferred to W5-3 (launchd)" and exits 0.
  It deliberately does **not** touch the user's running PM2 fleet.

## Build script (`packaging/macos/build_app.sh`)

- Pure `mkdir`/`cp` assembly — **no macOS-only tools required** to assemble
  (so it runs in CI too); `plutil` validation is opt-in behind `--validate`
  (macOS only).
- `--out DIR` (default `dist/`), `--app-name`, `--clean`.
- Copies the curated source set (the `codec_*.py`, `routes/`, `skills/`,
  `whisper_server.py`, `requirements.txt`, etc.) into `Resources/app/`.
- Idempotent; writes nothing outside the output dir.
- Exits non-zero with a clear message on any missing input.

## Test plan (`tests/test_app_bundle.py`)

Portable (stdlib + pathlib), runs on ubuntu CI **and** macOS:
- `build_app.sh` and the launcher exist and are executable; launcher has a
  shebang and references `codec_app_main.py`.
- `codec_app_main.py` exposes `--selftest` and is stdlib-only (no `import codec*`).
- Build script references the W5-1 Info.plist and the right `CFBundleExecutable`.
- **darwin-only** smoke (skipped on Linux): run `build_app.sh --out <tmp>`, assert
  the bundle tree exists, `Info.plist` copied, launcher present+executable, then
  run `codec_app_main.py --selftest` and assert exit 0.

Added to the CI doc-guard list so the packaging can't silently regress.

## Rollback

Net-new files under `packaging/macos/` + `dist/` (gitignored) + one test + an
audit closed-note. No runtime/daemon code touched; `git revert` the commit.
