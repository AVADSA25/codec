# PHASE 1 AUDIT E — DISTRIBUTION + APPLE APP READINESS

**Date:** 2026-05-17
**Auditor:** general-purpose agent
**Scope:** What blocks paid Mac app from shipping?

## Summary
- Total findings: **18**
- Critical: **7**, High: **6**, Medium: **3**, Low: **2**
- Overall readiness: **~5%** (essentially zero Apple distribution work has been done; only a Touch-ID Swift helper and an unsigned native overlay exist)
- Recommended distribution path: **Developer ID Application + notarization (direct download), NOT Mac App Store**

The repo currently ships as a developer-targeted, MIT-licensed `git clone && ./install.sh` flow. Nothing in the tree is signed, notarized, sandboxed, or packaged as a `.app`. The work to turn CODEC into a notarized paid Mac app is substantial (XL-class effort) because the architecture is fundamentally incompatible with the Mac App Store sandbox and requires custom installer plumbing for ~20 PM2 daemons, an external Node toolchain, multi-gigabyte ML models, system permissions, and a separate Cloudflare tunnel binary.

---

## Methodology

Searched the repo for the standard distribution artifacts and got **zero hits**:

- `grep -ri "Info\.plist"` → none
- `grep -ri "codesign\|notarytool\|notarize\|altool"` → none
- `grep -ri "entitlements\|Sparkle\|app-specific-password"` → none
- `find -name "*.plist" -o -name "*.entitlements" -o -name "*.dmg" -o -name "*.pkg"` → none
- `find -name "*.app"` → none
- `grep -ri "com\.[a-z]*\.codec\|bundleIdentifier\|CFBundleIdentifier"` → none
- `grep -ri "Developer ID\|Team ID\|Gatekeeper\|hardened runtime\|notarization\|App Store\|Mac App Store"` → none
- `grep -ri "PrivacyInfo\|xcprivacy\|NSPrivacyAccessedAPI"` → none
- `grep -ri "NSMicrophoneUsageDescription\|NSAppleEventsUsageDescription\|NSScreenCaptureUsageDescription"` → none
- `grep "uninstall\|brew uninstall\|pm2 delete"` → none

Read:

- `README.md` (Quick Start describes `git clone && ./install.sh`)
- `install.sh` (Homebrew-dependent, asks user to grant Accessibility/Mic/Screen Recording in System Settings)
- `setup_codec.py` (9-step interactive wizard — assumes a terminal, asks for API keys, port choices, F-key conflicts)
- `requirements.txt` (8 required pip packages; comments list "optional" deps for TTS/STT/Google that the wizard actually requires)
- `ecosystem.config.js` (17 PM2 apps, many pointing at a hardcoded `/usr/local/bin/python3.13` — won't survive on a fresh user machine)
- `codec_slash_commands.py` (AVA license JWT decoding + `~/ava-stack/license-server/licenses.db` reference + `ava-license.lucyvpa.com` / `ava-proxy.lucyvpa.com` health probes)
- `codec_auth/main.swift` (LocalAuthentication helper — compiled at install time via `swiftc`, no code signing)
- `swift-overlay/Package.swift` (a SwiftPM executable target — Swift sources exist but no `.app` wrapper, no bundle ID)
- `request_mic.py` (instructs user to add `/usr/local/bin/python3.13` to System Settings → Privacy → Microphone)
- `update.sh` / `install.sh --update` (git-pull-based; no Sparkle, no version check, no signed update bundle)
- `codec_observer.py` (screencapture + Quartz + osascript usage — at least 146 osascript call sites repo-wide, 105 subprocess.run/Popen call sites)
- `CHANGELOG.md` (no entry references signing/notarization/distribution work)

The conclusion is unambiguous: there is no Apple-app distribution scaffolding in the repo. The OSS distribution model is "developer clones the repo, runs install.sh, grants permissions manually in System Settings." Shipping a paid Mac app is greenfield work.

---

## CODEC system access → Apple sandbox mapping

| CODEC need | macOS API used | Sandbox entitlement | Sandboxable? | Notes |
|---|---|---|---|---|
| Microphone (voice, dictate, wake word) | AVFoundation via `request_mic.py`, `sounddevice` | `com.apple.security.device.microphone` | Yes | Needs `NSMicrophoneUsageDescription` in Info.plist; works inside sandbox. |
| Screen recording for OCR (observer, voice "check my screen") | `screencapture -x` subprocess + Vision via osascript + Quartz `CGEventSourceSecondsSinceLastEventType` | none | **No** | Sandboxed apps cannot capture other apps' windows. Requires Screen Recording permission per `~/Library/Group Containers/group.com.apple.replayd/...` — but only outside the sandbox or with the deprecated `com.apple.security.screencapture-helper` private entitlement. **Hard incompatibility with Mac App Store.** |
| AppleScript control (iMessage send, Notes, Reminders, Spotify, Music, etc. — 146 osascript call sites) | NSAppleScript via `osascript` subprocess | `com.apple.security.automation.apple-events` (per-target) | Partially | Each target app needs an explicit `NSAppleEventsUsageDescription` in Info.plist AND the per-target descriptor (e.g. `com.apple.iChat`, `com.apple.Notes`). User has to approve each one in TCC. Mac App Store reviewers reject "blanket" app-events. **Direct distribution viable; App Store is not.** |
| Hotkey listener / keystroke injection (F13/F18, Cmd+R, F5 live typing, pyautogui paste) | CGEvent tap (`pynput`, `pyautogui`, `CGEventPost`) | none — requires Accessibility | **No** | Cannot be sandboxed. User must add the app to System Settings → Privacy → Accessibility. Same for the `*` and `+` screenshot hotkeys. |
| Full file access (`~/.codec/**`, project worktrees, agent workspaces, skill plugins, `~/codec-repo`) | filesystem (`pathlib`, `open`, `os.path`) | `com.apple.security.files.user-selected.read-write` for picker / Documents / Downloads | **No** for `~/.codec/**` | Sandbox forbids reading/writing arbitrary `$HOME` paths. CODEC writes to `~/.codec/memory.db`, `~/.codec/audit.log`, `~/.codec/skills/`, `~/.codec/plugins/`, `~/.codec/agents/<id>/workspace/`, `~/.codec/pilot_traces/`, `~/codec-repo/**`. None of these are eligible for the sandbox's container model. |
| Subprocess spawning (PM2, python3, mlx_lm.server, screencapture, sox, osascript, swiftc, cloudflared, ffmpeg, git) — 105+ subprocess call sites | posix_spawn / fork+exec | none | **No** | Sandboxed apps cannot fork/exec arbitrary binaries outside their bundle. PM2 alone forks 17+ Node-supervised Python processes. **Hard incompatibility.** |
| Network access (Cloudflare tunnel inbound, DuckDuckGo/Serper outbound, Telegram Bot API long-poll, Google APIs, MCP HTTP at :8091, dashboard at :8090, etc.) | sockets, requests, httpx | `com.apple.security.network.client` + `com.apple.security.network.server` | Yes | Both entitlements exist and work inside sandbox. Not a blocker on its own. |
| Reading user's iMessage DB (`~/Library/Messages/chat.db`) | sqlite3 over the live DB | none | **No** | The Messages DB is outside any sandbox container. Requires Full Disk Access — not grantable via sandbox entitlement. |
| Reading clipboard (text + image — observer, clipboard skill) | NSPasteboard, `pbpaste` subprocess | Works in sandbox | Yes | OK either way. |
| Idle detection (Quartz `CGEventSourceSecondsSinceLastEventType`) | Quartz | Works in sandbox | Yes | OK either way. |
| Touch ID auth (`codec_auth/codec_auth`) | LocalAuthentication framework | `com.apple.security.personal-information.touchid`-ish (not actually required) | Yes | Works inside sandbox. |
| Spawning Chromium via Playwright (Pilot — CDP 9223) | playwright launches `Chromium.app` | none | **No** | Sandbox blocks launching external app bundles with arbitrary args. |
| Cloudflare `cloudflared` binary lifecycle | subprocess + signal handling | none | **No** | External binary, must be outside sandbox. |
| AppKit overlay windows (Swift overlay app) | NSPanel | works in sandbox | Yes | The Swift `swift-overlay/` target is sandbox-friendly in principle. |

**Conclusion: hard incompatibility with the Mac App Store sandbox.** CODEC must ship as a **Developer ID signed + notarized direct download** (`.dmg` or `.pkg`). At least 5 core CODEC capabilities (screen recording, AppleScript blanket access, hotkey/Accessibility, full file access, arbitrary subprocess spawning) are non-sandboxable. Even partial App Store distribution (e.g. ship a "lite" sandboxed build) would require gutting Pilot, the observer's OCR, Dictate, the F-key listeners, and the entire PM2 fleet.

---

## Findings

### E-1 — No `.app` bundle, no Info.plist, no bundle identifier [CRITICAL]

> **🟡 SUBSTANTIALLY CLOSED — W5-1 (Info.plist) + W5-2/PR-5B (wrapper + launcher).** The `Info.plist` (bundle ID `ai.avadigital.codec`, all TCC usage strings) landed in W5-1. W5-2 adds `packaging/macos/build_app.sh` — a pure mkdir/cp assembler (no macOS-only tools, so it runs in CI) that produces `dist/Sovereign AI Workstation.app` from the repo + W5-1 metadata, with a shell launcher at `Contents/MacOS/codec` (CFBundleExecutable) execing a stdlib-only entry point (`codec_app_main.py`, safe `--selftest`, fleet-start deferred to W5-3). Verified end-to-end on macOS (`tests/test_app_bundle.py`, 5 tests incl. a darwin build smoke). **Remaining for full closure:** bundle Python.framework so the app is self-contained (W5-4); replace the shell launcher with a Mach-O main image for hardened-runtime signing (W5-7); icon + DMG (installer). Until then the bundle is an *unsigned skeleton*.

**What's missing:** The repo is a collection of `.py` and `.sh` files. There is no `.app` wrapper, no `Info.plist` declaring `CFBundleIdentifier`, `CFBundleVersion`, `CFBundleShortVersionString`, `LSMinimumSystemVersion`, no `NSMicrophoneUsageDescription`, no `NSAppleEventsUsageDescription`, no `NSScreenCaptureUsageDescription`, no `NSDesktopFolderUsageDescription`. Nothing the OS recognizes as a Mac app.
**Why it matters:** Without a bundle, Gatekeeper has nothing to scan, the user has no draggable artifact, and TCC has no identity to grant permissions to. Each new Python invocation re-prompts for Accessibility/Mic/Screen Recording. Currently the user grants permissions to **Terminal.app** (or whichever terminal launched `python3.13`), which is fragile, wrong, and ships as "add Terminal to Privacy" in `install.sh:204-209`.
**Effort:** **L** (build an Xcode app target wrapping a Python launcher, design the bundle ID — proposed `ai.avadigital.codec` or `com.opencodec.codec` — write Info.plist with all usage descriptions, decide whether to bundle Python or rely on system Python).
**Dependencies:** none (foundational).

### E-2 — No code signing pipeline [CRITICAL]
**What's missing:** Zero references to `codesign`, `--sign`, Developer ID Application certificates, or hardened runtime in the repo. Both the Touch ID helper (`codec_auth/codec_auth`, compiled inline by `setup_codec.py:563-571` via `swiftc`) and the Swift overlay (`swift-overlay/`) ship unsigned.
**Why it matters:** Unsigned binaries trip Gatekeeper on download; the user has to right-click → Open → Open or run `xattr -d com.apple.quarantine`. A paid app cannot ship like this. Hardened runtime (required for notarization) is also un-configured — affects subprocess injection (need `com.apple.security.cs.allow-jit`, `com.apple.security.cs.disable-library-validation`, or `com.apple.security.cs.allow-unsigned-executable-memory` to keep CPython + PyObjC working).
**Effort:** **L** (acquire Developer ID Application cert, set up keychain for CI, write a `scripts/sign_app.sh` that signs the bundle including embedded Python, all `.dylib`, the cloudflared binary, and the Touch ID helper; test hardened-runtime entitlements against the actual launch path).
**Dependencies:** E-1 (need a bundle to sign), E-13 (Apple Developer enrollment).

### E-3 — No notarization workflow [CRITICAL]
**What's missing:** Zero references to `notarytool`, `xcrun stapler`, `altool`, or any CI hook that uploads the bundle to Apple. No `.dmg` or `.pkg` build target exists.
**Why it matters:** As of macOS 10.15+, downloaded apps that aren't notarized refuse to launch on first run (Gatekeeper blocks them with the dreaded "cannot be opened because Apple cannot check it for malicious software" dialog). A paid app **must** be notarized. The notarization process scans all embedded binaries — Python interpreter, every `.dylib` from `mlx`, `numpy`, `pyobjc`, etc. — for signing/hardened-runtime compliance.
**Effort:** **M** (script using `notarytool submit` + `stapler staple`; configure App Store Connect API key; test with a stub bundle; document the failure-mode triage when Apple flags an unsigned embedded `.dylib` — common with `mlx`/`numpy`/`PyObjC` builds).
**Dependencies:** E-2.

### E-4 — Sandbox incompatibility blocks Mac App Store distribution [CRITICAL]

> **Closed by PR-5A (W5-1).** Recorded in `docs/APPLE-DISTRIBUTION.md` §2 — the forced decision (Developer ID signed+notarized direct download; App Store ruled out) with the full capability→sandbox table. No implementation; it's the foundational architecture record the rest of Wave 5 references.
**What's missing:** A decision/documentation acknowledging that CODEC cannot ship via the Mac App Store and the implications.
**Why it matters:** Five core capabilities (screen recording / OCR, blanket AppleScript control, hotkey/CGEvent tap, arbitrary subprocess spawning including PM2, full filesystem access to `~/.codec/**`) are non-sandboxable. See §"CODEC system access → Apple sandbox mapping" for the entitlement-by-entitlement breakdown. Trying to ship a sandboxed build would strip CODEC of Core, Dictate, Pilot, Observer, the PM2 fleet, and iMessage — i.e. ~80% of the value prop. App Store is out.
**Effort:** **S** (write the decision doc into `docs/`; no implementation).
**Dependencies:** none.

### E-5 — No PrivacyInfo.xcprivacy manifest [HIGH]

> **Closed by PR-5A (W5-1).** `packaging/macos/PrivacyInfo.xcprivacy` declares `NSPrivacyTracking=false`, empty tracking-domains + collected-data-types (local-first), and the two Required-Reasons APIs actually hit: `FileTimestamp` (C617.1 — observer/scheduler/heartbeat mtime) + `DiskSpace` (E174.1 — heartbeat/df). `plutil`-validated + key-asserted by `tests/test_apple_packaging.py`. (Re-audit the embedded C-extensions' API use once Python.framework is bundled in W5-4.)
**What's missing:** No `PrivacyInfo.xcprivacy` declaring the Required Reasons APIs CODEC uses: `NSPrivacyAccessedAPICategoryFileTimestamp` (mtime checks for observer recent_files, scheduler), `NSPrivacyAccessedAPICategoryDiskSpace` (heartbeat health check, install.sh's `df -g /`), `NSPrivacyAccessedAPICategoryUserDefaults` (likely zero — Python doesn't touch NSUserDefaults directly, but embedded PyObjC frameworks might), `NSPrivacyAccessedAPICategorySystemBootTime` (uptime — none observed), `NSPrivacyAccessedAPICategoryActiveKeyboard` (likely none — `pynput` uses CGEvent tap, not the keyboard layout API). No declaration of data collection types or third-party SDKs.
**Why it matters:** Required for any app submitted to the App Store *or* notarized through current `notarytool` versions (Apple has been tightening enforcement since 2024). Missing manifests trigger notarization warnings; in some cases, future Gatekeeper revisions can hard-block on missing manifests. Also affects the Reason codes the user sees in Privacy & Security settings.
**Effort:** **M** (audit which APIs each embedded Python C-extension actually calls — `numpy`, `mlx`, `PyObjC`, `pynput`, `sounddevice`, `pyautogui`, `Pillow`, `requests` — write the manifest; document reason strings; test against current notarytool).
**Dependencies:** E-1.

### E-6 — Hardcoded `/usr/local/bin/python3.13` makes the install non-portable [CRITICAL]

> **🟡 SUBSTANTIALLY CLOSED — W5-4/PR-5D (bundled relocatable Python).** `packaging/macos/bundle_python.sh` downloads a **sha256-pinned** `python-build-standalone` CPython **3.12.13** (`packaging/macos/python-runtime.json`), verifies it, extracts to `Contents/Frameworks/python/`, and `pip install`s the requirements into it; `build_app.sh --with-python` wires it in; the launcher prefers the bundled interpreter. **Validated end-to-end on macOS arm64** (download → sha256 match → run → native stdlib loads → pip works in-bundle). *Mechanism note:* python.org's `Python.framework` (the literal locked wording) isn't relocatable/signable for redistribution; python-build-standalone is (it's what uv/Rye/Briefcase use) — same intent, flagged for Mickael in `docs/HANDOFF-MICKAEL.md`. **Remaining:** full `pip install -r requirements.txt` with the native/ML wheels (numpy 2.x, soundfile, mlx) + the launchd interpreter remap pointing here + dylib `install_name_tool` fixups at sign time (W5-7) — validated on Mickael's build Mac. The hardcoded `/usr/local/bin/python3.13` in `ecosystem.config.js` stays for the OSS/PM2 path; the launchd generator remaps it to the bundled python for the app.

**What's missing:** A bundled Python runtime, or a path-resolution layer that finds Python on the user's machine.
**Why it matters:** `ecosystem.config.js` hardcodes `/usr/local/bin/python3.13` for 6 PM2 services (`codec-mcp-http`, `codec-dictate`, `codec-autopilot`, `codec-observer`, `pilot-runner`, `codec-agent-runner`). On Apple Silicon, Homebrew installs Python to `/opt/homebrew/bin/python3.13` — these services will fail to start. On a fresh Mac with no Homebrew, none of these exist. The OSS distribution implicitly assumes Mickael's dev machine layout. A paid app must either (a) bundle a Python.framework inside the `.app`, or (b) probe at install time and rewrite `ecosystem.config.js`. Option (a) adds ~80MB and makes signing/notarization more complex (every `.so` in `site-packages` must be signed); option (b) is fragile and requires the user to have already installed Homebrew + Python before the app launches.
**Effort:** **XL** (decision: bundle Python or runtime-probe. If bundle: standalone Python relocatable build per `python-build-standalone` or PyOxidizer, install all pip deps into bundled `site-packages`, sign every `.so`. If probe: write `ecosystem.config.js` templating into the installer, fail gracefully when Python is missing, surface a "Install Homebrew first" wall).
**Dependencies:** E-1.

### E-7 — Node.js + PM2 dependency is unbundled and explicit [CRITICAL]

> **🟡 SUBSTANTIALLY CLOSED — W5-3/PR-5C (launchd toolkit).** Decision (locked, `docs/APPLE-DISTRIBUTION.md`): the paid `.app` runs the fleet under **launchd LaunchAgents**, dropping the Node/PM2 dependency. `packaging/macos/launchd/generate_launchagents.py` derives one `ai.avadigital.codec.<svc>.plist` per service straight from `ecosystem.config.js` (single source of truth, dumped via `node -e` at build time) using stdlib `plistlib`+`shlex` — verified to emit all 16 services with correct ProgramArguments tokenisation (incl. `bash -c '…'`), env, KeepAlive, RunAtLoad, interpreter remap, and `~`-expanded log paths. `install_launchagents.sh` (opt-in, `--dry-run`, **refuses if the PM2 fleet is live**) + `uninstall_launchagents.sh` manage the agents. 8 tests (`tests/test_launchd.py`). **Remaining for full closure:** the interpreter remap points at the bundled `Python.framework` python (W5-4); first-run auto-install of the agents is wired in the permissions wizard (W5-6); `launchctl` behaviour validated on a real Mac (handoff). The OSS/dev build keeps PM2.

**What's missing:** A bundled Node runtime + PM2, OR a replacement supervisor that doesn't depend on Node.
**Why it matters:** `install.sh:120-136` shells out to Homebrew to install Node + `npm install -g pm2`. A non-technical paid-app user does not have Homebrew. Bundling Node adds another ~75MB and another tree of binaries to sign. Alternatives: (a) replace PM2 with `launchd` plists (macOS-native, no extra runtime, lifecycle is a `LaunchAgent` per service); (b) replace PM2 with a single Python supervisor (`supervisord` or a custom one); (c) bundle Node into the `.app`. Option (a) is the cleanest fit for Apple distribution but requires rewriting all PM2 ecosystem entries as 17 plist files and refactoring `sync_to_pm2.sh`, `install.sh:192-194`, every `pm2 restart` in skills, every `pm2 logs` documentation reference.
**Effort:** **XL** (migrate 17 PM2 entries to launchd, or bundle Node — either way, large surgery).
**Dependencies:** E-1.

### E-8 — Multi-gigabyte ML model files have no distribution strategy [CRITICAL]
**What's missing:** A model-distribution mechanism. Currently `setup_codec.py` assumes the user runs `mlx_lm.server` with model names like `mlx-community/Qwen3.5-35B-A3B-4bit`, which `mlx-lm` resolves and downloads from Hugging Face on first request. README lists Qwen 3.5 35B (~20GB at 4-bit), Llama 3.3 70B (~40GB), plus Whisper large-v3-turbo (~3GB), Kokoro-82M (~300MB), and a vision model.
**Why it matters:** First-launch UX for a paid app cannot be "wait while we download 25GB from Hugging Face." Options: (a) bundle the smallest model set in the installer (Whisper turbo + Kokoro + Qwen-7B-class at 4-bit ≈ 8GB) and download the larger models on demand with progress UI; (b) ship a thin app that requires the user to pick a cloud LLM (defeats the local-first pitch); (c) ship a separate "model pack" download that auto-installs on first launch with explicit consent. Also: model files must NOT be inside the `.app` bundle (notarization signing every multi-GB binary is impractical) — they live in `~/Library/Application Support/CODEC/models/` or `~/.codec/models/`, but the path needs Disk Space + File Timestamp privacy declarations.
**Effort:** **XL** (model-pack downloader UI, integrity check, resumable downloads, progress reporting, fallback when the download is interrupted, storage in the right macOS-conformant location).
**Dependencies:** E-1, E-5.

### E-9 — Permissions UX assumes a terminal + System Settings walkthrough [HIGH]
**What's missing:** A first-launch onboarding screen that requests Microphone, Screen Recording, Accessibility, Full Disk Access (for iMessage DB), and Automation (per-target AppleScript) in the correct order with deep links to the right System Settings pane.
**Why it matters:** `install.sh:198-209` literally tells the user "Go to System Settings > Privacy & Security, add Terminal.app." For a paid app, that's unacceptable. The app must: (1) prompt for each permission with a clear "why we need this" panel; (2) deep-link to System Settings using `x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility` (and variants for Microphone, Screen Recording, Automation, Full Disk Access); (3) gracefully degrade when a permission is denied (e.g. wake-word disabled when mic is denied); (4) re-check permissions on every launch and prompt again if revoked. Also: with no `.app` (E-1), TCC currently grants permission to **the terminal that launched Python**, not to CODEC — the wrong identity.
**Effort:** **L** (build the onboarding wizard, ideally in Swift to match macOS look-and-feel, with TCC status checks, deep links, and "ask again" affordances).
**Dependencies:** E-1.

### E-10 — Cloudflare tunnel binary has no install strategy in the paid app [HIGH]
**What's missing:** A bundled or first-launch-installed `cloudflared` binary + automated tunnel config.
**Why it matters:** Inbound PWA access (the entire Overview/Cortex/voice-from-phone story) depends on `cloudflared`, which `setup_codec.py:511-515` instructs the user to install via `brew install cloudflared` and configure manually (login, tunnel create, DNS route, edit `config.yml`, Zero Trust email auth). A non-technical buyer cannot do this. Options: (a) bundle `cloudflared` in the app + own a Cloudflare account that vends tunnels per-customer (operational burden + cost on AVA Digital); (b) ship without inbound (LAN-only) for the paid v1 and recommend Tailscale (still requires user-side install); (c) integrate with a different remote-access service designed for end users.
**Effort:** **L** (option a or b, with implementation and ops design).
**Dependencies:** E-1, E-13.

### E-11 — No license validation architecture wired into CODEC [HIGH]
**What's missing:** A working license-check flow that gates the paid app. Currently `codec_slash_commands.py` references `~/ava-stack/license-server/licenses.db` (a server-side SQLite at the AVA backend, not on the user's machine) and decodes a JWT from `config["ava"]["license_key"]` to display tier + expiry — but only as a status display (`_ava_license_status`). Nothing in the codebase actually refuses to run when the license is invalid, expired, or missing.
**Why it matters:** A paid app must validate the license to (a) gate features behind tiers, (b) cut off access when the subscription lapses, and (c) handle offline gracefully (typical: 7-30 day grace period after last successful server check). Existing fragments suggest the architecture intent: `ava-license.lucyvpa.com` (license server), `ava-proxy.lucyvpa.com` (LLM proxy), a `ava-license` PM2 service running locally per the CLAUDE.md PM2 list (though it's not in `ecosystem.config.js` — probably runs on the AVA backend, not on the user's machine). The local CODEC needs: a JWT verification path with Apple-side public key, periodic refresh against the license server, offline grace window, hard cutoff with a clear "subscription expired" UI.
**Effort:** **L** (implement the actual license-gating module, JWT signature verification with the AVA public key, refresh-on-online + grace window, integration with first launch, surfacing in PWA + Cortex).
**Dependencies:** E-1.

### E-12 — No auto-update mechanism for the paid app [HIGH]
**What's missing:** Sparkle integration or equivalent. The OSS distribution updates via `git pull` + `pm2 restart all` (per `update.sh` and `install.sh --update`). Paid app buyers don't have a git checkout.
**Why it matters:** Users need updates for bug fixes, model improvements, and skill catalog refreshes. Sparkle (https://sparkle-project.org/) is the de facto standard for direct-download Mac apps: hosts an appcast XML on the AVA Digital CDN, Sparkle in the app checks for updates, downloads the signed delta `.dmg`, verifies the signature, prompts the user, replaces the bundle on next launch. Need to design: appcast URL, signing key for Sparkle's update verification (separate from Apple Developer ID), how to migrate `~/.codec/` data across versions, how to restart PM2 fleet cleanly after replacement, how to handle the multi-GB model files (don't re-download every update — diff against installed pack).
**Effort:** **L** (integrate Sparkle 2.x, set up appcast hosting on a CDN, generate + manage the Sparkle EdDSA key pair, write the post-update migration runner that restarts PM2/launchd services, document the rollback path).
**Dependencies:** E-1, E-2, E-3.

### E-13 — No Apple Developer Program enrollment evidence [CRITICAL]

> **Resolved (W5-Pre, 2026-05-24).** Mickael confirmed AVA Digital LLC is **enrolled with a Team ID + Developer ID Application certificate in hand** — the 2-4 week critical path is already cleared, so the signing (E-2) + notarization (E-3) pipelines are unblocked. Provisioning the App Store Connect API key for `notarytool` + the Sparkle EdDSA key (E-12) happens in those PRs.
**What's missing:** Any reference to a Team ID, Developer ID Application certificate, or Apple Developer Program membership in the repo or documented setup.
**Why it matters:** Code signing, notarization, and App Store distribution all require Apple Developer Program enrollment ($99/year for individual; $299/year for organization — for AVA Digital LLC, organization enrollment likely required, which adds D-U-N-S Number verification, can take 2-4 weeks). Once enrolled: generate Developer ID Application certificate via Xcode, generate App Store Connect API key for `notarytool`, generate Sparkle EdDSA key for auto-updates. None of this exists today.
**Effort:** **M** (enrollment paperwork + waiting period, then ~2h to provision certs).
**Dependencies:** none, but blocks E-2, E-3, E-12.

### E-14 — No uninstaller [HIGH]

> **🟡 SUBSTANTIALLY CLOSED — W5-12/PR-5E.** `packaging/macos/uninstall_codec.sh` — **safe by default** (no `--yes` = dry-run; deletes nothing). Auto-removes the CODEC-owned set: launchd agents (`bootout` + plist), the `.app`, `~/Library/Logs/CODEC`, Keychain items `ai.avadigital.codec.*`, and `~/.codec` (double-gated behind `--yes --purge-data`). Prints guided manual steps for the residue Apple/sharing rules make unsafe to auto-delete: **TCC grants** (Apple forbids self-revocation — exact System Settings path given), `~/.cloudflared/config.yml`, model cache, and the source checkout (never touched). Guarded `safe_rm` (refuses empty/`/`/`$HOME`); `--home` override lets tests exercise the destructive path entirely in a temp dir (Keychain gated to real `$HOME`). 5 tests (`tests/test_uninstaller.py`), validated by a real dry-run on macOS (listed all artifacts, `~/.codec` untouched). **Remaining:** the in-app "Uninstall" menu item (ties into W5-6/W5-11 GUI) calls the same script.

**What's missing:** A script or in-app affordance that removes CODEC cleanly: the `.app` bundle, `~/.codec/`, `~/.cloudflared/config.yml`, all PM2 (or launchd) services, the LaunchAgent plists, the trust grants in TCC, the Cloudflare tunnel registration, the model files, the `~/codec-repo` worktree, the AVA license token.
**Why it matters:** A paid app must support clean removal — both for users who churn and for support cases ("reinstall to fix X"). Currently the only doc-level guidance is `pm2 delete` (not even mentioned in install.sh). Apple's App Store technically requires Mac apps to leave no traces beyond `~/Library/Application Support/<bundle-id>/` when dragged to Trash; direct-download apps have more flexibility but users still expect a clean uninstall path. TCC grants persist even after removal — user has to manually clean Privacy & Security panes (Apple intentionally does not allow apps to revoke their own TCC entries).
**Effort:** **M** (write `uninstall.sh` + an in-app "uninstall CODEC" menu item that runs the same steps with a confirmation dialog; document the TCC residue + how to clean it).
**Dependencies:** E-7 (need to know if it's PM2 or launchd before uninstalling can target the right supervisor).

### E-15 — Onboarding requires manual API keys and ports that a paid user shouldn't see [MEDIUM]
**What's missing:** A "happy path" that doesn't require the user to choose ports (8081, 8084, 8085, 8090, 8091, 8094, 9223), pick an LLM provider, paste an API key, or decide between F-key variants. For the paid app, all of this should be auto-resolved: ports auto-detected with fallback, LLM defaults to bundled local model, API keys only requested for optional Google Workspace / Telegram integrations.
**Why it matters:** `setup_codec.py` is a 9-step terminal wizard with arrow-key menus. It's appropriate for an open-source build; for a paid app it has to become a GUI onboarding flow.
**Effort:** **L** (build the GUI onboarding — likely the same Swift wizard as E-9; auto-resolve ports; default to bundled model from E-8; treat all cloud integrations as optional add-ons).
**Dependencies:** E-1, E-6, E-7, E-8, E-9.

### E-16 — OSS distribution is unsigned [MEDIUM]

> **Closed by PR-5A (W5-1).** Decision recorded in `docs/APPLE-DISTRIBUTION.md` (D5): the OSS build **stays unsigned** (`git clone && ./install.sh`, developers build + trust their own toolchain) — only the **paid** app is signed + notarized. The `packaging/macos/` metadata applies to the paid build only.
**What's missing:** A decision on whether the public GitHub `git clone` flow should produce a signed Touch-ID helper / Swift overlay / Python launcher, so users don't have to disable Gatekeeper.
**Why it matters:** Today, `setup_codec.py:563-571` compiles `codec_auth` via `swiftc` inline (unsigned); the Swift overlay (`swift-overlay/`) has no build step in `install.sh` and isn't shipped. Users who clone the repo and run the installer end up with an unsigned helper they trust because they built it locally — that's fine. But if the OSS distribution ever ships a precompiled binary (e.g. via Homebrew tap or GitHub Releases), it needs to be signed (Developer ID), or users will hit Gatekeeper. Hybrid: keep OSS as "build from source" forever (user trusts their own `swiftc`); paid app gets signed binaries. **Recommended path: keep OSS as build-from-source; document the Gatekeeper workaround for any precompiled artifact.**
**Effort:** **S** (write the decision doc).
**Dependencies:** none.

### E-17 — Missing `entitlements.plist` for required runtime exemptions [HIGH]
**What's missing:** An entitlements file declaring: `com.apple.security.cs.allow-jit` (for MLX's Metal kernels and any JIT in numpy/torch fallbacks), `com.apple.security.cs.allow-unsigned-executable-memory` (for some PyObjC bridges), `com.apple.security.cs.disable-library-validation` (for loading unsigned `.dylib` from pip-installed packages), `com.apple.security.cs.allow-dyld-environment-variables` (sometimes needed for `DYLD_LIBRARY_PATH` overrides), `com.apple.security.automation.apple-events` (osascript control), `com.apple.security.device.audio-input` (microphone), `com.apple.security.network.client` + `com.apple.security.network.server`. Plus the per-target AppleScript descriptors for every app CODEC controls (Messages, Notes, Reminders, Spotify, Music, Calendar, Mail, Chrome, Safari, etc.).
**Why it matters:** Hardened runtime (required for notarization) blocks JIT and unsigned dylib loading by default. Without these entitlements, MLX inference will crash with `EXC_BAD_ACCESS`, and pip-installed `.so` files won't load. This is the single most common failure mode for Python apps trying to ship notarized.
**Effort:** **M** (write `entitlements.plist`, test each entitlement against the actual hardened-runtime launch, narrow the entitlement list to the minimum that works — Apple frowns on apps that request all CS exemptions blanketly).
**Dependencies:** E-1, E-2.

### E-18 — Telemetry / crash reporting are absent for paid customers [LOW]
**What's missing:** A telemetry pipeline. The OSS pitch is "no telemetry"; the paid app should have an opt-in crash reporter (Sentry, Bugsnag, or homegrown) so AVA Digital can fix customer-facing crashes without asking the user to upload logs manually.
**Why it matters:** Currently `~/.codec/audit.log` is the only diagnostic — local-only, never sent anywhere. For a paid product, opt-in crash reporting is table stakes. Must respect the local-first promise: crash reports default OFF, sanitize PII (no audit log raw, no clipboard, no OCR text — strip per the §6 privacy contract).
**Effort:** **M** (pick a backend, write the in-app opt-in UI, define the redaction layer that strips audit-log PII before upload).
**Dependencies:** E-11 (license tier may gate this).

---

## Shipping checklist

**Pre-shipping (must complete in order):**
- [ ] E-13 — Enroll AVA Digital LLC in Apple Developer Program (4-week lead time; do this FIRST)
- [ ] E-4 — Document the App-Store-is-out decision (≤1 day)
- [ ] E-16 — Decide OSS-stays-unsigned vs OSS-gets-signed (≤1 day)
- [ ] E-1 — Build the `.app` bundle wrapper + Info.plist + bundle ID
- [ ] E-7 — Pick PM2-vs-launchd, migrate process supervision
- [ ] E-6 — Pick bundled-Python-vs-probe, implement
- [ ] E-8 — Build model-pack downloader + bundled minimum set
- [ ] E-9 — Build first-launch permissions wizard
- [ ] E-17 — Write `entitlements.plist` with the minimum CS exemption set, test against hardened runtime
- [ ] E-2 — Implement code-signing pipeline (sign Python, all dylibs, helpers, the app)
- [ ] E-5 — Write `PrivacyInfo.xcprivacy` manifest
- [ ] E-3 — Implement notarization pipeline (`notarytool submit` + `stapler staple`)
- [ ] E-11 — Wire license validation into CODEC (JWT verify, refresh, offline grace, expiry cutoff)
- [ ] E-10 — Solve the Cloudflare tunnel paid-tier story (bundle vs LAN-only)
- [ ] E-15 — Replace the `setup_codec.py` terminal wizard with a GUI onboarding
- [ ] E-14 — Write the uninstaller (script + in-app menu)
- [ ] E-12 — Integrate Sparkle for auto-updates

**Post-shipping (v1.1):**
- [ ] E-18 — Opt-in telemetry + crash reporting
- [ ] (potential v2) per-target AppleScript permission requests with finer-grained UX

---

## Distribution path recommendation

**Developer ID + notarization, direct download (`.dmg` or `.pkg`).** Mac App Store is incompatible with CODEC's architecture — see §"CODEC system access → Apple sandbox mapping" and Finding E-4. The blocking incompatibilities (screen recording / OCR, blanket AppleScript control, hotkey/CGEvent tap, arbitrary subprocess spawning including PM2, full filesystem access to `~/.codec/**`) are non-negotiable for CODEC's value prop.

**Hybrid model is workable for the OSS/paid split:**

| Surface | Distribution | Signing |
|---|---|---|
| OSS / GitHub | `git clone && ./install.sh` (current) | Unsigned; user compiles helpers locally — they trust their own swiftc |
| Paid app | `.dmg` download from `avadigital.ai` | Developer ID signed + notarized + hardened runtime + entitlements + PrivacyInfo + Sparkle auto-update |

This preserves the MIT OSS pitch ("local-first, no subscription") while letting the paid app ship as a real Mac product. The paid app would internally reuse the OSS codec_*.py modules, wrapped in an `.app` with a Swift onboarding wizard, a bundled minimal model set, license gating via JWT, and Sparkle for updates.

**Realistic effort for v1:** roughly 6-10 weeks of focused engineering work for one engineer (plus the 2-4 week Apple Developer Program enrollment lead time in parallel). The XL items (E-6, E-7, E-8) are the long pole — bundling Python + replacing PM2 + the model-pack story is the surgery, not the code signing.

---

## Open Questions for Mickael

1. **What's the target launch date for the paid app?** With Apple Developer enrollment taking 2-4 weeks and the engineering work XL-class, the earliest realistic v1 ship is ~10-14 weeks from start.
2. **Is Apple Developer Program enrollment done for AVA Digital LLC?** If not, this needs a D-U-N-S Number lookup + AVA Digital LLC legal docs ready. Start this on day 1 — it gates everything signing-related (E-2, E-3, E-12).
3. **Pricing model — one-time license or subscription?** Affects license validation architecture (E-11). Subscriptions need a refresh cadence and an offline grace window; one-time licenses just need expiry + entitlement-tier metadata in the JWT.
4. **Is the OSS distribution intended to remain unsigned / require Gatekeeper bypass, or do we sign + notarize that too?** Recommended: leave OSS unsigned (user clones + builds), sign the paid app. Want to confirm before architecting (E-16).
5. **Model download strategy — bundle minimum models in the installer (~8GB `.dmg`), or first-run download (~25GB from CDN with progress UI)?** Affects E-8 + the buyer's first-launch UX. Bundling pushes the installer to 8GB+ but eliminates a fail point.
6. **PM2 or launchd for the paid app?** PM2 means bundling Node.js (+75MB, more signing complexity). launchd means rewriting 17 PM2 entries as `LaunchAgent` plists — cleaner but a big chunk of E-7 work.
7. **Cloudflare tunnel — does AVA Digital host the inbound tunnels per-customer (Mickael's account vends tunnels), or does the paid v1 ship LAN-only with Tailscale recommended?** Affects E-10 and ops cost.
8. **Where does the `ava-license` PM2 service actually live?** CLAUDE.md lists it but `ecosystem.config.js` doesn't include it. Confirm it's running on the AVA backend (not on user machines) so license validation is a network call, not a local PM2 service to bundle.

---

## Files reviewed

- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/README.md`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/CHANGELOG.md`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/CLAUDE.md`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/install.sh`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/update.sh`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/setup_codec.py`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/requirements.txt`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/config.json.example`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/ecosystem.config.js`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_slash_commands.py` (license validation references)
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_auth/main.swift`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/swift-overlay/Package.swift`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/request_mic.py`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_observer.py` (screencapture + Quartz usage)
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec.py` (hotkey + screencapture sites)
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_dictate.py` (keystroke injection)
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/scripts/` (directory listing — no Apple distribution scripts present)
- Repo-wide grep for: `Info.plist`, `codesign`, `notarytool`, `notarize`, `altool`, `entitlements`, `Sparkle`, `*.plist`, `*.entitlements`, `*.dmg`, `*.pkg`, `*.app`, `com.*.codec`, `bundleIdentifier`, `Developer ID`, `Team ID`, `Gatekeeper`, `hardened runtime`, `Mac App Store`, `PrivacyInfo`, `xcprivacy`, `NSPrivacyAccessedAPI`, `NSMicrophoneUsageDescription`, `NSAppleEventsUsageDescription`, `NSScreenCaptureUsageDescription`, `uninstall` — **all returned zero matches outside generic license strings**.
