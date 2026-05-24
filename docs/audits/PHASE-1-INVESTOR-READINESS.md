# PHASE 1 AUDIT F — INVESTOR / ENTERPRISE-GRADE READINESS

**Date:** 2026-05-17
**Auditor:** general-purpose agent (Audit F)
**Scope:** What blocks "investor-grade" / "enterprise-grade" positioning for YC application, enterprise sales, and paid Mac app launch.
**Mode:** AUDIT-ONLY — research and documentation only, no code changes.

---

## Summary

- **Total findings:** 19
- **Critical:** 3 · **High:** 7 · **Medium:** 6 · **Low:** 3
- **Overall investor-readiness score: 6.5 / 10**

**Headline:** the *product* and the *documentation* are 8/10 (extraordinary depth for a 2-person, 26-commit repo). The *positioning surface a YC partner / enterprise CISO actually reads first* — top of README, CI badges, SECURITY.md, demo loop — is 4/10. Two days of focused work on the "outside layer" (badges that match reality, vuln disclosure policy, one-pager, Discord, demo GIF in the first viewport) closes most of the gap without touching code.

The single biggest gap is the **absence of a vulnerability-disclosure policy (SECURITY.md) and a privacy / data-handling page** — both are blanket-no items on every enterprise security questionnaire and a credibility tell at YC interviews for any "agent that runs on your machine" pitch.

---

## Methodology

Reviewed in this order:
1. `README.md` (full, 775 lines) for value-prop clarity, unique-angle visibility, badges, demo, install, pricing.
2. `docs/` directory listing — 32+ files including phase design docs, ARCHITECTURE.md, API.md, MCP_HTTP_SETUP.md, known-issues.md, audits/ subfolder.
3. Repo-root OSS health files (LICENSE, CONTRIBUTING.md, CHANGELOG.md, CODE_OF_CONDUCT.md, SECURITY.md, FUNDING.yml).
4. `.github/` — workflows/, ISSUE_TEMPLATE/, PULL_REQUEST_TEMPLATE.md.
5. `tests/` — 54 `test_*.py` files, 873 test functions (verified via grep of `def test_`).
6. `git tag -l` — 2 tags (`pre-dashboard-redesign`, `v3.0.0`).
7. Competitive comparison from training knowledge of Open Interpreter, Aider, Goose (Block), Continue, Khoj, Cursor positioning.
8. `skills/` directory (76 files, 70 with `SKILL_MCP_EXPOSE = True`).
9. `install.sh`, `requirements.txt`, `pytest.ini`, `ecosystem.config.js` (referenced).
10. CLAUDE.md / AGENTS.md (front-door context, 920+ lines).

---

## Findings

### F-1 — No SECURITY.md / vulnerability disclosure policy [CRITICAL]

**What's missing:** `SECURITY.md` does not exist at repo root. No file referenced in `.github/` either. GitHub does not display the "Security policy" tab.

**Why investors / enterprise customers expect it:**
- **Enterprise security questionnaires** universally include: "Does the vendor have a published vulnerability disclosure policy?" — a blanket no when the file is absent.
- For an agent that *executes code, controls the mouse, reads the screen, and writes files on the user's behalf*, the absence of a disclosure policy is a credibility tell. CODEC has a 46-pattern dangerous-command blocklist, AES-256-GCM session keys, Touch ID + TOTP, an OAuth 2.1 MCP HTTP transport — but no documented way for a researcher to report a finding.
- GitHub's own security advisory product is free and renders a public timeline once `SECURITY.md` exists.

**Recommended fix:** Add `SECURITY.md` at repo root covering: (a) supported versions (currently v2.3 / engine v3.0.0), (b) disclosure channel (security@ email or GitHub private vuln reporting toggle), (c) response SLA (e.g. acknowledge in 72h, patch in 30d for high-sev), (d) scope (in-scope: codec_* modules, MCP HTTP transport, OAuth flow, skill execution sandbox; out-of-scope: user-authored skills/plugins, user's LLM provider).
**Effort:** S (1-2 hours).

---

### F-2 — No public-facing privacy / data-handling statement [CRITICAL]

**What's missing:** No `PRIVACY.md`, no privacy page at opencodec.org referenced in README, no AI Act / GDPR alignment note. README §"Privacy & Security" lists 6 technical layers but does not answer the EU enterprise question: *"what categories of personal data are processed, on what legal basis, with what retention?"*.

**Why investors / enterprise customers expect it:**
- Mickael is **French, based in Marbella, Spain** — primary commercial market is EU. GDPR applies the moment a paid tier exists; AI Act (in force 2026) classifies "AI systems that interact with natural persons" and require transparency obligations even for local-first agents.
- Even though CODEC is local-first by design, **iMessage / Telegram / Twilio bridges** and **cloud LLM fallback** (Claude / GPT-4o via AVA proxy per CLAUDE.md §codec_ava_client) ARE data flows that need disclosure.
- The MCP HTTP transport with claude.ai bridge means **prompts and tool outputs traverse Anthropic** when used that way — needs to be disclosed.

**Recommended fix:** Add `docs/PRIVACY.md` with explicit data-flow diagram (local vs cloud), AI Act Art. 50 transparency statement, GDPR Art. 13 disclosures for the paid tier, third-party processors list (DuckDuckGo, Cloudflare, Anthropic/OpenAI when configured, Google Workspace OAuth). Link from README.
**Effort:** M (3-4 hours, ideally reviewed by a privacy lawyer before paid launch).

---

### F-3 — README badges overstate verified state ("940+ tests", "400+ features") [CRITICAL for investor trust]

> **✅ CLOSED by PR-6G (2026-05-24).** Static `tests-940+` pill replaced with a **live GitHub Actions CI badge** + a conservative `tests-1300+` count — rounds *down* from the 1,386 `def test_` floor (1,685 collected via `pytest --collect-only`), so it can't be called an overstatement by either measure. Counts reconciled across README + CONTRIBUTING + AGENTS; `skills` 75→76, `lines` 58K→67K. Guarded by `tests/test_readme_investor.py`. (Expanding CI to run the full suite = F-4, separate. The `features-400+` badge links to FEATURES.md and was left as-is.)

**What's missing:** Truthful representation of automated test coverage. README line 15 displays `tests-940+` badge. Verified count: **873 `def test_` functions in 54 test files** (`grep -rE "^def test_|^    def test_" tests/test_*.py | wc -l`). The CI workflow at `.github/workflows/ci.yml` only runs **four** of those files: `test_skill_imports.py`, `test_skill_contracts.py`, `test_oauth_provider.py`, `test_retry.py`. The remaining 50 test files are NOT run in CI.

CLAUDE.md §9 *Testing* claims "600+ tests collected" — actual count 873 (more, not fewer). CONTRIBUTING.md line 75 says "168+ pytest tests" (stale from an earlier release). Three different numbers (168, 600, 940) exist in-repo describing the same artifact.

**Why investors / enterprise customers expect it:**
- Inflated metric claims are the #1 cheap-tell at a YC interview. The partner running technical DD will read the README, then look at `ci.yml`, see four files, and the credibility crater takes 90 seconds.
- "Tests pass" is meaningless if the badge is hand-typed and not generated by a CI status check. Modern OSS convention: badges link to actual workflow runs (`shields.io/github/actions/workflow/status/...`).

**Recommended fix:**
1. Reconcile the count: pick one source of truth (`pytest --collect-only -q | tail -1`) and write a `scripts/test_count.sh` that the README badge URL pulls live. Or just say "850+" and round down.
2. Replace the static green `tests-940+` badge with a real GitHub Actions workflow status badge (`![CI](https://github.com/AVADSA25/codec/actions/workflows/ci.yml/badge.svg)`).
3. Expand `ci.yml` to actually run more of the suite (see F-4).
4. Update CONTRIBUTING.md "168+" → current number.
**Effort:** S (30 min for badge + reconciliation; M if expanding CI per F-4).

---

### F-4 — CI runs <10% of the test suite [HIGH]

**What's missing:** `.github/workflows/ci.yml` runs exactly 4 test scripts on push/PR to main:
```yaml
- run: python tests/test_skill_imports.py
- run: python -m pytest tests/test_skill_contracts.py -v
- run: python -m pytest tests/test_oauth_provider.py -v
- run: python -m pytest tests/test_retry.py -v
```
The other 50 test files — including `test_agent_runner.py` (46 tests), `test_agent_plan.py` (42 tests), `test_full_product_audit.py` (121 tests), `test_security.py` (25 tests), `test_destructive_consent.py` (18 tests), `test_audit_envelope.py` (18 tests), `test_dashboard_api.py` (18 tests) — are not gated on PR merge. A regression in any of these lands on `main` undetected.

No lint (ruff, flake8, black). No type check (mypy, pyright). No coverage report (coverage.py + codecov). No dependency scan (pip-audit, Dependabot config not visible). No Python version matrix (CI is 3.13 only; README claims 3.10+).

**Why investors / enterprise customers expect it:**
- Standard OSS hygiene baseline: `pytest -q && ruff check && mypy --ignore-missing-imports`.
- An enterprise buyer's security team will inspect the workflow file as part of supply-chain due diligence. "Most of the tests don't run in CI" is a finding on that questionnaire.
- The `tests-940+` badge becomes truthful the moment all 873 actually gate the merge.

**Recommended fix:**
1. Add a `full_tests` job to `ci.yml` running `python -m pytest tests/ --ignore=tests/test_smoke.py -q` with `continue-on-error: false` once the 20 documented pre-existing failures are either fixed or marked `@pytest.mark.xfail`.
2. Add `ruff check .` and `ruff format --check .` jobs.
3. Add `coverage run -m pytest && coverage report --fail-under=60` (start at 60%, raise quarterly).
4. Add `.github/dependabot.yml` for weekly Python + GitHub Actions updates.
5. Test matrix: Python 3.10, 3.11, 3.12, 3.13 (README says 3.10+; macOS Sonoma ships 3.12).
**Effort:** M (half-day for the additions; pruning the 20 pre-existing failures may be a separate effort).

---

### F-5 — No release tagging discipline; only 2 git tags exist [HIGH]

**What's missing:** `git tag -l` returns:
```
pre-dashboard-redesign
v3.0.0
```
CHANGELOG.md documents 6+ versioned releases (v2.0, v2.1.0, v2.1.1, v2.2.x, v2.3.0 with date 2026-05-13) — none of them are tagged in git. GitHub Releases page is therefore empty (no downloadable artifacts, no per-release changelog rendering, no SemVer history).

The engine badge says `engine: CODEC v2.3` while the only versioned tag is `v3.0.0` — version drift between the engine string, the CHANGELOG, and the tag.

**Why investors / enterprise customers expect it:**
- Enterprise procurement requires a "what version am I deploying" answer. Without tags, the answer is "whatever HEAD is" — not acceptable for managed deployments.
- YC partners often ask "how fast do you ship?" — release tags are the visible cadence proof. A 3-month gap with no tags reads as either dead or unstructured.
- A future paid Mac app build needs a versioning scheme that maps installer → source commit → CHANGELOG entry. SemVer tags are the bridge.

**Recommended fix:**
1. Tag every CHANGELOG entry retroactively: `git tag -a v2.3.0 <commit-of-2026-05-13> -m "..." && git push --tags`.
2. Resolve the v2.3 vs v3.0.0 confusion — pick one. If product brand is v2.3 and engine is v3, document this split.
3. Adopt **Keep a Changelog** format strictly (CHANGELOG.md is mostly there but missing "[Unreleased]" header and `## [Version] - YYYY-MM-DD` heading prefix that release-please / changelog-tooling expects).
4. Enable GitHub Releases — autogenerate from tag + CHANGELOG section. Attach a tarball + zip per release.
**Effort:** S (1-2 hours).

---

### F-6 — No CODE_OF_CONDUCT.md [HIGH]

**What's missing:** Standard OSS health file absent. GitHub's "Community Standards" check (visible at `github.com/AVADSA25/codec/community`) shows this as a missing checkmark.

**Why investors / enterprise customers expect it:**
- The "Insights → Community Standards" page is the first place a YC partner clicks after reading the README. Missing items show as red Xs.
- Enterprise contributors (devs at companies who want to PR a skill) need to know there's a code of conduct before they can get internal legal approval to contribute. Without one, "we can't contribute to that repo" is a frequent block.

**Recommended fix:** Copy the [Contributor Covenant 2.1](https://www.contributor-covenant.org/version/2/1/code_of_conduct/) into `CODE_OF_CONDUCT.md`. 5-minute change. Substitute enforcement contact (e.g. `conduct@avadigital.ai`).
**Effort:** S (15 min).

---

### F-7 — No FUNDING.yml / GitHub Sponsors surface [HIGH]

**What's missing:** `.github/FUNDING.yml` does not exist. README §"Support the Project" has a PayPal link only. GitHub's Sponsors button is therefore not visible on the repo page.

**Why investors / enterprise customers expect it:**
- For a pre-YC OSS project with growing stars (22 → 51), GitHub Sponsors is the conventional path-to-revenue signal. Its absence reads as "no commercial intent visible to a reviewer."
- Paid Mac app launch (per Mickael's brief) needs a visible monetization story BEFORE launch — Sponsors is the warm-up.

**Recommended fix:** Add `.github/FUNDING.yml`:
```yaml
github: [AVADSA25]
custom: ["https://paypal.me/avadsa25", "https://avadigital.ai"]
```
And/or set up GitHub Sponsors for the org. Add the "Sponsor" button to README header.
**Effort:** S (15 min for file; setting up Sponsors org takes ~30 min through GitHub UI).

---

### F-8 — No live demo URL, no animated GIF in the first viewport [HIGH]

**What's missing:** README has a YouTube thumbnail (line 23-29) but no GIF visible before scrolling. The YT link is `https://www.youtube.com/watch?v=OEXxvxA0_AE` — quality unverified by this audit. No live demo URL (e.g. opencodec.org/demo with a hosted sandbox).

The "9 Products" section is dense walls of text. A 30-second-attention-span YC partner who lands here from a tweet does NOT see CODEC do anything until they click through to YouTube.

**Why investors / enterprise customers expect it:**
- README first viewport (above the fold on a 1080p screen, ~30 lines) is the single most-trafficked surface a repo has. Anthropic's own MCP repo, Cursor's repo, Open Interpreter's repo, Aider's repo — all open with an animated GIF showing the product *in motion* within the first 15 lines.
- Voice control is impossible to convey in text. *Show* "Hey CODEC, click the Submit button" → screen-record → GIF in README is the single highest-leverage change in this whole audit.
- Stranger-from-Hacker-News test: a stranger lands on the repo, can they understand what CODEC does in 30 seconds? **Currently: no**, because they need to either click through to YouTube (lossy) or scroll through 9 product sections.

**Recommended fix:**
1. Record a 15-30s GIF showing the vision-mouse-click flow ("Hey CODEC → click the Submit button → mouse moves → click"). Place it directly above the "What This Is" heading.
2. Optionally a second GIF below for the MCP flow ("Claude desktop says 'use CODEC to check my calendar' → calendar opens").
3. Add a hosted demo: a recorded interactive walkthrough at opencodec.org/demo or an Asciinema cast for the CLI install.
**Effort:** M (half-day to record, edit, optimize GIFs to <2MB each).

---

### F-9 — Top-of-README value prop is not stranger-readable in 30 seconds [HIGH]

> **✅ CLOSED by PR-6G (2026-05-24).** README now leads with a one-sentence value prop ("CODEC turns a Mac into a voice-controlled AI workstation that runs 100% on your machine…") above the badges, before "What This Is". (Demo GIF in the first viewport = F-8, handoff to Mickael.)

**What's missing:** Top of README is identity-and-credentials heavy:
- 2 logos / titles / taglines (lines 1-9)
- 6 badges (line 12-19)
- YouTube thumbnail (line 22-29)
- 3-paragraph "What This Is" (line 33-41) — uses words like "give it a brain", "ears", "voice", "eyes" before stating the function.

A stranger from Hacker News lands here. They want to know in 5 seconds: *what does this DO and WHY is it different?* The answer is buried at line 39: "It listens, sees the screen, speaks back, controls apps, writes code, drafts messages, manages Google Workspace — and when it doesn't know how to do something, it writes its own plugin and learns."

That sentence — moved to line 1-2 — *is* the value prop. The brand reveal can come after.

**Why investors / enterprise customers expect it:**
- The "Lucy"-style differentiator (voice-controlled local agent, multi-agent collaboration via MCP) is one of the strongest cards in CODEC's deck. It needs to land in the first 100 words, not the second screen.
- YC application's "what does your company do?" field is 50 chars. A README that compresses to that is a README that converts.

**Recommended fix:**
Rewrite top of README to lead with the one-sentence value prop:
> **CODEC turns a Mac into a voice-controlled AI workstation that runs 100% on your machine.** Speak. See the screen. Click anywhere by voice. Run agent crews. Plug into Claude, Cursor, or VS Code as an MCP server. Open source, MIT, no cloud required.

Then GIF. Then 3 bullet points (the unique angles). Then the rest.
**Effort:** S (1-2 hours of writing + iteration).

---

### F-10 — Unique angles per Mickael's brief are present but underweighted [HIGH]

> **✅ CLOSED by PR-6G (2026-05-24).** New "## Why CODEC, not the alternatives" section near the top with the three moat bullets (vs Open Interpreter/Aider, vs Cursor/Claude Desktop via MCP-as-server, vs CrewAI/LangChain via the zero-dep runtime) + the five-way combination framing.

**What's missing:** The four positioning angles from the brief are *in* the README but not prominent:

| Angle | Where it appears | Visibility |
|---|---|---|
| "Talk to other agents like Lucy" (agent-to-agent via MCP) | Not explicitly. README §MCP Server mentions "Use CODEC to check my calendar" but the **agent-to-agent** framing is missing | Low |
| "Claude can use CODEC with MCP to get functions it doesn't have natively" | §MCP Server (line 418-447) + "What this unlocks (that Claude alone can't do)" subsection | Medium — present but buried at line ~440 |
| Local-first, voice-controlled, sovereign positioning | Title + "What This Is" paragraph | High |
| Self-writing skills | §Vibe (line 124-129) — "Skill Forge takes it further" + Pilot's `pilot_{slug}.py` auto-compile | Medium — split across two product sections |
| Full agent runtime without CrewAI / LangChain | §What CODEC replaced table (line 322-336) — "CrewAI + LangChain → Chat — 795-line agent framework, zero dependencies" | Medium |

The README sells *features* well. It does not sell *positioning* — the *why-this-not-that-thing-Anthropic-just-shipped* moat narrative.

**Why investors / enterprise customers expect it:**
- YC's #1 question: "what's your moat?". CODEC's moat is the combination: local-first + voice + MCP-as-server + self-writing skills + no-CrewAI runtime. None of OpenInterpreter / Aider / Goose / Continue have all five.
- "Why now?" — Anthropic shipped MCP in Nov 2024 (per training); CODEC's positioning as an MCP *server* (not just client) for any MCP host is the YC-grade insight that needs to be on screen by line 50.

**Recommended fix:** Add a new section right under the value prop: **"Why CODEC, not X"** with 3 bullets:
1. *"Open Interpreter / Aider write code in a terminal. CODEC controls the whole Mac by voice, including your IDE."*
2. *"Cursor and Claude desktop talk to LLMs. CODEC turns your Mac into a tool Claude (or Cursor, or VS Code) can use — via MCP. Claude gets your screen, your apps, your skills, your memory."*
3. *"CrewAI / LangChain orchestrate. CODEC orchestrates AND executes on your hardware, with a 795-line runtime that has zero dependencies on either."*
**Effort:** S (1 hour writing).

---

### F-11 — No pricing / paid tier mention in README [MEDIUM]

**What's missing:** Per Mickael's brief, CODEC is positioning for "paid Mac app launch." README says (line 39): "No subscription on the open-source build." That implies a paid build exists or is coming, but README does not say:
- What's in the paid tier (managed install? hosted LLM? team features? priority support?)
- Pricing range
- "Get notified when launched" email capture

**Why investors / enterprise customers expect it:**
- YC application asks "how do you make money?". Repo should answer this without the user clicking through to avadigital.ai.
- The "Support the Project" section (line 752-758) is donation-only (PayPal), not commercial. The bridge from "donate" to "buy" is missing.

**Recommended fix:** Add a 2-line **"Paid Tier (coming Q2 2026 or whenever)"** subsection under "Support the Project" with: tier name, what's included, price band, "join the waitlist → avadigital.ai/waitlist" link. Even if final pricing isn't set, signaling commercial intent is the point.
**Effort:** S (30 min, contingent on Mickael deciding the tier shape).

---

### F-12 — No CONTRIBUTORS / community-surface signals [MEDIUM]

**What's missing:** README mentions no Discord, no Twitter/X handle, no GitHub Discussions, no Reddit subreddit, no community surface beyond GitHub Issues. The repo currently has 2 contributors per the brief.

**Why investors / enterprise customers expect it:**
- Community velocity = product velocity at OSS stage. A YC partner asks "where's the community talking?" — repo's answer should be a Discord invite at the top.
- Open Interpreter, Aider, Continue, Goose all have Discord servers linked in README header — that's the convention.
- 22 → 51 stars in some period is good momentum; converting them to an active community requires a place to gather that isn't GitHub Issues.

**Recommended fix:**
1. Spin up a Discord server (free, 30 min setup). Add the invite to the badge row at top of README.
2. Enable GitHub Discussions on the repo (Settings → Features → Discussions). Use it for Q&A and showcase.
3. If `@avacodec` / `@opencodec` / `@codecai` Twitter handle exists, add to README header. If not, register one before the next demo video drop.
**Effort:** S (1 hour for Discord + Discussions toggle).

---

### F-13 — No investor one-pager or pitch deck in docs/ [MEDIUM]

**What's missing:** `docs/` has 32+ files heavy on engineering (PHASE*-DESIGN.md, MCP_HTTP_SETUP.md, API.md) and zero investor-facing artifacts:
- No `docs/PITCH.md` or `docs/ONE-PAGER.md`
- No `docs/VISION.md` or `docs/WHY-NOW.md`
- No deck (PDF or Pitch.com / Tome link)
- No `docs/BUSINESS.md` describing target market, monetization, competitive landscape

**Why investors / enterprise customers expect it:**
- YC partners frequently read `docs/` after the README. An investor-ready repo has both engineering depth AND business-narrative artifacts. Asana, Linear, and even Rust-based projects like Tauri have this split.
- An enterprise procurement team looking at CODEC for internal deployment will need a "Why this vendor?" doc — a one-pager that's NOT the README.

**Recommended fix:** Create `docs/ONE-PAGER.md` (1 page max) covering:
- **Problem:** AI assistants don't control your computer. Cloud agents don't run private. CrewAI is overhead.
- **Solution:** Voice-first local-first Mac agent with MCP-server tier-1 support.
- **Market:** macOS-native power users + enterprise teams wanting private agent infra.
- **Why now:** MCP standardized Nov 2024. Anthropic / Cursor / VS Code all support it. Local LLMs (Qwen 3.5 / Llama 3.3) hit production quality 2025.
- **Traction:** stars, commits, releases, integrations.
- **Team:** founder bio.
- **Ask:** what you're raising / building toward.

Also add `docs/VISION.md` (3 pages) for the longer narrative.
**Effort:** M (full day for the writing).

---

### F-14 — Architecture diagram exists but is buried [MEDIUM]

> **✅ CLOSED by PR-6G (2026-05-24).** New top-level "## Architecture" section (right before Quick Start): condensed mermaid topology + 3 topology bullets (inbound-private, gated+audited, swappable LLM) + a direct link to `docs/ARCHITECTURE.md`.

**What's missing:** `docs/ARCHITECTURE.md` *exists* (and is well-written, mermaid diagram, ~40 lines reviewed). README does NOT link to it from the body — only an indirect path through the "Project Structure" block at line 614+.

**Why investors / enterprise customers expect it:**
- Enterprise security reviewers want to see system topology before any technical detail. An architecture diagram answers "what processes run, what talks to what, what's exposed to network" in a single image.

**Recommended fix:** Add an "## Architecture" section to README at the level of "## Quick Start", with:
- The mermaid diagram from ARCHITECTURE.md inlined (or as a PNG export)
- A 3-paragraph topology summary
- A link to `docs/ARCHITECTURE.md` for the full detail
**Effort:** S (1 hour).

---

### F-15 — `requirements.txt` is minimal; no `pyproject.toml` / no lock file [MEDIUM]

**What's missing:** `requirements.txt` lists 8 dependencies with `>=` floors and no upper bounds. No `pyproject.toml`. No `poetry.lock` / `uv.lock` / `requirements-lock.txt`. No `setup.cfg`.

For enterprise installs and reproducible builds, this is a yellow flag:
- "Why was build X different from build Y?" — because pypi shipped pynput 1.8 between the two installs.
- pip-audit cannot pin a known-good snapshot.
- No Python project metadata (name, version, author, license — version is hardcoded in CHANGELOG/README, not introspectable via `pip show`).

**Why investors / enterprise customers expect it:**
- Enterprise package teams require a lock file for any installed package. Without it, reproducibility is impossible.
- An installable Python package needs `pyproject.toml` per [PEP 621](https://peps.python.org/pep-0621/). The future paid Mac app likely bundles CODEC as a pinned snapshot — needs lock provenance.

**Recommended fix:**
1. Migrate to `pyproject.toml` with `[project]` table (name, version, deps, license=MIT, urls).
2. Generate a lock with `pip-compile` (pip-tools) or `uv pip compile requirements.in -o requirements.txt --generate-hashes`.
3. Commit the lock; CI installs from lock.
**Effort:** M (half-day, including resolving any version-range resolution issues).

---

### F-16 — Stray garbage file at repo root [LOW]

**What's missing / wrong:** There is a file at repo root named literally:
```
authlib google-auth-httplib2 --break-system-packages
```
(visible in the `ls` output, contains pip install error output captured into a filename).

**Why investors / enterprise customers expect it:**
- "Cleanliness of the root directory" is a 5-second tell. A YC partner reading the file list `ls` output sees this and forms a mental "are they paying attention?" judgment.
- A paid Mac app installer that bundles the repo would ship this file. Not user-visible, but a code-signing audit would flag it.

**Recommended fix:** `git rm "authlib google-auth-httplib2 --break-system-packages" && git commit -m "chore: remove stray file from pip install error"`.
**Effort:** S (60 seconds).

---

### F-17 — README + CONTRIBUTING.md test counts disagree (3 different numbers in repo) [LOW]

> **✅ CLOSED by PR-6G (2026-05-24).** One number (`1,300+`) across the README badge+body, CONTRIBUTING.md and AGENTS.md; the exact `1,386` functions / `1,685` collected appear in the bodies for transparency. Skills reconciled to 76 in the same files. (Same PR as F-3.)

**What's missing:** Three test-count claims live in the repo:
- README badge: `tests-940+`
- README line 653: `940+ pytest tests across 53 files`
- CLAUDE.md §9: `"600+ tests collected"`
- CONTRIBUTING.md line 75: `168+ pytest tests`

Verified count: 873 test functions across 54 files (`grep` of `def test_` in `tests/test_*.py`).

**Why investors / enterprise customers expect it:**
- Internal consistency is a credibility tell. A reader who notices the badge says 940 and the contributor guide says 168 either trusts the higher (and is later disappointed) or trusts the lower (and undervalues the work).

**Recommended fix:** Pick one number, update all three locations, ideally make the badge live-generated per F-3.
**Effort:** S (15 min).

---

### F-18 — No README mention of "agent-to-agent like Lucy" (Mickael's headline angle) [LOW]

> **🟡 PARTIALLY CLOSED by PR-6G (2026-05-24).** MCP section now has a "Bidirectional MCP — agent-to-agent" subsection: CODEC is **both an MCP client AND server**, two CODECs peer directly = agent-to-agent on the open protocol. The generic angle is stated. **Open for Mickael:** whether "Lucy" is a separate brand to *name* in the README (tracked in `docs/HANDOFF-MICKAEL.md`).

**What's missing:** Per the audit brief, one of Mickael's headline unique angles is *"Talk to other agents like Lucy"* — i.e. multi-agent agent-to-agent collaboration via MCP. README's MCP section frames the bridge as CODEC → Claude (Claude consumes CODEC tools), not CODEC → other-agent (CODEC peers with Lucy or another MCP-speaking agent).

If "Lucy" is a separate product / persona (the codename appears in CHANGELOG context as a quality reference and on `lucyvpa.com` in the Pilot tunnel hostname), the bidirectional MCP story is unstated.

**Why investors / enterprise customers expect it:**
- "Agents talking to agents" is the 2026 narrative AI investors want to hear. CODEC has the substrate for it via MCP (acts as both client AND server). Not stating that in README leaves the angle on the table.
- Open Question for Mickael: is Lucy a separate product brand or an instance of Sovereign AI Workstation deployed for a specific user (Mickael himself, his clients)?

**Recommended fix:** Add a paragraph to §MCP Server: *"CODEC is both an MCP client AND an MCP server. It consumes tools from any MCP host (Claude, Cursor, VS Code) AND exposes its 75 skills to any MCP client. Two CODECs on two Macs can peer through MCP — agent-to-agent collaboration on the open protocol Anthropic standardized."* If Lucy is a separate brand, link to it.
**Effort:** S (30 min, contingent on Mickael clarifying the Lucy positioning).

---

### F-19 — No Mac app distribution surface — code signing, notarization, installer [LOW]

**What's missing:** Per Mickael's brief, "paid Mac app launch" is planned. Repo has `install.sh` (shell installer) and the `swift-overlay/` directory for the AX bridge, but:
- No `.dmg` build pipeline in CI
- No documented Apple Developer signing identity
- No notarization step
- No `entitlements.plist` visible
- No Sparkle (macOS auto-update framework) integration

A paid Mac app cannot ship to general consumers without code signing + notarization (Gatekeeper will block it). Building toward that surface starts now.

**Why investors / enterprise customers expect it:**
- A "paid Mac app" with a Developer ID + notarization is the difference between $0 GTM cost and "users see scary warnings, conversion drops 80%".
- A YC partner pitch that says "we're launching a paid Mac app Q2" but has no signing pipeline triggers the obvious follow-up: "what does it look like to actually ship one?"

**Recommended fix:** Out of scope for a public-repo audit — but flag this as a Phase 4 work item:
1. Acquire Apple Developer Program membership ($99/yr).
2. Create `Developer ID Application` certificate.
3. Add GitHub Actions step: build `.dmg`, sign with cert (via `codesign`), notarize (via `xcrun notarytool`), staple ticket.
4. Use Sparkle for in-app update channel.
**Effort:** L (multi-day; needs Mickael's Apple Developer account and CI secrets).

---

## Competitive comparison

| Feature | **CODEC** | Open Interpreter | Aider | Goose (Block) | Continue | Khoj | Cursor (commercial ref) |
|---|---|---|---|---|---|---|---|
| Local-first (no cloud needed) | ✓ | partial (LLM choice) | ✗ (LLM API required) | partial | ✗ (LLM API required) | ✓ | ✗ |
| Voice control (wake word, STT) | **✓ (Whisper)** | ✗ | ✗ | ✗ | ✗ | partial (chat input only) | ✗ |
| TTS / voice output | **✓ (Kokoro)** | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| Vision-based mouse control | **✓ (UI-TARS)** | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| MCP server (acts AS MCP for any client) | **✓ (stdio + HTTP, OAuth 2.1)** | ✗ | ✗ | partial (consumes MCP) | partial (consumes MCP) | ✗ | partial (commercial MCP) |
| MCP client (consumes other tools) | ✓ | ✗ | ✗ | ✓ | ✓ | ✗ | ✓ |
| Self-writing skills / plugins | **✓ (Skill Forge + nightly drafter)** | ✗ | ✗ | partial (extensions) | partial (slash cmds) | ✗ | partial |
| Multi-agent / Crew runtime | **✓ (12 crews, 795-line own runtime)** | ✗ | ✗ | ✓ | ✗ | ✗ | ✗ |
| No CrewAI / LangChain dependency | **✓ (zero)** | n/a | n/a | uses own | n/a | n/a | n/a |
| Drop-a-project autonomous mode | **✓ (Phase 3 substrate)** | ✗ | ✗ | ✓ | ✗ | ✗ | ✗ |
| Mac-native (Swift overlay, AX bridge) | **✓ (swift-overlay, ax_bridge)** | (cross-platform) | (cross-platform) | (cross-platform) | (cross-platform) | (cross-platform) | (cross-platform, Mac primary) |
| Browser automation with teach-by-doing | **✓ (CODEC Pilot)** | partial | ✗ | ✗ | ✗ | ✗ | ✗ |
| iMessage / Telegram bridges | **✓** | ✗ | ✗ | ✗ | ✗ | partial (chat surface) | ✗ |
| Full audit log + 16 categories | **✓** | ✗ | ✗ | partial | ✗ | partial | partial |
| OAuth 2.1 + Touch ID + TOTP auth | **✓** | ✗ | ✗ | ✗ | ✗ | partial | partial |
| Open source / MIT | ✓ | ✓ (AGPL!) | ✓ (Apache 2.0) | ✓ (Apache 2.0) | ✓ (Apache 2.0) | ✓ (AGPL) | ✗ (commercial) |
| GitHub stars (rough order Q2 2026 training data) | 51 (growing) | ~50k+ | ~20k+ | ~3k | ~20k | ~13k | n/a (closed) |
| Number of dedicated contributors | 2 | 50+ | 30+ | 20+ | 100+ | 30+ | n/a |
| Discord / live community | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| CI runs full test suite | ✗ (4 of 54 files) | ✓ | ✓ | ✓ | ✓ | ✓ | n/a |

### Where CODEC wins (unambiguously, today)

1. **Voice-first by default.** Wake word, vision mouse, dictation, voice call — none of the comparables ship this stack.
2. **Mac-native depth.** Swift overlay, AX bridge, Kokoro TTS on Apple Silicon, F-key hotkeys, AppleScript bridges to iMessage. Open Interpreter et al. are cross-platform-OK; CODEC is Mac-excellent.
3. **MCP as both server AND client.** CODEC's killer story: any MCP host (Claude, Cursor, VS Code) gains 75 of CODEC's skills as tools. Goose and Continue consume MCP but don't *expose* skill catalogs to other agents the way CODEC does.
4. **Substrate depth for autonomy.** Phase 3 (Plan + Permission + Runner + Messaging) with audit/plugin/hooks/ask-user/stuck-detection/step-budget is more architecturally complete than Goose's autonomous mode and more than Open Interpreter offers. The "permission manifest + plan_hash + resume-after-restart" combination is unusual.
5. **Self-writing skills.** Skill Forge + nightly drafter + `codec_self_improve` is genuinely novel — no comparable does runtime-extensible LLM-authored plugins with a human review gate.

### Where CODEC loses

1. **Community size and contributor velocity.** 2 contributors vs 30-100. This is a stars-and-time problem, not a fixable-overnight problem — but every other gap on this list is *visible* via the absence of community surfaces (F-12).
2. **Cross-platform.** macOS-only is a deliberate strategic bet. Open Interpreter / Aider work on Linux servers and Windows. CODEC's TAM is materially smaller until Linux/WSL ship (`What's Coming` line 729-730).
3. **IDE-native.** Continue, Aider, Cursor live *inside* the editor. CODEC has Vibe (Monaco in browser) but that's not where most devs work.
4. **Established install base.** Open Interpreter has Pyodide + Pyodide-on-Mac. Aider is `pip install aider`. CODEC needs `install.sh` + setup wizard + LLM + Whisper + Kokoro + vision — heavier install footprint.
5. **CI hygiene visible to reviewers.** 4 test files run on PR vs full suites elsewhere. See F-4.

---

## Investor / enterprise readiness scorecard

| Item | Status | Evidence |
|---|---|---|
| README sells the product in 30 seconds | ✗ | Top is logo + 6 badges + YouTube; value-prop sentence is at line 39. F-9. |
| Demo video in README | ⚠ partial | YouTube thumbnail (line 22), no GIF in first viewport. F-8. |
| Live demo URL (opencodec.org) | ⚠ partial | Link in header, content not audited |
| Quickstart that works on a fresh Mac | ✓ | `install.sh` + setup wizard; well-documented 9 steps |
| Architecture diagram in docs/ | ⚠ partial | `docs/ARCHITECTURE.md` exists with mermaid; not linked from README body. F-14. |
| CI/CD with green status badges | ⚠ partial | `ci.yml` exists, badge is hand-typed not workflow-status. F-3, F-4. |
| Test suite with coverage report | ⚠ partial | 873 tests exist, only 4 files run in CI, no coverage gate. F-4. |
| SECURITY.md with vuln disclosure policy | ✗ | Does not exist. F-1. |
| CONTRIBUTING.md | ✓ | Present, slightly stale on test count. F-17. |
| CODE_OF_CONDUCT.md | ✗ | Does not exist. F-6. |
| LICENSE (MIT confirmed) | ✓ | MIT, AVA Digital LLC, 2026 |
| CHANGELOG with semver tags | ⚠ partial | CHANGELOG good (Keep-a-Changelog-ish); only 2 git tags exist for 6+ versions. F-5. |
| Community surface (Discord / Discussions) | ✗ | No Discord linked; Discussions toggle unknown. F-12. |
| FUNDING.yml / GitHub Sponsors | ✗ | Does not exist; PayPal in README only. F-7. |
| Investor one-pager or pitch deck | ✗ | No `docs/ONE-PAGER.md` / `VISION.md`. F-13. |
| Privacy / data-handling page | ✗ | No `PRIVACY.md`; README §Privacy & Security covers tech only. F-2. |
| AI Act / GDPR alignment statement | ✗ | Not addressed. F-2. |
| Pricing / paid tier story in README | ⚠ partial | "No subscription on the open-source build" implies paid; not described. F-11. |
| Unique angles visible up-top (multi-agent, MCP server, self-writing) | ⚠ partial | All present in README, none in first viewport. F-9, F-10, F-18. |
| Pinned issues / roadmap visible | ✓ | README "What's Coming" section is good |
| `pyproject.toml` / lock file | ✗ | `requirements.txt` only, no lock. F-15. |
| Mac app code-signing / notarization pipeline | ✗ | Not visible. F-19. |
| Repo cleanliness (no stray files) | ✗ | Garbage filename at repo root. F-16. |

**Score: 6.5 / 10.** Strong substance, weak shop-window. Two days of "outside layer" work (F-1, F-2, F-3, F-6, F-7, F-8, F-9, F-12) closes most of the visible gap.

---

## Pre-audit finding verification

| ID | Status | Evidence |
|----|--------|----------|
| **P-4: No tests visible in repo root. Industry baseline ~70% line coverage. CLAUDE.md §9 claims "600+ tests collected" — verify with `pytest --collect-only`.** | **partial — REFUTED on existence, CONFIRMED on coverage gap** | Tests DO exist: `tests/` directory has 54 `test_*.py` files containing **873 `def test_` functions** (verified via `grep -rE "^def test_\|^    def test_" tests/test_*.py | wc -l`). However: (a) The CLAUDE.md "600+" claim is undercounted — actual is 873. (b) The README "940+" badge is overcounted — actual is 873. (c) Coverage tooling (coverage.py / codecov) is not configured. (d) `.github/workflows/ci.yml` only runs **4 test files** of the 54 — so on PR merge, ~90% of the tests are not gated. The "industry baseline ~70% line coverage" cannot be verified because no coverage tool is configured. See F-3, F-4, F-17. |

---

## Open Questions for Mickael

1. **Lucy positioning.** Is "Lucy" a separate brand (e.g. a hosted CODEC instance for clients), an internal codename for *your* CODEC deployment, or a planned product? The "agent-to-agent like Lucy" unique angle and the `lucyvpa.com` tunnel hostname need a clear public framing — currently absent from README. (F-18)

2. **Paid tier shape.** What's in the paid Mac app vs the OSS build? Options: (a) Managed installer + zero-config, (b) Hosted LLM included, (c) Team / multi-Mac sync, (d) Enterprise SSO + audit export, (e) Priority support / SLA. The README needs a one-liner once decided. (F-11)

3. **Target enterprise verticals.** Sales (per existing `apollo:` skills?), finance (per `finance:` skills?), legal, dev? An enterprise GTM with no vertical focus is harder; one named beachhead is easier. The skill plugin list (`marketing:*`, `sales:*`, `finance:*`, `legal:*` in the available skills) suggests breadth — pick one to lead with.

4. **YC application timing.** S26 batch (Apr 2026 deadline already passed)? W27? Or applying to a non-YC accelerator (Antler, EF, Z-Fellows, Bloomberg Beta)? The "investor-grade" target is shaped by which.

5. **Apple Developer account status.** Do you already have a Developer ID Application certificate? If not, that's a 1-week procurement lag-time that should start before the Mac app launch date. (F-19)

6. **Trademark / brand strategy.** "Sovereign AI Workstation" and "CODEC" — are either trademarked? CODEC is a heavily-overloaded generic term (audio/video codecs); the engine brand may be hard to defend. The product brand "Sovereign AI Workstation" is cleaner but long. Worth a 1-hour conversation with a trademark lawyer before paid launch.

7. **AVA Digital LLC jurisdiction.** Spanish entity for EU privacy compliance ease, US Delaware C-corp for YC funding, or both via a flip? YC requires Delaware C-corp at funding. If LLC is the only entity, the flip is a known $5-15k legal exercise.

---

## Files reviewed

**Repo root:**
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/README.md` (full, 775 lines)
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/CONTRIBUTING.md`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/CHANGELOG.md` (first 60 lines + spot checks)
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/LICENSE`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/AGENTS.md` (front-door context, full read via system reminder)
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/FEATURES.md` (first 30 lines)
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/CLAUDE.md` (via system reminder; same as AGENTS.md in this repo)
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/requirements.txt`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/install.sh` (first 40 lines)
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/pytest.ini`

**OSS health / .github:**
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/.github/workflows/ci.yml`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/.github/PULL_REQUEST_TEMPLATE.md`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/.github/ISSUE_TEMPLATE/bug_report.yml`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/.github/ISSUE_TEMPLATE/feature_request.yml`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/.github/ISSUE_TEMPLATE/config.yml`

**docs/:**
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/docs/ARCHITECTURE.md` (first 40 lines)
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/docs/API.md` (first 20 lines)
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/docs/MCP_HTTP_SETUP.md` (first 20 lines)
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/docs/known-issues.md` (first 40 lines)
- Full file listing of `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/docs/` (32 files)
- Full file listing of `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/docs/screenshots/` (15 PNGs)
- Empty audits subfolder verified: `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/docs/audits/`

**tests/:**
- Directory listing of `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/tests/` (54 files matching `test_*.py`)
- Test function count verified via grep: 873 `def test_` functions

**skills/:**
- Listing of `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/skills/` (76 files; 70 with `SKILL_MCP_EXPOSE = True`)
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/skills/_template.py`

**Git:**
- `git tag -l` — returned 2 tags
- `git status` (clean per session start info)
- Recent commits via session context (e92caa3, 9ed6dd8, 34c7f82, 478a58b, c4f77ac)

**Out of scope / not reviewed:**
- Source `codec_*.py` modules (Audit F is positioning, not code)
- `routes/` HTTP handlers
- Actual content of `opencodec.org` and `avadigital.ai`
- The YouTube demo video at `https://www.youtube.com/watch?v=OEXxvxA0_AE` (asserted to exist; quality unverified)
- `ecosystem.config.js` (referenced indirectly)

---

## Recommended fix sequence (2-day burn-down to 8.5/10)

**Day 1 morning (3 hours):**
- F-1 SECURITY.md (1h)
- F-6 CODE_OF_CONDUCT.md (15min)
- F-7 FUNDING.yml (15min)
- F-16 remove stray garbage file (5min)
- F-17 reconcile test counts (15min)
- F-3 swap static `tests-940+` badge for workflow-status badge (30min)
- F-12 spin up Discord + enable GitHub Discussions + add to README header (60min)

**Day 1 afternoon (4 hours):**
- F-8 record + edit a 20-second voice-mouse-click GIF, place in first viewport (3h)
- F-9 rewrite top of README to lead with value prop (1h)

**Day 2 morning (4 hours):**
- F-2 PRIVACY.md with EU/AI Act statement (3h)
- F-10 add "Why CODEC, not X" comparison block to README (1h)

**Day 2 afternoon (3 hours):**
- F-13 docs/ONE-PAGER.md (2h)
- F-5 retroactively tag releases + enable GitHub Releases (1h)

**Day 3 (later, half-day):**
- F-4 expand CI to run full test suite + coverage gate
- F-14 inline architecture diagram in README
- F-11 paid-tier subsection (contingent on Mickael's pricing decision)

**Deferred / longer-horizon:**
- F-15 pyproject.toml migration (good to do; not blocking)
- F-18 Lucy positioning (contingent on Mickael's brand strategy)
- F-19 Mac app code-signing pipeline (Phase 4 work, separate from positioning audit)

---

**End of Audit F.**
