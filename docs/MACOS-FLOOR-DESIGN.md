# macOS 14 floor for the buyer app (D-34, P-055)

Owner decision 29 Oct 2026: confirmed. Status: built; the clean macOS 14 run is pending (no macOS 14
machine here, the Mac Air's version is not known).

## Finding (checked on the installed v3.6.0 app, 30 Oct 2026)
`Info.plist` said macOS 13 and the README said Ventura, but four files in the app need a newer macOS:
`mlx/core.cpython-312-darwin.so`, `mlx/lib/libmlx.dylib`, `mlx/lib/libjaccl.dylib` (all minos 26.2) and the
launcher `Contents/MacOS/codec` (minos 26.0). Two causes, both fixed here:
1. `bundle_python.sh` ran plain `pip install`; on the macOS 26 build Mac pip chose `macosx_26_0` wheels.
   PyPI's mlx has wheels for `macosx_14_0`, `15_0` and `26_0` only, so the floor for MLX is 14.
2. `build_app.sh` had `swiftc -target ...macos13.0` behind `[ -n "$ARCH" ]`, but `ARCH` is never set in
   that script (the variable is `PY_ARCH`), so the launcher was built for the build Mac's own OS.

## Change
- **Floor = 14.0**, written once in `packaging/macos/Info.plist` (`LSMinimumSystemVersion`). Everything
  else reads it or is pinned equal to it by `tests/test_macos_floor.py`: README requirements line,
  `docs/APPLE-DISTRIBUTION.md`, the lock header ("macOS 14+").
- **Wheels:** `bundle_python.sh` installs with `--platform macosx_<floor>_0_<arm64|x86_64> --target
  <site-packages>` (still `--require-hashes --only-binary=:all: --no-deps`). pip accepts every wheel up to
  the floor, so a macOS 26 build Mac gets the same files as a macOS 14 one.
- **Launcher:** `build_app.sh` always passes `-target <arch>-apple-macos<floor>` (arch from `--arch` or
  this Mac).
- **Check:** `packaging/macos/verify/macho_minos.py --app <app>` reads the version out of every Mach-O
  header (thin or universal, `LC_BUILD_VERSION` and the old `LC_VERSION_MIN_MACOSX`) and exits 1 if any
  needs more than the floor; it prints the offenders. `build_app.sh --with-python` runs it right after the
  bundle step, so a bundle that needs a newer macOS never reaches signing. CI's macos-14 job runs it on the
  installed lock too. Cross-checked against `otool -l` (libmlx.dylib: minos 26.2 both ways).
- Not edited: `scripts/start_model_server.sh`, `llm_model`.

## Not done / pending
- **Clean macOS 14 run of the packaging e2e gate (`verify/e2e.mjs`):** needs a macOS 14 machine. A GitHub
  macos-14 runner has no Metal GPU, so a GPU failure there is inconclusive, not a floor result. Written
  down as pending until the Mac Air (or any macOS 14 Mac) runs it.
- **If the macOS 14 run fails for a floor reason,** the floor in the three places becomes the lowest macOS
  that passed (26.2 if nothing lower works), recorded in DECISIONS.md.
- **Rebuild and notarise** need the owner at an unlocked screen.

## Rollback
Revert the PR: the floor goes back to 13.0 in the plist (wrong, but as before) and pip chooses wheels for
the build Mac.
