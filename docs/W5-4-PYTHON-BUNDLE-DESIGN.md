# W5-4 — bundle a self-contained Python runtime (design)

> Wave 5 (Audit E). Closes **E-6** (the `.app` must not depend on system /
> pyenv / Homebrew Python). Builds on W5-2 (the bundle) + W5-3 (launchd).

## Decision refinement (flagged for Mickael)

`docs/APPLE-DISTRIBUTION.md` locked **"bundle Python.framework."** Implementing
it revealed the literal mechanism is the wrong one:

- **python.org's `Python.framework`** bakes absolute install paths
  (`/Library/Frameworks/...`), is not designed for relocation inside another
  app, and is painful to re-sign + notarize. Apps that try it fight dylib-path
  fixups for every release.
- **`python-build-standalone`** (the `astral-sh` project) ships a **fully
  relocatable** CPython `install_only` build with portable `@rpath`/loader
  paths. It is what **uv, Rye, and Briefcase** use to embed Python in shippable
  apps. Same *intent* (ship our own Python; never touch system Python), correct
  *mechanism*.

**This PR uses python-build-standalone.** The decision's intent is honored; the
source is abstracted behind `packaging/macos/python-runtime.json`, so swapping
back to a python.org framework (or bumping versions) is a one-file change.
**→ Mickael: confirm you're OK with python-build-standalone (recommended).**

## What ships

- **`packaging/macos/python-runtime.json`** — pinned manifest: CPython
  **3.12.13**, pbs release **20260510**, per-arch **sha256**, and a URL template.
  The single source of truth for *which* Python; bump here.
- **`packaging/macos/bundle_python.sh`** — downloads the arch-correct tarball,
  **verifies sha256** (refuses on mismatch — no executing an unverified 30 MB
  binary), extracts it to `<App>.app/Contents/Frameworks/python/`, then
  `pip install -r requirements.txt` into that interpreter. Flags: `--app`,
  `--arch`, `--requirements`, `--skip-pip`, `--dry-run`.
- **`build_app.sh --with-python`** — assembles the bundle (W5-2) then invokes
  `bundle_python.sh` on it.
- **launcher update** — `Contents/MacOS/codec` now prefers
  `Contents/Frameworks/python/bin/python3` (python-build-standalone's layout),
  falling back to system `python3` for the skeleton/dev path.

## Layout in the bundle

```
Contents/Frameworks/python/
  bin/python3 -> python3.12          # the relocatable interpreter
  lib/python3.12/...                 # stdlib + our site-packages (after pip)
```

`@rpath` is already portable in the install_only build, so most native
extensions resolve without `install_name_tool` surgery. Any that don't (rare;
typically self-built wheels) are fixed at sign time — tracked for W5-7.

## Security

- **sha256-pinned** download (supply-chain: we never run an unverified binary —
  consistent with the repo's audit-HMAC / allowlist posture).
- The bundled pip install uses the project `requirements.txt`; a future
  lock-file (F-15) would pin transitive deps too.

## Test plan (`tests/test_python_bundle.py`, hermetic — no network in CI)

- Manifest is valid JSON: `python_version`, `pbs_release`, `url_template`, and
  64-hex sha256 for both `aarch64` + `x86_64`.
- `bundle_python.sh` exists, is executable, verifies sha256 (`shasum`), targets
  `Contents/Frameworks/python`, and supports `--dry-run`.
- `--dry-run` prints the arch-correct URL + sha (no download) — asserted.
- `build_app.sh` exposes `--with-python`; launcher prefers the pbs python path.

**Real download is NOT in the test suite** (CI must stay fast/offline). It was
validated once by hand on macOS arm64 — see the PR description for the captured
`--version` + import + pip evidence. Full `pip install -r requirements.txt`
(numpy 2.x, soundfile, sounddevice native wheels) + the signed launch is
validated on Mickael's build Mac (handoff).

## Scope boundaries

- Not auto-run by the app; `bundle_python.sh` is a build step. First-run is W5-6.
- Code signing the bundled dylibs/interpreter = W5-7; notarization = W5-8.
- Model packs are separate (W5-5), not part of the Python bundle.

## Rollback

Net-new under `packaging/macos/` + a launcher path tweak + one test + CI line +
audit/handoff notes. No runtime/daemon code; `git revert` the commit.
