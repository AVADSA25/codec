# CODEC — Handoff & Action Items for Mickael

> **Living doc.** Everything across the Phase-1 audit that needs *your* hands — things I (the agent) can't do from here: merges, Apple cert/keys, business decisions, accounts, and manual one-time setup. Updated as PRs land. Last update: 2026-05-24.
>
> Legend: 🔴 blocking · 🟠 before paid launch · 🟡 decision needed · ⚪ optional/informational

---

## 0. PR queue status ✅ (clear)

All PRs through **#112 are merged**. Queue clear.

- **Wave 4 (Reliability):** #82–#91 ✅.
- **Wave 5 (Apple):** #92, #98, #106–#112 ✅ — full build→sign→notarize→staple→DMG pipeline + launchd + Python bundle + models + first-run + uninstaller. All Audit-E CRITICALs closed.
- **Wave 6 (Investor):** #93–#97 ✅ + the 7 Dependabot bumps you merged.
- **In flight:** **Wave 7 has started** — **PR-7A fixes B-1** (the phantom destructive-consent gate → now wired to the real `codec_ask_user.ask`).

> 🔴 **Audit B found 3 CRITICALs in the autonomous-agent permission gate** (`PHASE-1-PROJECTS-PILOT.md`) — **all three now addressed:** **B-1 ✅ (PR-7A)** consent gate wired · **B-2 🟡 (PR-7B)** destructiveness server-derived · **B-3 🟡 (PR-7C)** `/grant` now refuses blocklisted/over-broad path grants. **Two follow-ups need a 🟡 decision from you, both deferred:** (1) **B-2 remainder** — server-deriving path/network *category+values* needs a per-skill capability model (**curated table vs `SKILL_CAPABILITIES` metadata across ~76 skills**, XL/design-first); (2) **B-3 per-agent ownership authz** (only matters if the dashboard goes multi-user — today it's single-user behind global auth + loopback). Wave 7 burn-down underway: **B-7 ✅ (PR-7D)** cross-process status-CAS flock. Remaining Audit-B items are HIGH/MEDIUM/LOW (B-4, B-5, B-6, B-8, B-9, B-10…B-20) per the audit doc's PR-7E/7F plan.

I work on isolated branches and never self-merge; each new branch builds on the latest `main`.

---

## 1. Apple paid app (Wave 5) 🟠

**Enrollment: ✅ done** (you confirmed Team ID + Developer ID Application cert in hand).

**Progress:** W5-1 (metadata) ✅ · W5-2 (`.app`) ✅ · W5-3 (launchd) ✅ · W5-4 (Python) ✅ · W5-5 (models) ✅ · W5-6 (first-run orchestration, `packaging/macos/first_run.py`) ✅ · W5-7/8 (sign + notarize) ✅ · W5-12 (uninstaller) ✅ · W5 capstone (`release_macos.sh` → build/sign/notarize/staple/DMG) ✅. **All Audit-E CRITICALs closed; build→DMG pipeline + first-run logic authored + tested.** Remaining (all GUI/decision/key-gated, need you): **W5-11** native Swift wizard (drives `first_run.py`'s deep-links), **W5-9** license gating, **W5-10** Cloudflare-for-buyers, **W5-13** Sparkle auto-update.

- ⚪ **See your live permission status now:** `python3 packaging/macos/first_run.py --permissions-only` prints which TCC grants CODEC has + deep links to fix the rest. (`--dry-run` previews the whole first-run sequence; `--yes` does the real install + ~6 GB model fetch.)

**The full release is now ONE command** (run on your build Mac with the cert):
```
bash packaging/macos/release_macos.sh \
  --identity "Developer ID Application: … (TEAMID)" \
  --keychain-profile codec-notary --version 2.3.0
# → build → sign → notarize → staple → dist/Sovereign-AI-Workstation-2.3.0.dmg
```
Add `--dry-run` to preview every step; `--skip-notarize` / `--skip-dmg` to do partial runs. I've validated build + Python bundle + DMG creation + the sign/notarize *plans*; only the cert-gated execution is left to you.

- ⚪ **Try the uninstaller (safe):** `bash packaging/macos/uninstall_codec.sh --dry-run` lists exactly what a real uninstall would remove and deletes nothing. (Real removal needs `--yes`; user data needs `--yes --purge-data`.) Note: macOS won't let the app revoke its own TCC grants — the script prints the System Settings path to clear them by hand.

- 🟡 **Confirm the Python-runtime mechanism (W5-4).** The locked decision said "bundle Python.framework," but python.org's framework isn't relocatable/signable for redistribution. I used **`python-build-standalone`** (CPython 3.12.13, sha256-pinned in `packaging/macos/python-runtime.json`) — the standard for embedding Python in shipped Mac apps (uv/Rye/Briefcase). Validated end-to-end on arm64. **Same intent; just confirm you're good with it** (one-file swap if not).
- 🟠 **Validate the full Python bundle on your build Mac:** `bash packaging/macos/build_app.sh --with-python --clean` then check the *full* `pip install -r requirements.txt` succeeds with the native/ML wheels (numpy 2.x, soundfile, sounddevice, mlx). I only validated the runtime + pip mechanism (skipped the heavy native install). Any wheel that bakes an absolute dylib path gets fixed with `install_name_tool` at sign time (W5-7).

**Validate on your Mac when you have a sec:** `bash packaging/macos/launchd/install_launchagents.sh --dry-run` (it'll refuse since PM2 is live — that's the safety guard working). The real cutover (stop PM2 → install LaunchAgents) is a deliberate, later step.

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

- 🟡 **Model strategy (E-8 / W5-5):** the **downloader + tiered manifest now exist** (`packaging/macos/{fetch_models.py,models.json}`; try `python3 packaging/macos/fetch_models.py --tier all --dry-run`). It ships my *recommended* default — **bundled** (≈6 GB): Whisper-turbo + Kokoro + Qwen-7B-4bit; **on_demand**: Qwen-35B + Qwen2.5-VL-7B. **Two things from you:** (1) confirm/adjust the list + tiers; (2) **pin each `revision` to a commit SHA** (currently `main`) for supply-chain integrity before launch.
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
- 🟡 **Lucy / agent-to-agent positioning (F-18)** — your brand call on how prominently to feature it in the README. (The generic bidirectional-MCP / agent-to-agent story is already in the README; this is only about whether to *name* "Lucy".)
- 🟡 **Release-versioning decision (F-5)** — the product is **v2.3** but the only git tag is **v3.0.0**. Pick the source of truth, then I'll prepare a `scripts/tag_releases.sh` that maps each CHANGELOG entry → its commit; you run it to push the tags + enable GitHub Releases. (Nothing is tagged today, so the Releases page is empty.)
- 🟡 **F-4 CI depth — your call on the trade-off.** PR-6I expands CI to gate the deterministic readiness/doc tests + adds Dependabot (no working-code edits). Gating the *full* test suite would mean cleaning **639 repo-wide `ruff` findings** in working modules — which I won't touch without your explicit OK (your standing "never touch working code" rule). Options: leave as-is (CI already gates 6 test files + the D-1 manifest gate + the readiness tests), **or** greenlight a dedicated ruff-cleanup pass.
- 🟠 **Fill the ONE-PAGER private fields** — `docs/ONE-PAGER.md` ships with explicit placeholders for the **founder bio**, the **raise/ask sentence**, and the **pricing band** (left blank because the repo is public). Fill a private copy before any investor send. Optionally I can draft `docs/VISION.md` (3-page narrative) on the same public-safe-placeholder basis.
- ⚪ **Tiny follow-up:** link `docs/PRIVACY.md` from the README "Privacy & Security" section (one line; I'll fold it into the next README-polish PR).

---

## 4. Informational — no action required ⚪

- **Local test failures:** running the full suite on *your* machine shows ~41 failures — these are all **missing optional dev deps** (`pynput`, `fastmcp`, `qrcode`, pilot/e2e, keychain-on-non-mac). CI (and a machine with those deps) is clean. Not real failures.
- **Keychain rotation:** procedures for the secrets migrated in Wave 2 (audit HMAC, internal token, provider keys) are documented in `AGENTS.md §10` — only needed if you rotate.
- **Feature-flag env vars:** `ASKUSER_ENABLED`, `STUCK_DETECTION_ENABLED`, `OBSERVER_ENABLED`, `TRIGGERS_ENABLED`, `AGENT_RUNNER_ENABLED`, etc. (defaults true) — documented in `AGENTS.md §10`.
- **Branch protection:** `main` is protected (you enabled it earlier).
- **Cold "repo-readiness audit" email (2026-05-24):** `PressureDesk <pressuredesk@agentmail.to>` → `ava.dsa25@proton.me`, offering a "fixed-scope full audit" for **49 USDC**, teaser "75/100, missing_claude_project_settings." Assessment: **automated cold outreach — don't pay, don't reply.** We already run four internal audits far deeper than a public-signals scorecard. Its one concrete hook (`.claude/` project settings) is moot — `.claude/` is intentionally git-ignored, and the repo's `CLAUDE.md`/`AGENTS.md` front door already covers agent-onboarding far beyond a settings file. Logged here for the record only.

---

## 5. Audit completion status (for reference)

| Audit | Wave | Status |
|---|---|---|
| D — Security | 1-2 | ✅ closed (PRs #56-#71 era) |
| A — Code quality | 3 | ✅ closed |
| C — Reliability | 4 | ✅ **fully closed** (all 5 CRITICAL + 9 HIGH + 6 MEDIUM + 2 LOW) |
| E — Apple app | 5 | 🟠 **all CRITICALs closed; full build→DMG pipeline + first-run logic done.** W5-1/2/3/4/5/6/7/8/12 + capstone shipped. Remaining: W5-11 Swift wizard, W5-9 license, W5-10 Cloudflare, W5-13 Sparkle — all GUI/decision/key-gated → you |
| F — Investor readiness | 6 | 🟢 ~90% closed — F-1,2,3,6,7,9,10,13,14,16,17 done; F-18 partial. Remaining gated on you: F-4 (ruff-cleanup decision), F-5 (versioning), F-8 (GIF), F-11 (pricing), F-12 (Discord), F-15 (pyproject, deferred). |
| B — Projects (+ Pilot) | 7 | ✅ Audit done (20 findings). **Wave 7 fixes in progress: B-1 ✅ (PR-7A); B-2/B-3 next.** **Pilot half deferred** — needs the `~/codec/pilot/` checkout. |
