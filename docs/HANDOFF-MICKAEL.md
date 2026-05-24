# CODEC — Handoff & Action Items for Mickael

> **Living doc.** Everything across the Phase-1 audit that needs *your* hands — things I (the agent) can't do from here: merges, Apple cert/keys, business decisions, accounts, and manual one-time setup. Updated as PRs land. Last update: 2026-05-24.
>
> Legend: 🔴 blocking · 🟠 before paid launch · 🟡 decision needed · ⚪ optional/informational

---

## 0. Right now — merge the open PR queue 🔴

I work on isolated branches and never self-merge. **Review + merge these in order** (each is design-first → TDD → CI-green; squash-merge is fine):

| PR | Title | Wave |
|---|---|---|
| #82 | C-2 PWA response bridge | 4 |
| #83 | C-5 `_atomic_set_status` | 4 |
| #84 | H-1 lifecycle helper | 4 |
| #85 | H-3 audit flock | 4 |
| #86 | H-4/H-6/M-6 bounded dicts | 4 |
| #87 | H-7/H-8/H-9 tempfile leaks | 4 |
| #88 | M-3/M-4/L-1/L-2 small fixes | 4 |
| #89 | H-5 rate-window eviction | 4 |
| #90 | H-2 codec.py state lock | 4 |
| #91 | M-5 per-thread DB | 4 ✅ (merged) |
| #92 | W5-1 Apple distribution foundation | 5 |
| #93+ | Wave-6 readiness PRs | 6 |

*(Once merged, my later branches that built on origin/main pick them up automatically.)*

---

## 1. Apple paid app (Wave 5) 🟠

**Enrollment: ✅ done** (you confirmed Team ID + Developer ID Application cert in hand).

When the Wave-5 build PRs land, these need you on a real macOS build machine — I author the scripts/plists; you run + validate them:

- 🟠 **Wire the Developer ID Application cert** into the signing step (import to the build machine's login keychain; for CI, store as a base64 secret + `security import`).
- 🟠 **Create an App Store Connect API key** (Issuer ID + Key ID + `.p8`) for `notarytool` notarization. Store as CI secrets.
- 🟠 **Generate a Sparkle EdDSA key pair** (`generate_keys` from Sparkle) for signed auto-updates (when E-12/W5-13 lands). Keep the private key OFF the repo.
- 🟠 **Run build → sign → notarize → staple on a Mac with the cert** (the scripts can't run from CI without the cert/keys above).
- 🟠 **Test the `.app` on a clean Mac** (no Homebrew/dev tools): permissions wizard, fleet startup under launchd, model download, Touch ID.
- ⚪ **Confirm the bundle ID** — I used `ai.avadigital.codec` (matches your Keychain prefix). Change before first signed build if you want a different ID (it's baked into TCC grants afterward).

---

## 2. Decisions still open (Wave 5 downstream) 🟡

These don't block the Wave-5 bundle/launchd/Python work, but gate later steps. Pick when ready:

- 🟡 **Model strategy (E-8 / W5-5):** which models bundle in the installer vs download-on-demand. *Recommendation:* bundle the minimum set (Whisper-turbo + Kokoro + a 7B-class 4-bit ≈ 8 GB), download the big Qwen/Llama on first use with a progress UI + consent.
- 🟡 **License gating (E-11 / W5-9):** offline grace-window length (typical 7-30 days), hard-cutoff UX, tier→feature map. (Backend exists at `ava-license.lucyvpa.com`; nothing currently *refuses to run* on an invalid license.)
- 🟡 **Cloudflare for buyers (E-10 / W5-10):** paid v1 LAN-only vs AVA-vended per-customer tunnels (recurring cost on AVA). *Recommendation:* LAN-only v1, revisit.
- 🟡 **Pricing + launch date (F-11):** gates the paid-tier README subsection + license work.

---

## 3. Investor / enterprise readiness (Wave 6) 🟠🟡

I'm writing the docs; a few items need your accounts/assets:

- 🟠 **Enable GitHub Private Vulnerability Reporting** — repo *Settings → Code security → Private vulnerability reporting → Enable*. (SECURITY.md points researchers there as the primary channel.)
- 🟡 **Confirm/create the contact emails** referenced in SECURITY.md (`security@avadigital.ai`) and CODE_OF_CONDUCT.md (`conduct@avadigital.ai`) — or tell me a different address and I'll update them. Until they exist, GitHub private reporting is the working fallback.
- 🟠 **Record a ~20-second demo GIF** (F-8) for the top of the README — a real screen-capture of voice→action is something only you can shoot on your machine. I'll wire it into the README once you drop the file in `docs/assets/`.
- 🟠 **Create a Discord server + enable GitHub Discussions** (F-12). Give me the invite link and I'll add the badge/links to the README.
- ⚪ **GitHub Sponsors** (F-7) — `.github/FUNDING.yml` is in place pointing at your PayPal + site; optionally enroll the org in GitHub Sponsors to light up the "Sponsor" button.
- 🟡 **Lucy / agent-to-agent positioning (F-18)** — your brand call on how prominently to feature it in the README.

---

## 4. Informational — no action required ⚪

- **Local test failures:** running the full suite on *your* machine shows ~41 failures — these are all **missing optional dev deps** (`pynput`, `fastmcp`, `qrcode`, pilot/e2e, keychain-on-non-mac). CI (and a machine with those deps) is clean. Not real failures.
- **Keychain rotation:** procedures for the secrets migrated in Wave 2 (audit HMAC, internal token, provider keys) are documented in `AGENTS.md §10` — only needed if you rotate.
- **Feature-flag env vars:** `ASKUSER_ENABLED`, `STUCK_DETECTION_ENABLED`, `OBSERVER_ENABLED`, `TRIGGERS_ENABLED`, `AGENT_RUNNER_ENABLED`, etc. (defaults true) — documented in `AGENTS.md §10`.
- **Branch protection:** `main` is protected (you enabled it earlier).

---

## 5. Audit completion status (for reference)

| Audit | Wave | Status |
|---|---|---|
| D — Security | 1-2 | ✅ closed (PRs #56-#71 era) |
| A — Code quality | 3 | ✅ closed |
| C — Reliability | 4 | ✅ **fully closed** (all 5 CRITICAL + 9 HIGH + 6 MEDIUM + 2 LOW) |
| E — Apple app | 5 | 🟠 in progress (W5-1 done; build pieces are XL + need your Mac/cert) |
| F — Investor readiness | 6 | 🟢 in progress (doc-heavy, mostly doable now) |
| B — Projects + Pilot | 7 | ⏳ not started (needs your scope description) |
