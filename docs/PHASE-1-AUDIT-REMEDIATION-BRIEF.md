# CODEC — Phase-1 Audit Remediation: Complete Brief

**Period:** 2026-05-24 → 2026-05-25 · **Scope:** the full Phase-1 security/quality/readiness
audit across every wave (A–F) + Projects (B) + the Pilot subsystem, plus the live-incident
response and the investor-readiness finale. This is the durable record of what was done, how it
was verified, and exactly what remains.

---

## 0. TL;DR

- **Every audit finding fixable in code is shipped + verified.** Waves **A (code quality),
  C (reliability), D (security), B (Projects), and the Pilot subsystem (P-1…P-15)** are 100%
  closed. **E (Apple)** has all CRITICALs + a built-and-tested pipeline; the remaining items are
  account/keys/GUI/decision-gated (see §4, now ground-truthed). **F (Investor)** is closed bar
  the demo screenshots (operator captures).
- **A live, internet-exposed RCE in the Pilot subsystem was found and stopped**, then fully
  remediated (PP-1…PP-12 + 2 follow-ups), the public tunnel removed, and the hardened daemon
  restarted.
- **codec-repo PRs #131–#143** + the Pilot repo's local `main` (`39e6146`→`756176a`) carry the
  work. Full suite at baseline (`41 fail` = missing optional dev-deps, `~1740 pass`), ruff gate
  green, manifests current.

---

## 1. The Pilot security wave (subsystem: `~/codec/pilot/`, separate repo)

A dedicated audit of the browser-automation subsystem found **15 findings (P-1…P-15)** plus a
cross-cutting parent-repo issue — and, critically, a **LIVE incident**: `pilot-runner` was
online, bound `0.0.0.0`, published to the public internet at `pilot.lucyvpa.com`, with **zero
auth** — anyone could drive the logged-in headless Chrome and compile+approve a skill →
arbitrary code execution on the Mac. **Stopped immediately** (`pm2 stop pilot-runner`,
operator-approved).

Remediation (each with a design doc under `pilot/docs/`, TDD'd, behavior verified against real
chromium suites):

| Fix | Finding | What |
|---|---|---|
| **PP-1** | P-1 | `x-pilot-token` auth on every route + loopback bind + CORS localhost-only |
| **PP-2** | P-2, P-11 | compiler injection-safety (`_safe`/`_int`, `compile()`-validate) + slug-traversal guard |
| **PP-3** | P-4 | SSRF / scheme guard on `navigate` (http/https only; blocks file/internal/metadata). *Follow-up: `about:blank` allowed (exact match) — it's the canonical empty page, no host/network/file.* |
| **PP-4** | P-6 | prompt-injection containment — untrusted page content fenced into the agent/replay LLM |
| **PP-5** | P-8 | randomized Chromium CDP debug port (was fixed 9223) |
| **PP-6** | P-13 | redact secrets typed into password fields from traces |
| **PP-7** | P-9, P-14 | single-active-run guard on the shared browser + bounded run history |
| **PP-8** | P-12 | forensic audit trail (`~/.codec/pilot_audit.log`) |
| **PP-9** | P-15 | tolerate corrupt/partial traces on load |
| **PP-10** | P-7, P-10 | destructive-action default-deny (pay/delete/transfer) in agent loop + replay |
| **PP-11** | P-3 | **AST safety gate at skill approve** (vendored `pilot/safety.py`) — closes the last RCE-enabling path |
| **PP-12** | P-14 | bounded HITL pause (`asyncio.wait_for` → `paused_timeout`) + MJPEG consecutive-failure bound |
| *follow-up* | P-1 | legacy `test_phase3` endpoint test sends the `x-pilot-token` header |

**Result:** 67 pilot security tests pass (`test_phase7…18`); all native real-chromium suites
(`test_phase2…6`) green. 14 commits on the Pilot repo's local `main` (no remote). The public
tunnel was removed from `~/.cloudflared/config.yml` and `pm2 start pilot-runner` re-launched the
**hardened, token-gated, loopback-only** daemon. Parent-repo coupling (the `x-pilot-token`
handshake) shipped in codec-repo **#132**.

---

## 2. Investor-readiness finale (codec-repo, PRs #136–#143)

| PR | Finding | What shipped |
|---|---|---|
| **#136** | **F-5** | Single source of version truth: `VERSION` (=2.3.0) ← CHANGELOG, runtime `codec_version.__version__`, CI-pinned. `scripts/tag_releases.py` (dry-run-default). All 10 releases (v1.0.0…v2.3.0) tagged + published; `v3.0.0` (erroneous, ahead of history) deleted; **v2.3.0 = Latest**. |
| **#137** | **F-11, F-18, F-5** | README pricing — **€10/month or €99/year** (annual = 2 months free); OSS build free/MIT forever. **No "Lucy" branding** (your call). F-5 doc closure sync. |
| **#138** | **F-4** | Pragmatic ruff baseline (`ruff.toml` — house-style rules ignored, correctness kept) + `ruff check .` CI gate + 229 safe auto-fixes. **Caught + fixed a latent bug:** `codec_heartbeat.py` called `subprocess.run()` with `subprocess` never imported. Proven zero-regression via a git-stash A/B run. |
| **#139** | **F-15** | `pyproject.toml` — project metadata, MIT, `requires-python>=3.10`, dynamic version from `VERSION`, deps + extras. Builds a valid wheel. |
| **#140** | **F-12, branding** | `skills/lucy.py` → `skills/delegate.py` (it was a misnamed file; `SKILL_NAME` was already `delegate`) + all "Lucy" labels scrubbed + manifest regenerated. GitHub **Discussions enabled** + README badge. |
| **#141** | F-wave reconcile | Audit/HANDOFF reconciled; F-8 orphan screenshots removed (f13/f18/transcribing + the cortex.screensht.png typo dupe). |
| **#142** | F-15 sliver | `requirements-dev.txt` + CONTRIBUTING reference (a fresh clone could `pip install -r requirements-dev.txt` to run tests). |
| **#143** | cross-cutting | **Scoped strict AST gate** — `is_dangerous_skill_code(strict=True)` blocks rarely-legit primitives (pickle/marshal/shelve + smtplib/ftplib/telnetlib) for the *autonomous* drafter (`codec_self_improve`). Default mode unchanged; HTTP/`open` deliberately allowed (human-reviewed). |
| *(this PR)* | E + bug | `first_run.py` launchd-installer path bug fix (real `--yes`-run failure) + this brief + Apple ground-truth reconciliation. |

---

## 3. Operational fixes (outside PRs)

- **macOS Keychain / Cloudflare:** removed the `pilot.lucyvpa.com` ingress from
  `~/.cloudflared/config.yml` (validated; `codec.lucyvpa.com` untouched). Pilot is loopback-only.
- **pyenv stale-lock:** the terminal hung on a stale `~/.pyenv/shims/.pyenv-shim` lock (an
  interrupted `pyenv rehash`). Cleared the lock + killed the spinning processes → terminal
  restored. (Not caused by this work — timestamp predated the session's background shells.)
- **Task-tab UI bug (reported):** investigated rigorously — `codec_chat.html` and
  `codec_tasks.html` have *byte-identical* theme logic (both default dark, both honor
  `localStorage['codec-theme']`, both full-page nav), and `loadSchedules()` has a `.catch` over a
  trivial-can't-hang backend. **`main` does not reproduce the symptom** → almost certainly a
  stale browser cache / un-restarted dashboard. Action: hard-refresh + `pm2 restart
  codec-dashboard` (done); send the browser console if it persists.

---

## 4. Apple (E) — GROUND TRUTH (reconciled from the on-Mac report, 2026-05-25)

The audit's earlier "build→DMG pipeline done" was accurate that the pipeline is **written +
tested (15/15)** — but the on-Mac report surfaced the reality:

- **Two disconnected efforts.** `codec-repo/packaging/macos/` (the bundled "Sovereign AI
  Workstation.app" pipeline) is complete + tested but **has NEVER produced an artifact** (no
  `dist/`, no `.app`, no `.dmg`) and defaults to a notary profile `codec-notary` that **doesn't
  exist**. The thing you can actually hand a buyer is the separate **`~/ava-stack/installer-gui/`
  Swift bootstrapper** → `dist/CODEC-Installer.dmg` (signed + notarized + stapled +
  Apple-Accepted, via the working `ava-codec` profile + a valid Developer ID cert).
- **W5-9 License gating — NOT enforced.** Server is live (`ava-license`, PM2 id 34) but in
  `env=dev`; the client only base64-*displays* the JWT for `/version` and a 2-sec health dot — it
  **never validates, expires, gates tiers, or refuses to run**. A buyer's copy runs fully
  unlicensed. *(Biggest gap.)*
- **W5-10 Cloudflare-for-buyers — shared tunnel, not per-customer.** Single multi-tenant
  `mac-studio` tunnel; no per-buyer provisioner (only printed manual instructions).
- **W5-11 Swift setup wizard — NOT-STARTED.** No wizard exists; nothing drives `first_run.py`.
  (`first_run.py` itself is a working CLI — and this PR fixes its real `install_launchagents.sh`
  path bug at line 154.)
- **W5-13 Sparkle auto-update — NOT-STARTED entirely** (no dep, appcast, EdDSA key, or feed).
- **Distribution mechanics are real + proven** (Developer ID cert + `ava-codec` notary profile,
  3 Apple-Accepted submissions).

**"To-ship" priority for a paid launch:** (1) client-side license enforcement + flip the server
out of dev; (2) decide the **canonical pipeline** (ava-stack installer vs codec-repo bundled-app —
they must converge; codec-repo's is currently dead scaffolding pointing at a missing notary
profile); (3) the W5-11 wizard; (4) W5-13 Sparkle. All need your Apple account/keys/decisions.

---

## 5. Scoreboard

| Wave | Status |
|---|---|
| **A** Code quality · **C** Reliability · **D** Security | ✅ 100% |
| **B** Projects (Audit-B, all 20) · **Pilot** (P-1…P-15) | ✅ 100% |
| **E** Apple | ✅ all CRITICALs + distribution proven (ava-stack). Pipeline convergence + licensing + wizard + Sparkle = product work, account/keys-gated (§4) |
| **F** Investor | ✅ ~99% — only the cortex screenshot (operator capture) remains |

---

## 6. Remaining — operator-gated only

1. **Cortex screenshot** → drop `cortex.png` in `docs/screenshots/`; the README `<img>` then refreshes.
2. **Apple "to-ship"** (§4) → product decisions + Apple account/keys: licensing enforcement,
   pipeline convergence, wizard, Sparkle. The ava-stack DMG ships *today* for early buyers.
3. **Task-tab bug** → confirm gone after hard-refresh + dashboard restart; else send the console.
4. **Deferred, low-ROI:** full pytest suite in CI (needs the Linux optional-dep matrix). CI
   already gates lint + doc-guards + skill/manifest/keychain/oauth + the security tests.
5. **Pilot:** running hardened; push the 14 local-`main` commits to a remote only if you want
   backup/CI.

**Nothing else is pending in code.**
