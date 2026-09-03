# Gates: the packaged CODEC app must actually run on a stranger's Mac

OWNS: packaging/macos/**, requirements.txt, GATES.md

Scope: `Sovereign AI Workstation.app` installs and appears to succeed, then does
nothing. Five independent defects, each fatal on its own, each found 2026-09-03
while walking a real first-run. Fixing four and shipping is worthless — the app
is only usable when all five are closed.

## Dependencies

- [x] G1: Every module the app imports at startup is a DECLARED dependency and present in the bundled Python.
  CHECK: node packaging/macos/verify/deps.mjs
  EXPECT: UNLAZY-G1-PASS
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/mickaelfarina/codec-wt-app; path=2cbcf16e1311/44 entries; EXPECT=matched; output-sha256=dc6dc367ec60e5dddea47d34ea91736c4149d3449a1ccbf44002528e86284577; output-bytes=15
  WHY: `fastapi` appears only inside an "Optional: STT" COMMENT in
  requirements.txt, yet codec_dashboard.py imports it unconditionally. The build
  machine has it from the dev environment, so the bundle looked fine and the
  dashboard could never start on a clean Mac. Measured, not assumed: the check
  imports each module inside the BUNDLED interpreter.

## Identity

- [x] G2: The app bundle carries the CODEC icon and declares it.
  CHECK: node packaging/macos/verify/icon.mjs
  EXPECT: UNLAZY-G2-PASS
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/mickaelfarina/codec-wt-app; path=2cbcf16e1311/44 entries; EXPECT=matched; output-sha256=f84ca6efa6efa2657b3702f9a7391fa2b6fb2142e9c61a59e4a0476490dc4c7f; output-bytes=15
  WHY: build_app.sh has no icon step at all — Contents/Resources holds no .icns,
  so the Dock, Finder and Launchpad all show a blank generic tile.

## Entitlements

- [x] G3: The app's main executable is a Mach-O binary, so codesign entitlements actually apply, and the signed app carries the microphone entitlement.
  CHECK: node packaging/macos/verify/launcher.mjs
  EXPECT: UNLAZY-G3-PASS
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/mickaelfarina/codec-wt-app; path=2cbcf16e1311/44 entries; EXPECT=matched; output-sha256=b6c285e5093eb3a6ab27031621db097d098dc2446b7edfca7fdfa90119161439; output-bytes=15
  WHY: CFBundleExecutable is a /bin/sh script. macOS attaches entitlements only
  to Mach-O binaries, so `codesign --entitlements` is accepted and SILENTLY
  ignored — the app has hardened runtime with ZERO entitlements. A voice-first
  product that can never be granted a microphone. This is the load-bearing gate:
  no entitlements-file edit can fix it while the executable is a script.

## Service paths

- [x] G4: No installed LaunchAgent references a removable volume, and the generated paths follow the app's real location.
  CHECK: node packaging/macos/verify/launchagents.mjs
  EXPECT: UNLAZY-G4-PASS
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/mickaelfarina/codec-wt-app; path=2cbcf16e1311/44 entries; EXPECT=matched; output-sha256=2319a7eceda202fa75482ed4b87f422398aaf35e9cccfc43060ce91a64f014f8; output-bytes=15
  WHY: all 14 ai.avadigital.codec.* plists were written pointing at
  /Volumes/CODEC Installer 1/... because first_run.py ran while the app was
  still on the disk image. Every one exits 78 once the image is ejected, which
  every user does immediately.

## First-run feedback

- [x] G5: Launching the app produces a visible, non-silent result — the user is never left clicking an icon that appears to do nothing.
  CHECK: node packaging/macos/verify/feedback.mjs
  EXPECT: UNLAZY-G5-PASS
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/mickaelfarina/codec-wt-app; path=2cbcf16e1311/44 entries; EXPECT=matched; output-sha256=ff06227ef6ae0c5d20b638683282d214245eed49afda1c2d823c703163ce5142; output-bytes=15
  WHY: the launcher starts the fleet and exits with no window, no menu-bar item
  and no notification. Reported verbatim: "when I click it nothing happened It
  just nothing happened."

## End to end

- [x] G6: A fresh install from the built app starts a working dashboard — proven by an HTTP response from the bundled stack, not by a log line claiming success.
  CHECK: node packaging/macos/verify/e2e.mjs
  EXPECT: UNLAZY-G6-PASS
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/mickaelfarina/codec-wt-app; path=2cbcf16e1311/44 entries; EXPECT=matched; output-sha256=03379ff5eade424e30a2fe4da785b2a744fe7698dd409444d952921c1a72cd48; output-bytes=56
  WHY: every previous "success" in this saga was a log line. This gate must
  observe the product working, on a port the bundled app owns, and must not be
  satisfiable by the operator's existing PM2 fleet.

<!--
Negative controls: G1..G4 all assert properties absent in the CURRENT build, so
each verifier is run against the unfixed tree first and MUST fail there. G6 must
bind a port the PM2 fleet does not use, or it would pass on the operator's
existing dashboard and prove nothing about the bundle.

Toolchain: macOS only — codesign, spctl, launchctl, file(1). Shell: /bin/bash.

NOT in scope, deliberately: publishing a release asset, the Buy button, and the
ava-stack installer changes (those live in that repo's own ledger).
-->
