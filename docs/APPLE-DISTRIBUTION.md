# CODEC — Apple Distribution Decision Record (Wave 5 / Audit E)

> **Status:** W5-1 — decisions locked, foundation started. This is the authoritative
> record of HOW the paid **Sovereign AI Workstation** Mac app ships. Every later
> Wave-5 PR (bundle, signing, notarization, launchd migration, model packs) references
> the decisions here. Source audit: `docs/audits/PHASE-1-APPLE-APP.md` (E-1…E-18).

## 1. Locked decisions (2026-05-24)

| # | Decision | Choice | Audit ref |
|---|---|---|---|
| D1 | **Distribution channel** | **Developer ID signed + notarized, direct download** (`.dmg`/`.pkg`). **Mac App Store ruled out** (forced — see §2). | E-4 |
| D2 | **Apple Developer Program** | **Enrolled — Team ID + Developer ID Application cert in hand.** Signing/notarization are unblocked (no 2-4 week wait). | E-13 |
| D3 | **Python runtime** | **Bundle `Python.framework`** (relocatable build + all pip deps in the `.app`, ~80 MB). Clean install with no Homebrew; every embedded `.so` gets signed. | E-6 |
| D4 | **Service supervisor** | **macOS-native `launchd` LaunchAgents** (no Node/PM2 in the shipped app). The ~17 PM2 entries become per-service plists. | E-7 |
| D5 | **OSS distribution** | **Stays unsigned** — `git clone && ./install.sh`, developer builds + trusts their own toolchain. Only the **paid** app is signed/notarized. | E-16 |
| D6 | **Bundle identifier** | **`ai.avadigital.codec`** — matches the existing macOS Keychain service prefix `ai.avadigital.codec.*` (PR-2B/2D/2E) so TCC + Keychain identities line up. | E-1 |

## 2. Why the Mac App Store is impossible (forced, not a preference) — E-4

Five core capabilities are **non-sandboxable**; an App Store (sandboxed) build would gut ~80% of the value prop (Pilot, Observer OCR, Dictate, the F-key listeners, the PM2/launchd fleet, iMessage):

| Capability | macOS API | Sandbox verdict |
|---|---|---|
| Screen recording / OCR (observer, "check my screen") | `screencapture -x` + Vision via osascript + Quartz | **No** — sandboxed apps can't capture other apps' windows |
| Blanket AppleScript control (iMessage/Notes/Reminders/Spotify/… — 146 osascript sites) | `osascript`/NSAppleScript, per-target | **No** — App Store rejects blanket Apple-events; direct OK with per-target `NSAppleEventsUsageDescription` |
| Hotkey listener / keystroke injection (F13/F18, F5 live-typing) | CGEvent tap (`pynput`/`pyautogui`) | **No** — requires Accessibility, not sandboxable |
| Arbitrary subprocess spawning (launchd/python/mlx/sox/cloudflared/git — 105+ sites) | posix_spawn / fork+exec | **No** — sandbox forbids exec of binaries outside the bundle |
| Full `~/.codec/**` + iMessage DB filesystem access | `open`/`sqlite3` over `~/Library/Messages/chat.db`, `~/.codec/memory.db`, … | **No** — outside any sandbox container; needs Full Disk Access |

**Conclusion:** Developer ID + notarized direct download is the only viable path. (Network client/server, mic, clipboard, idle detection, Touch ID *are* sandbox-compatible, but the five above are not.)

## 3. Signing + hardened-runtime shape (for E-2/E-3)

- **Hardened runtime** is required for notarization. Because the app embeds CPython + PyObjC + native `.so`s and spawns subprocesses, it needs:
  - `com.apple.security.cs.allow-jit` and `com.apple.security.cs.disable-library-validation` (let bundled CPython load + run the signed-but-third-party `.dylib`s from `mlx`/`numpy`/`pyobjc`).
  - `com.apple.security.cs.allow-unsigned-executable-memory` (CPython/JIT-y extensions) — include if notarization flags it.
- **NOT** `com.apple.security.app-sandbox` (direct distribution; see §2).
- **Network**: `com.apple.security.network.client` + `.network.server` (dashboard :8090, MCP HTTP :8091, Cloudflare tunnel).
- Sign order (later PR): every embedded `.so`/`.dylib` → `Python.framework` → `cloudflared` → Touch-ID helper → Swift overlay → outer `.app`, then `notarytool submit` + `stapler staple`.
- **Models live OUTSIDE the bundle** (`~/Library/Application Support/CODEC/models/` or `~/.codec/models/`) — notarizing multi-GB binaries is impractical (E-8).

## 4. The W5 bundle metadata (this PR — `packaging/macos/`)

Decision-complete, no build required; consumed by the signing pipeline later:
- **`Info.plist`** — `CFBundleIdentifier=ai.avadigital.codec`, version keys, `LSMinimumSystemVersion`, and every TCC usage string the §2 table implies (`NSMicrophoneUsageDescription`, `NSAppleEventsUsageDescription`, `NSScreenCaptureUsageDescription` (informational), `NSDesktopFolderUsageDescription`, `NSDocumentsFolderUsageDescription`, `NSDownloadsFolderUsageDescription`).
- **`codec.entitlements`** — the hardened-runtime set in §3 (no app-sandbox).
- **`PrivacyInfo.xcprivacy`** — Required-Reasons APIs CODEC actually hits: `NSPrivacyAccessedAPICategoryFileTimestamp` (observer recent-files mtime, scheduler) reason `C617.1`, `NSPrivacyAccessedAPICategoryDiskSpace` (heartbeat health, `df`) reason `E174.1`; `NSPrivacyTracking=false`, empty `NSPrivacyTrackingDomains`, empty `NSPrivacyCollectedDataTypes` (local-first — nothing leaves the machine).

## 5. Roadmap (Audit E checklist, decisions filled in)

| Step | Finding | Status |
|---|---|---|
| W5-Pre | E-13 Apple Developer enrollment | ✅ **done** (D2) |
| **W5-1** | E-4 + E-16 decision docs | ✅ **this PR** (+ §4 metadata) |
| W5-2 | E-1 + E-17 + E-5 `.app` wrapper + Info.plist/entitlements/PrivacyInfo | metadata ✅ this PR; `.app` wrapper + Python launcher next |
| W5-3 | E-7 PM2 → **launchd** migration (17 services) | decided (D4); XL |
| W5-4 | E-6 **bundle Python.framework** | decided (D3); XL |
| W5-5 | E-8 model-pack downloader + bundled minimum set | **open** (recommend: bundle Whisper-turbo + Kokoro + a 7B-class 4-bit ≈ 8 GB, download larger on demand) |
| W5-6 | E-9 first-launch permissions wizard (Swift) | pending |
| W5-7 | E-2 code-signing pipeline | unblocked (D2) |
| W5-8 | E-3 notarization pipeline | follows E-2 |
| W5-9 | E-11 license validation wired in | **open** (AVA license server `ava-license.lucyvpa.com`; JWT verify + offline grace) |
| W5-10 | E-10 Cloudflare tunnel for end-users | **open** (recommend: paid v1 LAN-only or AVA-vended tunnels — ops/cost call) |
| W5-11 | E-15 GUI onboarding replacing `setup_codec.py` | pending |
| W5-12 | E-14 uninstaller | pending |
| W5-13 | E-12 Sparkle auto-update | pending (needs Sparkle EdDSA key) |
| W5-Post | E-18 opt-in crash reporting | v1.1 |

## 6. Still-open decisions (flagged for Mickael, downstream)
- **Model strategy (E-8/W5-5):** which models bundle vs download-on-demand; storage location; resumable-download UX.
- **License gating (E-11/W5-9):** offline grace window length; hard-cutoff UX; tier→feature map.
- **Cloudflare for buyers (E-10/W5-10):** LAN-only v1 vs AVA-vended per-customer tunnels (recurring cost).
- **Pricing / launch date:** business inputs that shape the license + onboarding work (not build-blocking).

These don't block W5-2/3/4 (bundle + launchd + Python), which proceed on the locked decisions.
