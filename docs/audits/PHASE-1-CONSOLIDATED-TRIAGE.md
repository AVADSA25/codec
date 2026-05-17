# PHASE 1 — CONSOLIDATED TRIAGE

**Date:** 2026-05-17
**Status:** 5 of 6 audits complete (A, C, D, E, F). Audit B (Projects + Pilot) is a placeholder pending Mickael's description.
**Mode:** Audit-only. No source files were modified by this triage. No fix PRs have been started.
**Predecessors:** mirrors the Intake Phase 3 consolidated-triage structure.

---

## 0. Headline

Five audits, ~103 findings, **20 CRITICAL**. The headline is Audit D's finding **D-1**: `SkillRegistry.load` invokes `exec_module` on any `.py` file in `~/.codec/skills/` with **no load-time safety check**. The dangerous-pattern AST check (`is_dangerous_skill_code`) only runs at the write gates, not at load. So "drop a file in `~/.codec/skills/`, wait for restart" is an RCE primitive. **Four independent paths can drop a file in that state today** — D-2 (`/api/forge`), D-3 (`/api/save_skill`), D-4 (MCP-HTTP-exposed `file_write`), D-5 (Step 9 `permission_gate` path-traversal).

D-4 is the most dangerous because it's **remote-reachable** from claude.ai via the OAuth-authenticated MCP HTTP transport, with a 30-day access-token TTL.

**Recommendation:** before sleeping, run STOP-GAP §1 (`pm2 stop codec-mcp-http cloudflared` and 3 other commands). That closes the remote vector tonight. Wave 1 patches the chain properly tomorrow. Waves 2-6 follow.

Other notable themes:
- **Audit C** found 5 reliability criticals — main `codec.py` daemon **ignores SIGINT/SIGTERM** (C-1), `~/.codec/pwa_response.json` and `notifications.json` and `pending_questions.json` have **inter-process file races** across PM2 daemons (C-2, C-3, C-4), and `_atomic_set_status` swallows all exceptions causing **agent state-machine desync** (C-5). The daemon-level reliability story is fragile in places.
- **Audit E** is structural: there is **zero** Apple distribution scaffolding in the repo. Shipping the paid Mac app is realistically 6-10 weeks of focused engineering + a 2-4 week Apple Developer enrollment lag. App Store is out (hard sandbox incompatibility); direct download with Developer ID signing + notarization is the only viable path.
- **Audit F** is positioning: the *product* is 8/10, the *shop-window* is 4/10. Two days of "outside layer" work (SECURITY.md, PRIVACY.md, a 20-second demo GIF, value-prop rewrite, Discord, accurate test-count badge) closes most of the visible YC/enterprise gap.
- **Audit A** found **~733 LOC of dead code** in two of the most-read files in the repo (`codec.py:240-513` orphan `build_session_script`, and `skills/codec.py` which is a 460-LOC stale fork of the main entry point that is NOT a valid skill). Two parallel skill-loading systems coexist (legacy `codec_core.load_skills` vs canonical `codec_dispatch.SkillRegistry`). 21 sites use the misleading `log.warning("Non-critical error: %s")` template to hide real failures.

Pre-audit findings P-1 and P-3 (system-prompt string-concat and SQL `ORDER BY+LIMIT` in `UPDATE`) **were REFUTED** — they are not in the code. Either fixed already, or misread from the README excerpt during plan drafting. P-2, P-4, P-5, P-6, P-7, P-8, P-9, P-10 confirmed or partially confirmed.

---

## 1. STOP-GAP: TONIGHT

**Operator-runnable mitigations. Run these before sleeping to neutralize the active RCE exposure. No PR required.** Listed in order of priority + safety; each item is independent.

### S-1 — Verify nothing has already been dropped (60 seconds, zero risk)
```bash
ls -la ~/.codec/skills/ ~/.codec/plugins/
```
Compare to your `<repo>/skills/` directory. Any unrecognized `.py` file in either user directory is suspect. If you find one, **don't delete it yet** — copy it elsewhere first for forensic analysis, then move it out of `~/.codec/skills/` and `~/.codec/plugins/`.

### S-2 — Kill the remote RCE vector (5 seconds, drops claude.ai MCP HTTP)
```bash
pm2 stop codec-mcp-http cloudflared
```
**Closes:** D-4 (MCP-HTTP-exposed `file_write` writes to `~/.codec/skills/`) and the remote OAuth-bearer reach for the entire MCP HTTP server.
**Costs:** claude.ai cannot reach CODEC tools over MCP until you restart. Local voice/chat/PWA all keep working.
**Note:** the audit also called out the `codec-mcp-http` service binding `0.0.0.0` and the OAuth state file being plaintext. Stopping the service drops the exposure window entirely.

### S-3 — Revoke any OAuth tokens that may have leaked (5 seconds)
```bash
rm ~/.codec/oauth_state.json
pm2 restart codec-mcp-http   # only if you re-enable it
```
**Closes:** any leaked 30-day access tokens. claude.ai (when you re-enable) will need to re-auth.
**Costs:** every existing MCP HTTP connection invalidated. Cheap to redo.
**CLAUDE.md §10 warning:** `oauth_state.json` is a "don't-touch zone" specifically because it kills all claude.ai connections. The point here is precisely that — if any token was harvested, this is the cure. Pair with S-2 so there's nothing to re-auth against until Wave 1.

### S-4 — Confirm no PM2 service is still listening on a public IP (10 seconds)
```bash
pm2 list
netstat -an | grep LISTEN | grep -E '8090|8091' | grep -v 127.0.0.1
```
**Reads:** if either dashboard (8090) or MCP HTTP (8091) appears with `*.8090` or `0.0.0.0:8090`, that port is bound on all interfaces. After S-2 the MCP-HTTP listener should be gone; the dashboard (8090) is still bound `0.0.0.0` per the hardcoded host in `codec_dashboard.py:3858`. If you're on a trusted LAN AND your `dashboard_token` is set with `auth_enabled=true` in config.json, the dashboard exposure is auth-gated for now. If `dashboard_token` is empty, see S-5.

### S-5 — Bind the dashboard to loopback only (2 minutes, 1-line code edit)
Edit `codec_dashboard.py:3858`:
```python
uvicorn.run(app, host="127.0.0.1", port=8090, ...)
# was: host="0.0.0.0"
```
Then:
```bash
pm2 restart codec-dashboard
```
**Closes:** D-7 — LAN-reachable RCE without authentication when no `dashboard_token` is set. After this edit, only `127.0.0.1` clients can reach the dashboard. Your PWA goes through Cloudflare → `127.0.0.1` so it still works.
**Costs:** any other Mac on your LAN that was hitting `http://<this-Mac>:8090` directly stops working. Rare in practice.

### S-6 — Disable `/api/forge` and `/api/save_skill` until Wave 1 (3 minutes, ~4 lines)
Edit `routes/skills.py`. At the top of both `forge_skill` and `save_skill` functions, add:
```python
return JSONResponse({"error": "Disabled pending Wave 1 hardening (D-2, D-3)."}, status_code=503)
```
Then:
```bash
pm2 restart codec-dashboard
```
**Closes:** D-2 (forge URL-fetch → LLM → skill write) and D-3 (direct skill write with weak validation).
**Costs:** the dashboard's "create skill from URL" and "save skill" buttons in the Skill Forge UI return 503. If you don't use them, no impact.

### S-7 — Add `~/.codec/skills/` and `~/.codec/plugins/` to `file_write` blocklist (5 minutes)
Edit `skills/file_write.py`, find the `_BLOCKED_ROOTS` set (per audit D-4) and add:
```python
os.path.expanduser('~/.codec/skills'),
os.path.expanduser('~/.codec/plugins'),
str(Path(__file__).parent),  # repo skills/ — adjust as needed
```
Then:
```bash
pm2 restart codec-dashboard codec-mcp-http
```
**Closes:** D-4's root cause. Even if S-2 is reverted later, the `file_write` skill refuses to drop files in skill directories.
**Costs:** none — skill files were not supposed to be writable through `file_write` anyway.

### S-8 — Patch D-1 with load-time AST check (10 minutes, single-function edit)
Edit `codec_skill_registry.py:137-157` (`SkillRegistry.load`). Before the `spec.loader.exec_module(mod)` call, add:
```python
from codec_config import is_dangerous_skill_code
try:
    src = Path(self.path).read_text(encoding="utf-8")
except Exception:
    src = ""
if src and is_dangerous_skill_code(src):
    log.warning(f"[skill_registry] {self.name} failed load-time AST check; refusing to load")
    return None
```
Then `pm2 restart` everything that loads skills (the full PM2 fleet — `pm2 restart all` is the brute-force option).

**Closes:** D-1 root cause — even if a malicious skill file is on disk (via any path), it won't execute on load.
**Caveats:** D-17 documents that `is_dangerous_skill_code` has bypass vectors (reflection, subclass-walking, `urllib.request`). This raises the bar significantly but is not a hermetic block. Wave 1 will replace it with a positive-allowlist + sandbox-exec.

### Priority order for tonight

If you have 5 minutes: **S-1 + S-2 + S-3**. These are pure operator commands, no code edit, and close the remote vector.

If you have 30 minutes: add **S-5 + S-6 + S-8**. Three small code edits, layered defense.

If you do all 8: you have layered defenses against D-1 / D-2 / D-3 / D-4 / D-7 until Wave 1 ships.

---

## 2. Findings counts

### By audit

| Audit | Total | Critical | High | Medium | Low | Headline |
|---|---:|---:|---:|---:|---:|---|
| **A — Code Quality** | 22 | 0 | 6 | 12 | ~4 | 733 LOC of dead code in two of the most-read files |
| **C — Reliability** | 22 | 5 | 9 | 6 | 2 | Main daemon ignores SIGINT/SIGTERM; 4 inter-process file races on `~/.codec/*.json` |
| **D — Security** | 22 | 5 | 7 | 7 | 3 | **Skill-load = RCE**; 4 enabling paths; 45% red-team bypass rate |
| **E — Apple App** | 18 | 7 | 6 | 3 | 2 | Zero Apple distribution scaffolding; sandbox-incompatible; 6-10 weeks of work |
| **F — Investor** | 19 | 3 | 7 | 6 | 3 | Substance 8/10, shop-window 4/10; 2-day burn-down possible |
| **B — Projects + Pilot** | TBD | TBD | TBD | TBD | TBD | Placeholder — pending Mickael's description |
| **Total (5 of 6)** | **103** | **20** | **35** | **34** | **14** | |

### CRITICAL findings — 20 total (lead the doc)

| ID | Audit | Title | Wave |
|---|---|---|---|
| **D-1** | Security | Skill registry lazy-load = RCE for anyone who can drop a `.py` file | **W1 — CLOSED ([#42](https://github.com/AVADSA25/codec/pull/42), `48ec5d5`)** |
| **D-2** | Security | `/api/forge` fetches arbitrary URL → LLM → writes skill, no review gate | **W1 — CLOSED ([#43](https://github.com/AVADSA25/codec/pull/43), `ff16664`)** |
| **D-3** | Security | `/api/save_skill` writes directly to skills/ with only substring check | **W1 — CLOSED ([#43](https://github.com/AVADSA25/codec/pull/43), `ff16664`)** |
| **D-4** | Security | `file_write` skill (MCP-exposed) can write to `~/.codec/skills/` | **W1** |
| **D-5** | Security | `permission_gate` accepts path-traversal via `fnmatch` (no realpath) | **W1** |
| C-1 | Reliability | `codec.py` daemon ignores SIGINT/SIGTERM; leaks sox + tkinter on every restart | W4 |
| C-2 | Reliability | `~/.codec/pwa_response.json` race conditions + no correlation_id | W4 |
| C-3 | Reliability | `notifications.json` has 3 writers with 3 different write semantics | W4 |
| C-4 | Reliability | `pending_questions.json` cross-process read-modify-write race | W4 |
| C-5 | Reliability | `_atomic_set_status` swallows `InvalidStatusTransition` → agent state desync | W4 |
| E-1 | Apple App | No `.app` bundle, no Info.plist, no bundle identifier | W5 |
| E-2 | Apple App | No code-signing pipeline | W5 |
| E-3 | Apple App | No notarization workflow | W5 |
| E-4 | Apple App | Sandbox incompatibility blocks Mac App Store distribution | W5 (decision doc) |
| E-6 | Apple App | Hardcoded `/usr/local/bin/python3.13` makes install non-portable | W5 |
| E-7 | Apple App | Node.js + PM2 dependency is unbundled and explicit | W5 |
| E-8 | Apple App | Multi-gigabyte ML model files have no distribution strategy | W5 |
| E-13 | Apple App | No Apple Developer Program enrollment evidence (4-week lag time) | W5 (start day 1) |
| F-1 | Investor | No SECURITY.md / vulnerability disclosure policy | W6 |
| F-2 | Investor | No public-facing privacy / data-handling statement | W6 |
| F-3 | Investor | README badges overstate verified state ("940+ tests" with 4 of 54 in CI) | W6 |

---

## 3. CRITICAL findings — same-day mitigation guidance

These 5 findings (D-1 through D-5) are the "do not let this slip to next week" set per Mickael's brief. The STOP-GAP §1 commands above are the same-day mitigations; the proper fixes land in Wave 1.

### D-1 — Skill registry lazy-load = RCE [CRITICAL]
**Location:** `codec_skill_registry.py:137-157` (`SkillRegistry.load`)
**Wave 1 fix:** run `is_dangerous_skill_code` at load-time **before** `spec.loader.exec_module`. Better: route every load through a stable signature check (manifest of approved hash → set of approved files). Best: run skills in a sandboxed subprocess by default (the wrapper exists in `codec_sandbox.py` — most paths don't use it).
**Same-day mitigation:** **S-8** above. Single function edit.
**Why critical:** four other paths reach this; any one is RCE. The fix here is the chokepoint.

### D-2 — `/api/forge` writes skill from URL fetch [CRITICAL]
**Location:** `routes/skills.py:71-201` (`forge_skill`)
**Wave 1 fix:** route `/api/forge` through `/api/skill/review` staging — never write directly to disk. Block private IP ranges + localhost in URL fetch (SSRF). Replace the 10-substring blocker with `is_dangerous_skill_code` (AST). Strong recommendation: drop the URL-fetch capability entirely — the threat-model gain is minimal.
**Same-day mitigation:** **S-6** above — disable the endpoint with a 503.
**Why critical:** RCE via prompt-injection from any URL the user (or a CSRF-tricked browser) tells the dashboard to fetch.

### D-3 — `/api/save_skill` writes directly with weak validation [CRITICAL]
**Location:** `routes/skills.py:14-28` (`save_skill`)
**Wave 1 fix:** route through `/api/skill/review` → `/api/skill/approve` flow. Move the AST check to LOAD time (D-1) as well so it's defense-in-depth.
**Same-day mitigation:** **S-6** above — same 503 disable.
**Why critical:** RCE on next restart from any caller with dashboard auth. With D-7 (no auth on default config), this is unauthenticated LAN-reachable.

### D-4 — `file_write` skill can write `~/.codec/skills/` [CRITICAL]
**Location:** `skills/file_write.py` + `codec_config.py:_HTTP_BLOCKED`
**Wave 1 fix:** add `~/.codec/skills`, `~/.codec/plugins`, `<repo>/skills` to `file_write._BLOCKED_ROOTS`. Better: explicit allowlist of write directories (e.g. `~/Documents`, `~/Desktop`, `/tmp`) instead of blocklist.
**Same-day mitigation:** **S-7** above (add the paths to `_BLOCKED_ROOTS`) AND **S-2** (kill MCP HTTP so it's not reachable from claude.ai).
**Why critical:** persistent RCE from a remote MCP client over a 30-day OAuth token.

### D-5 — `permission_gate` path-traversal via fnmatch [CRITICAL]
**Location:** `codec_agent_runner.py:95-142` (`permission_gate`)
**Wave 1 fix:** in `permission_gate`, call `os.path.realpath(os.path.expanduser(action.path))` AND verify the realpath still starts with one of the approved roots (after realpathing those too). Replace `fnmatch` with prefix-on-realpath comparison. Add `/.codec/skills`, `/.codec/plugins`, `/.codec/auth`, `/.codec/oauth_state.json` to `_PATH_BLOCKLIST_SUBSTRINGS` (`codec_agent_plan.py:754`). Reject any path with `..` segments outright.
**Same-day mitigation:** none clean. Until Wave 1 ships, avoid approving any agent plan with broad write grants. If you must approve a plan, manually verify the action paths in subsequent checkpoints don't contain `..`.
**Why critical:** permission-gate bypass; reads/writes outside the granted scope; chain to D-1 RCE via the `_PATH_BLOCKLIST_SUBSTRINGS` gap.

---

## 4. Wave structure proposal

Mirror the Intake Phase 3 wave pattern. 7 waves planned; sizes are PR-counts, NOT findings-counts.

### Wave 1 — Skill loading hardening + write-path gates (target: 2-3 days)
**Findings:** D-1, D-2, D-3, D-4, D-5
**PR count estimate:** 3-4
- PR-1A: D-1 load-time AST check + integration tests
- PR-1B: D-2 + D-3 — route through `/api/skill/review` staging; SSRF guards on URL fetch
- PR-1C: D-4 — `file_write` block-roots expansion + positive-allowlist refactor
- PR-1D: D-5 — `permission_gate` realpath + `_PATH_BLOCKLIST_SUBSTRINGS` updates
- Optional PR-1E: replace `is_dangerous_skill_code`'s pattern blocklist with positive allowlist (closes D-17 too)

**Rationale:** these 5 findings share the same root pathway (write-to-skills-dir → restart → exec). Wave 1 closes the pathway.

### Wave 2 — Rest of security cleanup (target: 1 week)
**Findings:** D-6 through D-22 (17 findings)
**PR count estimate:** 4-6
- PR-2A: D-7 — dashboard default `127.0.0.1` + auto-generated `dashboard_token` + unconditional CSRF
- PR-2B: D-8 + D-15 — secret storage via macOS Keychain (OAuth tokens + config secrets)
- PR-2C: D-9 + D-10 — `python_exec` sandbox + `/api/execute` removal-or-strict-consent
- PR-2D: D-11 — replace `x-internal: codec` header trust with per-process token in `~/.codec/internal_token`
- PR-2E: D-12 + D-19 + D-22 — audit log HMAC-chain + secret redaction + chmod 0600
- PR-2F: D-13 — AppleScript injection fix in `imessage_send`; D-21 — same for `do_screenshot_question`; D-18 — plugin AST validation + thread-timeout
- PR-2G: D-14 + D-16 + D-20 — path-blocklist hardening across `codec_agent_plan`, `extract_user_paths`, `file_ops`

**Rationale:** these 17 don't share a single chokepoint, so they break into 4-6 themed PRs. Wave 2 closes the rest of the security debt.

### Wave 3 — Code quality + dead code (target: 1 week)
**Findings:** all 22 in Audit A
**PR count estimate:** 4-5
- PR-3A: A-1 + A-2 — delete the ~733 LOC of dead `build_session_script` + `skills/codec.py` fork. **High-leverage**: investors and contributors will read these files first.
- PR-3B: A-3 + A-22 — rewrite the 21 + 50 silent-except sites with proper narrow-except + `log_event` audit
- PR-3C: A-4 + A-16 + A-17 + A-21 — skill-loader unification (delete `codec_core.loaded_skills` path) + wire `WAKE_PHRASES` and `DRAFT_KEYWORDS_CFG` + remove dead `AGENT_NAME` constant
- PR-3D: A-5 + A-6 + A-7 — extract helpers from the 3 monolithic functions (`_dispatch_inner`, `chat_completion`, `Agent.run`)
- PR-3E: A-11 + A-12 — unify vision + 51-site `chat/completions` through `codec_llm_proxy`
- PR-3F (optional, large): A-19 — bridge unification (iMessage + Telegram → `BridgeRouter`)
- PR-3G: small misc — A-8 (codec_keyboard), A-9 (DISABLED overlay), A-10 (run_session_module), A-13 (dashboard pattern blocker), A-14 (close_session shadow), A-15 (config_version), A-18 (unused Pydantic models), A-20 (inline sqlite)

**Rationale:** Audit A is the broadest in scope — clean up patterns and dead code. PR-3A alone deletes ~730 LOC and improves the first-impression of the most-read file.

### Wave 4 — Reliability (target: 1 week)
**Findings:** all 22 in Audit C
**PR count estimate:** 5-6
- PR-4A: C-1 + H-1 — unified SIGTERM/SIGINT/atexit lifecycle helper (`codec_lifecycle.py`) wired into all 11 PM2 daemons
- PR-4B: C-2 — replace `~/.codec/pwa_response.json` with per-request files (or migrate to SQLite); add `request_id` correlation
- PR-4C: C-3 + C-4 + M-1 + M-2 — unify all `~/.codec/*.json` writers on `_atomic_write_json`; add `fcntl.flock` for cross-process; add eviction
- PR-4D: C-5 — narrow `_atomic_set_status` to `InvalidStatusTransition`-only; return bool; check at call sites
- PR-4E: H-3 — `fcntl.flock` on audit-log writes + rotation
- PR-4F: H-2 — add `_state_lock` to `codec.py`'s state dict
- PR-4G: H-4 + H-5 + H-6 + M-6 — bounded growth + eviction for `_agent_jobs`, `_RATE_WINDOW`, `_pending_approvals`, `_resumable_sessions`
- PR-4H: H-7 + H-8 + H-9 — tempfile leak fixes (try/finally) in observer OCR, `_exec_code`, `codec_session.speak()`
- PR-4I: M-3 + M-4 + L-1 + L-2 — small fixes: audit-rotation logging, observer narrow except, fsync in `codec_ask_user`, autopilot corrupt-state recovery

**Rationale:** reliability findings affect daily-driver Mickael UX directly. C-1 + the 3 file-race findings are the most impactful.

### Wave 5 — Apple app readiness (target: 6-10 weeks of engineering + 2-4 weeks Apple Dev enrollment in parallel)
**Findings:** all 18 in Audit E
**PR count estimate:** 8-12

Mickael decision required first (see §5): launch date, pricing model, Apple Dev account status, bundle Python or probe, PM2 vs launchd, model distribution strategy.

After decisions, the ordered checklist from Audit E:
- W5-Pre: E-13 — start Apple Developer Program enrollment (D-U-N-S Number lookup for AVA Digital LLC, 2-4 weeks lag time) **DAY ZERO**
- W5-1: E-4 + E-16 — decision docs (App Store is out, OSS stays unsigned)
- W5-2: E-1 + E-17 + E-5 — `.app` bundle wrapper + Info.plist + entitlements + PrivacyInfo
- W5-3: E-7 — pick PM2-vs-launchd, migrate 17 services
- W5-4: E-6 — pick bundled-Python-vs-probe, implement (XL)
- W5-5: E-8 — model-pack downloader + bundled minimum set (XL)
- W5-6: E-9 — first-launch permissions wizard
- W5-7: E-2 — code signing pipeline
- W5-8: E-3 — notarization pipeline
- W5-9: E-11 — license validation wired in
- W5-10: E-10 — Cloudflare tunnel paid-tier story
- W5-11: E-15 — GUI onboarding replacement for `setup_codec.py`
- W5-12: E-14 — uninstaller
- W5-13: E-12 — Sparkle auto-update integration
- W5-Post: E-18 — opt-in crash reporting (v1.1)

**Rationale:** this wave is its own multi-week project, separate from the OSS hardening waves. Should start in parallel with Wave 1, not after.

### Wave 6 — Investor / enterprise readiness (target: 2 days for the bulk, plus 1 week of follow-on)
**Findings:** all 19 in Audit F
**PR count estimate:** 5-6 (clustered by Audit F's 2-day burn-down plan)

Day 1 morning (~3h total):
- PR-6A: F-1 SECURITY.md + F-6 CODE_OF_CONDUCT.md + F-7 FUNDING.yml + F-16 garbage file delete
- PR-6B: F-3 + F-17 — reconcile test counts; swap static badge for workflow-status badge
- PR-6C: F-12 — Discord + GitHub Discussions + add to README header

Day 1 afternoon (~4h):
- PR-6D: F-8 — record + edit 20-second demo GIF in first viewport
- PR-6E: F-9 — rewrite top of README to lead with value prop

Day 2 morning (~4h):
- PR-6F: F-2 — PRIVACY.md with EU/AI Act statement
- PR-6G: F-10 — add "Why CODEC, not X" comparison block

Day 2 afternoon (~3h):
- PR-6H: F-13 — `docs/ONE-PAGER.md`
- PR-6I: F-5 — retroactively tag releases + enable GitHub Releases

Follow-on (later, half-day):
- PR-6J: F-4 — expand CI to run full test suite + coverage gate
- PR-6K: F-14 — inline architecture diagram in README
- PR-6L: F-11 — paid-tier subsection (contingent on Mickael's pricing decision)

Deferred:
- F-15 — `pyproject.toml` migration (good to do, not blocking)
- F-18 — Lucy positioning (contingent on Mickael's brand decision)
- F-19 — Mac app code-signing (handled in Wave 5)

**Rationale:** Audit F's own findings have a clean burn-down plan. Day 1 hits 7/10; Day 2 hits 8.5/10.

### Wave 7 — Projects + Pilot (Audit B) (target: TBD)
**Findings:** TBD pending Mickael's description.
**Placeholder:** see §6 below.

---

## 5. Decisions Mickael needs to make before fix PRs start

These are blocking decisions for wave PRs — answer before the wave-author prompt is fired.

### Security architecture decisions

**Q1. Skill review gate.** Should `/api/save_skill` and `/api/forge` be **(a) removed entirely** (skill creation goes through the existing `/api/skill/review` flow only), or **(b) wired through `/api/skill/review` staging** (LLM proposes → user approves → promoted)? The audit recommends (a); (b) preserves the Skill Forge UI.

**Q2. Secret storage.** Migrate `~/.codec/config.json` secrets + OAuth tokens to **macOS Keychain via `security add-generic-password`**? The OS-native path is cleaner; the alternative is envelope encryption + a per-install key (~/.codec/secret.key 0600). Keychain has the additional benefit of Touch ID gating.

**Q3. Dashboard default binding.** Change `host="0.0.0.0"` to `host="127.0.0.1"` (loopback only) by default, with explicit opt-in (env var or config flag) for LAN exposure? Audit D recommends yes.

**Q4. `/api/execute` endpoint.** Delete entirely (the `terminal` skill already exists with proper gating), or wire through `ask_user` strict-consent (Step 3 §1.7)? Audit D recommends delete.

**Q5. Audit log integrity.** **(a) HMAC-SHA256 per line** with a per-install secret in macOS Keychain, **(b) hash chain** (each line includes hex(sha256(prev_line + new_line))), or **(c) minimal** — just chmod 0600 + secret redaction? The compliance story (CLAUDE.md §1) is stronger with (a) or (b).

**Q6. `x-internal: codec` header trust.** Replace with per-process token in `~/.codec/internal_token` (HMAC compare on every internal call)? Audit D recommends yes.

### Reliability architecture decisions

**Q7. PWA bridge architecture.** Migrate `notifications.json` + `pending_questions.json` + `pwa_response.json` to **SQLite** (existing `memory.db` gets new tables), or fix in place with **`fcntl.flock`** + atomic writes? SQLite is the bigger one-time migration; flock is the smaller incremental fix. Both work.

**Q8. `codec.py` SIGTERM suppression.** Were the two `signal.signal(SIG..., lambda *a: None)` lines added deliberately (e.g. to prevent Ctrl-C during interactive testing), or accidentally? If deliberate, the fix is to gate them behind an env var (`CODEC_INTERACTIVE=1`); if accidental, just delete them and add an atexit cleanup handler.

**Q9. `_agent_jobs` retention.** Should completed crew runs persist to disk (so the PWA shows "last 30 crew runs" after dashboard restart), or is the existing audit-log emit enough? Affects PR-4G design.

### Code architecture decisions

**Q10. Skill-loader unification.** OK to migrate `codec.py:_dispatch_inner` to use `codec_dispatch.check_skill` + `run_skill`, then delete `codec_core.loaded_skills` / `codec_core.load_skills` / `codec_core.run_skill`? Audit A recommends yes; check for out-of-tree consumers first.

**Q11. Bridge unification (A-19).** Merge `codec_imessage.py` + `codec_telegram.py` into a single `BridgeRouter` with platform adapters? Trade-off: 1 codepath vs. 2; the 2 have already drifted on goal-tracking, intent-detection, voice-briefing features. Decision blocks PR-3F.

**Q12. A-1 + A-2 deletion safety.** Any out-of-tree consumer of `codec.py:build_session_script` or `skills/codec.py` (a Mac app build pipeline, an installer test, a private fork)? If no, delete in PR-3A. ~733 LOC removed in one commit.

### Apple App decisions (Wave 5)

**Q13. Mac app launch date target.** With 6-10 weeks engineering + 2-4 weeks Apple Dev enrollment, earliest realistic v1 is ~10-14 weeks from start. What's the target ship date?

**Q14. Apple Developer Program enrollment status for AVA Digital LLC.** Done? In progress? Not started? This is the longest-pole item — start day-zero if not done.

**Q15. Pricing model.** One-time license / subscription / freemium? Affects license validation architecture (E-11) and PR-5-9.

**Q16. OSS distribution signing.** OSS stays unsigned (user clones + builds locally, trusts own `swiftc`), or sign everything? Audit E recommends OSS unsigned, paid app signed.

**Q17. Bundled Python vs. probe-at-install.** Bundle Python.framework inside the `.app` (~80MB + signing surface), or probe at install with a hard-fail UX if Python is missing? Audit E says bundling is cleaner but XL effort.

**Q18. PM2 vs launchd.** Replace PM2 with macOS-native launchd plists (17 LaunchAgent files, no Node dependency), or bundle Node.js (+75MB)? Audit E recommends launchd as the cleaner Apple-native fit.

**Q19. Model distribution strategy.** Bundle minimum models in `.dmg` (~8GB installer), or first-run download with progress UI (25GB from CDN)? Affects buyer's first-launch UX.

**Q20. Cloudflare tunnel paid-tier story.** AVA Digital hosts inbound tunnels per-customer (op cost on AVA), or paid v1 ships LAN-only + recommends Tailscale? Affects E-10.

**Q21. `ava-license` service location.** Confirm runs on AVA backend (not on user machines)? CLAUDE.md mentions it; `ecosystem.config.js` doesn't include it.

### Investor / positioning decisions (Wave 6)

**Q22. Lucy positioning.** Separate brand, codename for Mickael's CODEC instance, or planned product? Blocks F-18 README phrasing and any Lucy-shaped wave content.

**Q23. Paid tier shape.** Managed installer / hosted LLM / team features / enterprise SSO / priority support? Affects F-11 paid-tier README subsection.

**Q24. Target enterprise vertical.** Sales / finance / legal / dev / other? Affects GTM positioning of skill plugin catalog (`marketing:*`, `sales:*`, `finance:*`, `legal:*`).

**Q25. YC application timing.** S26 missed; W27 batch (deadline ~Sep 2026)? Or non-YC accelerator? Shapes investor-grade target date.

**Q26. Trademark strategy.** "Sovereign AI Workstation" and "CODEC" — trademarked? CODEC is a generic term; product brand is cleaner but long.

**Q27. AVA Digital LLC jurisdiction.** Spanish entity (EU privacy ease) vs. Delaware C-corp (YC funding requirement)? May need a flip ($5-15k legal exercise).

### Cross-cutting decisions

**Q28. Test count reconciliation.** Pick one number for README + CONTRIBUTING.md + CLAUDE.md: **873** (verified), **940+** (current README badge — overcounted), **600+** (CLAUDE.md — undercounted), **168+** (CONTRIBUTING — stale)? Audit F recommends 873 or "850+".

**Q29. CI scope expansion.** Wave 6's PR-6J expands CI from 4 to 54 test files. Some tests are already documented-failing per CLAUDE.md §9 ("known pre-existing failures documented in `docs/known-issues.md`"). Should those be `@pytest.mark.xfail`-marked first, or fixed in Wave 4 reliability before CI gates them?

**Q30. Wave 2 ordering.** Wave 2 (rest of D) ships after Wave 1 (D-1..D-5). Should Wave 4 (reliability) interleave with Wave 2 because some reliability findings (C-3, C-4) directly affect Wave 2's audit-log-integrity work (D-12, H-3)? Audit C says yes — interleaving makes sense for the audit-log work specifically.

---

## 6. Audit B placeholder

**Status:** dispatched after Mickael provides the Projects + Pilot description.

**Scope as defined by the plan (re-stated here for completeness):**
- **Projects:** what does this feature do, which files/modules contain its code, what tests/smoke checks have been done so far, and what's known-fragile?
- **Pilot:** same questions.

**Audit B will produce:**
- Document: `docs/audits/PHASE-1-PROJECTS-PILOT.md`
- Same severity model as A-F (CRITICAL / HIGH / MEDIUM / LOW)
- Pre-audit findings if any
- State-management map (where state lives, how it's persisted, how it's recovered)
- Adversarial cases (worst-case for each feature)
- Smoke-test checklist Mickael can run manually

**Wave 7 will be sized after Audit B completes.** Estimate (based on the other audits' size): 10-20 findings.

**What this triage doesn't tell us:**
- Whether Projects and Pilot share findings with Wave 1 (skill loading) or Wave 4 (reliability)
- Whether Projects and Pilot have any same-day mitigation needs (STOP-GAP) that should be added to §1
- Whether Audit B has security findings that re-prioritize Wave 1

**Hand-off prompt for Mickael:**
> Paste a description of Projects + Pilot when ready. I'll dispatch Audit B (general-purpose agent, ~10-15 min), write the audit doc, and update §0, §2, §5, §6 of this triage in place.

---

## 7. Reference: per-audit summaries

### Audit A — Code Quality (22 findings)
**File:** [docs/audits/PHASE-1-CODE-QUALITY.md](PHASE-1-CODE-QUALITY.md)
**Top 3:**
- A-1 — `codec.py:240-513`: 273 LOC of dead `build_session_script` (never called).
- A-2 — `skills/codec.py`: 460 LOC stale fork of the main entry point, not a valid skill (missing `SKILL_NAME` / `SKILL_TRIGGERS` / `run`).
- A-3 — 21 sites of `log.warning(f"Non-critical error: {e}")` masking real failures in config parse, subprocess cleanup, pgrep, DB write.

### Audit C — Reliability (22 findings)
**File:** [docs/audits/PHASE-1-RELIABILITY.md](PHASE-1-RELIABILITY.md)
**Top 3:**
- C-1 — `codec.py:1-4`: SIGINT + SIGTERM no-op handlers cause force-kill leaking sox + tkinter + temp files on every PM2 restart.
- C-2/C-3/C-4 — three different `~/.codec/*.json` files with three different write semantics across 11 PM2 daemons; per-process locks; cross-process races.
- C-5 — `_atomic_set_status` swallows all exceptions → agent state machine desyncs from runner's mental model.

### Audit D — Security (22 findings, 19 of 42 red-team bypasses)
**File:** [docs/audits/PHASE-1-SECURITY.md](PHASE-1-SECURITY.md)
**Top 3:**
- D-1 — Skill registry lazy-load = RCE for anyone who can drop a `.py` in `~/.codec/skills/`.
- D-4 — `file_write` skill (MCP-exposed, NOT in `_HTTP_BLOCKED`) → claude.ai over 30d OAuth can write to that directory.
- D-7 — Default-no-auth dashboard binds `0.0.0.0:8090` with no CSRF.

### Audit E — Apple App Distribution (18 findings, ~5% readiness)
**File:** [docs/audits/PHASE-1-APPLE-APP.md](PHASE-1-APPLE-APP.md)
**Top 3:**
- E-1 — No `.app` bundle, no Info.plist, no bundle identifier — foundational.
- E-6 — Hardcoded `/usr/local/bin/python3.13` in `ecosystem.config.js` for 6 PM2 services — won't survive on a fresh user machine.
- E-13 — No Apple Developer Program enrollment evidence — 2-4 week lag, longest pole, start day-zero.

### Audit F — Investor / Enterprise Readiness (19 findings, 6.5/10 score)
**File:** [docs/audits/PHASE-1-INVESTOR-READINESS.md](PHASE-1-INVESTOR-READINESS.md)
**Top 3:**
- F-1 — No SECURITY.md / vulnerability disclosure policy.
- F-2 — No public-facing privacy / data-handling statement; AVA is EU-based, GDPR + AI Act apply on paid launch.
- F-3 — README "tests-940+" badge with only 4 of 54 test files actually run in CI.

### Audit B — Projects + Pilot (TBD)
**File:** placeholder; dispatched after Mickael's description.

---

## 8. Pre-audit finding verification (consolidated)

| ID | Status | Located by | Notes |
|---|---|---|---|
| P-1 | **REFUTED** | Audit A | `' + AGENT_NAME + '` string-concat anti-pattern does NOT exist anywhere in the code. `AGENT_NAME` is a dead module-level constant (Audit A-21). |
| P-2 | **CONFIRMED (broader)** | Audit A | 21 instances total across `codec_keyboard.py` (9), `codec_dashboard.py` (9), `routes/skills.py` (3) — NONE in `codec.py` itself. |
| P-3 | **REFUTED** | Audit A | `UPDATE sessions … ORDER BY id DESC LIMIT 1` is not in the code. The actual `UPDATE` in `codec.py:718` uses `WHERE id=?` (primary-key, correct). `pwa_dispatch` symbol doesn't exist. |
| P-4 | **PARTIAL** | Audit F | Tests DO exist: 873 functions in 54 files. But README claims "940+" (overcounted), CLAUDE.md claims "600+" (undercounted), CONTRIBUTING.md says "168+" (stale). CI only runs 4 of 54 files. Coverage gap confirmed. |
| P-5 | **PARTIAL** | Audit A | Yes, monolithic functions exist; specific numbers in pre-audit were wrong. Real monoliths: `chat_completion` 440 LOC, `Agent.run` 225 LOC, `_dispatch_inner` 200 LOC, etc. |
| P-6 | **CONFIRMED** | Audit A | `codec.py:716-721` opens a fresh `sqlite3.connect` for an inline UPDATE, bypasses `CodecMemory`. Other inline imports exist for valid lazy-load reasons. |
| P-7 | **CONFIRMED (worse than stated)** | Audit C | File path is `~/.codec/pwa_response.json` (not `/tmp/q_pwa_response.json`). Defects: non-atomic write + no request/response correlation + mtime-based stale detection + no inter-process mutex. |
| P-8 | **CONFIRMED** | Audit D | Pattern blocklist: ≥19 of 42 plausible bypass variants succeed (~45% bypass rate). The blocker is a typo-catcher, not a security boundary. |
| P-9 | **CONFIRMED** | Audit D | Audit log plaintext, no HMAC, no chain, no chmod-enforcement at create time, no secret redaction before write. |
| P-10 | **CONFIRMED** | Audit A | `codec_config.py` has no `CONFIG_SCHEMA_VERSION`; no `config_version` field in any `cfg.get(...)`; no migration path. |

---

## 9. What this triage does NOT include

- **Fix PRs.** Per Mickael's brief: produce the triage, STOP. Wave-PR prompts are written separately and fired one at a time.
- **Audit B findings.** Placeholder pending Mickael's Projects + Pilot description.
- **Linter execution.** Audit A could not run `ruff`/`mypy`/`bandit`/`vulture` due to sandbox permissions in the agent's environment. A re-run with linters enabled may surface additional small findings; consider this in PR-3.
- **Live exploit verification.** Audit D's red-team was static analysis only — no commands were executed against running CODEC. A follow-up wave (Phase 2 red-team) should verify with live testing once Wave 1 has landed.
- **Code-vs-CLAUDE.md drift.** A few CLAUDE.md statements look stale (e.g. `codec-hotkey` PM2 service is mentioned but not in `ecosystem.config.js`). A CLAUDE.md reconciliation pass is recommended after Wave 1 + Wave 3.

---

**End of consolidated triage. Awaiting wave-decision input from Mickael.**
