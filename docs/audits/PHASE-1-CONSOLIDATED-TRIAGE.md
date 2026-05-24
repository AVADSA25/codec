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
| **D-4** | Security | `file_write` skill (MCP-exposed) can write to `~/.codec/skills/` | **W1 — CLOSED ([#45](https://github.com/AVADSA25/codec/pull/45), `0065d90`)** |
| **D-5** | Security | `permission_gate` accepts path-traversal via `fnmatch` (no realpath) | **W1 — CLOSED ([#47](https://github.com/AVADSA25/codec/pull/47), `fd2b460`)** |
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
- PR-2D: D-11 — replace `x-internal: codec` header trust with per-process HMAC token in macOS Keychain ✅ (branch `fix/pr2d-internal-token-replacement`; token never lands on disk in plaintext — `codec_keychain.get_internal_token()` bootstraps to Keychain or 0600 envelope-encrypted fallback)
- PR-2E: D-12 + D-19 + D-22 — audit log HMAC-SHA256-per-line + secret redaction + chmod 0600 ✅ (branch `fix/pr2e-audit-log-hmac-redaction`; secret in macOS Keychain `ai.avadigital.codec.audit_hmac_secret`; 15-pattern redaction sweep applied before truncation; `verify_audit_log()` utility + operator-only `audit_verify` skill)
- PR-2F: D-13 — AppleScript injection fix in `imessage_send`; D-21 — same for `do_screenshot_question`; D-18 — plugin AST validation + thread-timeout ✅ (branch `fix/pr2f-applescript-and-plugin-hardening`; strict phone/email regex gate for recipient; argv-binding for screenshot dialog so adversarial OCR text can't escape the string context; SHA-256 allowlist at `~/.codec/plugins.allowlist` + 500ms daemon-thread hook timeout + new operator-only `plugin_approve` skill; existing plugins grandfathered via one-time migration)
- PR-2G: D-6 — dangerous-command blocker rewrite (normalize + layered categories) ✅ (branch `fix/pr2g-dangerous-command-hardening`; bypass rate 45% → 0% across all 42 red-team variants; documented as confirmation-trigger heuristic, not a complete boundary)
- PR-2H: D-17 + D-20 — AST reflection hardening + `file_ops` path-blocklist ✅ (branch `fix/pr2h-ast-reflection-and-fileops`; reflection dunder-attr blocking + bare `__builtins__` Name + `vars`/`dir` in `is_dangerous_skill_code`; network-module + `open` blocking deliberately scoped out — see D-17 footnote; `file_ops` now blocks the whole `~/.codec/` tree + repo skills/ realpath-resolved with `file_ops_blocked` audit; D-14 + D-16 were already closed in PR-1D)
- PR-2B-2: D-15 remainder — migrate the last 4 plaintext secrets (`gemini_api_key`, `pexels_api_key`, `serper_api_key`, `telegram.bot_token`) to Keychain ✅ (branch `fix/pr2b2-remaining-secrets-keychain`; new Keychain-aware getters reuse the PR-2B `_migrate_and_get`/30s-cache machinery + a nested variant for `telegram.bot_token`; 6 call sites updated; env-var fallback preserved. Residuals: `alerts.telegram.bot_token` is a separate audit-unnamed key left for future cleanup; `auth_pin_hash` argon2id remains out-of-scope. **This closes Wave 2 entirely.**)

**Rationale:** these 17 don't share a single chokepoint, so they break into themed PRs. Wave 2 closes the rest of the security debt. (D-14 + D-16 were folded into PR-1D's path-blocklist work; the originally-planned "PR-2G: D-14+D-16+D-20" became D-20-only, re-slotted into PR-2H alongside D-17.)

### Wave 3 — Code quality + dead code (target: 1 week)
**Findings:** all 22 in Audit A
**PR count estimate:** 4-5
- PR-3A: A-1 + A-2 — delete the ~733 LOC of dead `build_session_script` + `skills/codec.py` fork. **High-leverage**: investors and contributors will read these files first. ✅ (branch `fix/pr3a-delete-dead-build-session-script`; verify-first dead-code trace confirmed both unreachable before deletion; codec.py orphan removed 1170→894 LOC + skills/codec.py fork deleted = ~734 LOC; `codec_core.build_session_script` deprecated copy KEPT per A-1; 1325 passing, fixed a pre-existing test, zero new failures)
- PR-3B: A-3 + A-22 — rewrite the silent-except sites with proper narrow-except + `log_event` audit ✅ A-3 fully closed (20 sites: keyboard/dashboard/skills, narrowed + re-leveled + better messages); A-22 partially closed (the named post-LLM `[SKILL:]` tag-resolution bug fixed). **Residual A-22 sweep → PR-3B-2**: the "~50" was an estimate; the other named sites are legitimate graceful-degradation fallback paths (codec_voice Gemini→local, observer injection, TTS) — narrowing them risks breaking working code, so a focused per-site survey is split out rather than rushed. (branch `fix/pr3b-silent-except-cleanup`)
- PR-3B-2: A-22 survey + bare-except fix ✅ (branch `fix/pr3b2-silent-except-survey`). Per-site survey concluded the remaining silent `except: pass` are all legitimate cleanup/best-effort (no bug-hiders left — those were fixed in PR-3B). Concrete fix: converted all **36 bare `except:` → `except Exception:`** across 12 production files (they swallowed KeyboardInterrupt/SystemExit). AST regression guard added (`tests/test_no_bare_except.py`). Full suite 1365 passing, zero new. **A-22 fully closed.**
- PR-3C: A-16 + A-17 + A-21 — wire `WAKE_PHRASES` (deduped homophone keywords + length-guarded phrase match in a testable `_is_wake_utterance`) + wire `draft_keywords` into `codec_core.is_draft` + remove dead `AGENT_NAME` constant ✅ (branch `fix/pr3c-wire-config-knobs`; 13 tests; zero net-new ruff; full suite 1338 passing). **A-4 (skill-loader unification) deliberately split out → its own PR**: it refactors the LIVE multi-file skill-dispatch path (`codec_core.load_skills` is called from codec.py + dashboard ×2 + voice + agent_runner), needs a careful voice-path test pass, and doesn't belong bundled with these contained config-wiring fixes.
- A-4: skill-loader unification ✅ (branch `fix/pr3-a4-skill-loader-unification`, design-first per §11 → `docs/A4-SKILL-LOADER-UNIFICATION-DESIGN.md`). Deleted legacy `codec_core.{loaded_skills,load_skills,run_skill}`; codec.py + cortex_skills now use canonical `codec_dispatch` registry. Closed a real **security gap** (legacy path skipped the PR-1A AST gate) + a **hooks bypass** (voice path now fires run_with_hooks). Option A: `custom_triggers.json` now honored everywhere via SkillRegistry. 10 tests; full suite 1376 passing.
- PR-3D: A-5 + A-6 + A-7 — extract helpers from the 3 monolithic functions (`_dispatch_inner`, `chat_completion`, `Agent.run`). Split into 3 sub-PRs (one per function — too risky to do all three on these hot paths in one diff). Design → `docs/PR3D-MONOLITH-EXTRACT-DESIGN.md`.
  - PR-3D-a: A-7 `Agent.run` ✅ (branch `fix/pr3d-extract-monolith-helpers`). Behavior-preserving extraction of `_parse_action` (pure protocol parse), `_validate_tool_call` (pure — 4 guards → 1 rejection msg), `_execute_tool_with_hooks` (copy_context + run_with_hooks + veto executor). `run()` 230 → 177 LOC; stuck detection kept inline post-`tool_result`-audit for exact parity. 13 unit tests (`tests/test_agent_run_helpers.py`) + 112 agent/crew regression tests green; zero net-new ruff; full suite 1444 passing, zero new failures.
  - PR-3D-b: A-5 `_dispatch_inner` ✅ (branch `fix/pr3d-b-dispatch-inner`). Behavior-preserving extraction of `_build_voice_system_prompt(task)` (memory/identity/facts + prompt assembly) + `_persist_voice_turn(task, answer, rid)` (session + DB + CodecMemory save). `_dispatch_inner` 188 → 131 LOC. Faithfulness: `_persist_voice_turn` carries its own `from codec_memory import CodecMemory` (original relied on the build block's local import being in scope). 7 unit tests (`tests/test_dispatch_inner_helpers.py`); zero net-new ruff; full suite 1451 passing, zero new failures.
  - PR-3D-c: A-6 `chat_completion` ✅ (branch `fix/pr3d-c-chat-stream`). Extracted the `<think>` + `[SKILL:...]` streaming tag-machine into new `codec_chat_stream.py` (`SkillTagBuffer` + shared `SKILL_TAG_RE`); `_stream_gen` keeps SSE/HTTP plumbing + injected `_resolve_skill_tag`. `chat_completion` 466 → 379 LOC. Behavior preserved exactly (same-chunk-`</think>`-dropped + think-adjacent-uncounted quirks, 5000-cap, cross-chunk assembly, empty dropped-tag frames). 13 unit tests (`tests/test_chat_stream.py`); zero net-new ruff; full suite 1464 passing, zero new failures. **Bonus:** unblocks the deferred A-12 dashboard-stream migration (the dashboard can now feed `codec_llm.stream()`'s raw tokens through `SkillTagBuffer`). **PR-3D complete — all 3 monoliths decomposed (A-7 #72 · A-5 #73 · A-6).**
- PR-3E: A-11 + A-12 — unify vision + `chat/completions` ✅ (branch `fix/pr3e-llm-vision-dedup`, design-first per §11 → `docs/PR3E-LLM-VISION-DEDUP-DESIGN.md`; **Option 2** chosen by Mickael). **A-11 fully closed**: new `codec_vision.py` (sync+async, Gemini→local fallback, live config); all 3 consumers (codec.py/voice/session) delegate; session gains a Gemini fallback it lacked. **A-12 first tranche**: discovered `codec_llm_proxy` is a *queue*, not an HTTP caller — built genuinely-new `codec_llm.py` (`call()` + `strip_think`/`extract_content`, retry, never-raises) and migrated codec.py voice-reply chat + `codec_session.qwen_call`. **Deferred to phased follow-ons**: `qwen_stream` SSE (needs `codec_llm.stream()`) + ~40 remaining sites (dashboard/voice/agents/bridges/misc), each its own tranche. 19 tests (`tests/test_llm_vision_dedup.py`); full suite zero new failures.
- PR-3E-2: A-12 tranche 2 ✅ (branch `fix/pr3-a12-tranche2-stream`, design-first → `docs/PR3E2-LLM-STREAM-TRANCHE2-DESIGN.md`; **Option 1** chosen). Built streaming keystone `codec_llm.stream()` (sync generator, raw deltas, never-raises) + shared `_build_request`; migrated `codec_session.qwen_stream` (proof) + non-streaming trivials `codec_compaction` + `codec_dictate`. Read-the-source moved `codec_textassist` + `regen_skill_descriptions` to **2c** (raise-on-failure contract — never-raise would paste empty over the user's selection / write empty descriptions). 14 tests (`tests/test_llm_stream.py`); zero net-new ruff; full suite 1409 passing, zero new failures. **Remaining A-12 tranches:** 2c (raise-mode: textassist/regen/agent_plan/agent_runner), bridges (telegram/imessage), dashboard (non-stream + the stream tag-machine), voice `_stream_qwen` + agents (async `astream()` + queue), skills tranche.
- PR-3E-2c: A-12 tranche 2c ✅ (branch `fix/pr3-a12-tranche2c-raise-mode`, design-first → `docs/PR3E2C-RAISE-MODE-DESIGN.md`). Added `codec_llm.LLMError` + `codec_llm.call(raise_on_error=True)` (raises on every non-success path; default unchanged never-raise). Migrated the 4 fail-loud sites: `codec_textassist` (was pasting empty over the user's selection on failure) + `scripts/regen_skill_descriptions` (LLMError propagates like the old `raise_for_status`), and `codec_agent_plan`/`codec_agent_runner` `_qwen_chat` via an adapter mapping `LLMError` → their public `QwenUnavailableError` (+ parallel `_qwen_base()`). 14 tests (`tests/test_llm_raise_mode.py`); 109 agent tests still green; zero net-new ruff; full suite 1423 passing, zero new failures. **Remaining A-12:** bridges (telegram/imessage), dashboard (4 non-stream + stream tag-machine), voice `_stream_qwen` + agents `Agent.run` (async `astream()` + queue), skills tranche.
- PR-3E-bridges: A-12 bridges ✅ (branch `fix/pr3-a12-bridges`, design-first → `docs/PR3E-BRIDGES-DESIGN.md`). Migrated `codec_telegram.call_llm` + `codec_imessage.call_llm` text sites to `codec_llm.call` (default never-raise — bridges want graceful degradation; `None`-on-failure contract preserved via `content if content else None`, `chat_template_kwargs` filtered from kwargs). Removed a dead `import re` in imessage. Vision sites left for A-11. 8 tests (`tests/test_llm_bridges.py`); zero net-new ruff; full suite 1431 passing, zero new failures. **Remaining A-12:** dashboard (4 non-stream + stream tag-machine), voice `_stream_qwen` + agents `Agent.run` (async `astream()` + queue), skills tranche.
- PR-3E-dashboard: A-12 dashboard 3-of-5 ✅ (branch `fix/pr3-a12-dashboard`, design-first → `docs/PR3E-DASHBOARD-DESIGN.md`; **Option 1**). Migrated the 3 independent non-stream sites: `_qwen_chat_classify` (clean; gains `<think>` strip pre-JSON-parse), `command` Flash fallback (2 error strings → 1, documented collapse), crew-report writer (`raise_on_error=True` preserves raise). Read-the-source found the chat stream needs a `codec_llm.stream(keepalive=)` affordance (it swallows the empty thinking-chunks that hold the Cloudflare tunnel) and shares its `payload` with the non-stream fallback → that **stream+fallback pair deferred** to a dedicated keepalive PR. Removed 2 dead `import requests as rq` (+ a placeholder-less f-string) → net-negative ruff. 5 tests (`tests/test_dashboard_llm.py`); full suite 1469 passing, zero new failures. **Remaining A-12:** chat stream+fallback pair (needs `stream(keepalive=)`), voice `_stream_qwen` + agents `Agent.run` (async `astream()` + queue), self_improve/watcher, skills tranche.
- PR-3E-chat-stream: A-12 dashboard chat pair ✅ (branch `fix/pr3-a12-chat-stream`, design-first → `docs/PR3E-CHAT-STREAM-DESIGN.md`). Added `codec_llm.KEEPALIVE` + `codec_llm.stream(keepalive=)` (sentinel on empty thinking-chunks → dashboard emits `: keepalive`; default off → `qwen_stream` unaffected). Migrated `chat_completion`'s stream path (`codec_llm.stream(keepalive=True)` → existing `SkillTagBuffer`) + non-stream fallback (`codec_llm.call(raise_on_error=True)` preserves 500-on-failure), off shared `_common` args (no drift). Removed dead `import requests as rq` + `headers`. Closed-without-`[DONE]` now also emits a terminating `[DONE]` + blank-bubble fallback (documented improvement). 3 keepalive tests + chat-handler invariants; zero net-new ruff; full suite 1473 passing, zero new. **Dashboard A-12 complete** (only the 5 vision sites remain inline = A-11). **Remaining A-12:** voice `_stream_qwen` + agents `Agent.run` (async `astream()` + queue), self_improve/watcher, skills tranche.
- PR-3E-async: A-12 voice + agents async ✅ (branch `fix/pr3-a12-async`, design-first → `docs/PR3E-ASYNC-DESIGN.md`; **Option 2**). Added async `codec_llm.acall()` (mirrors `call()` + `raise_on_error`, injected httpx client) + `codec_llm.astream()` (mirrors `stream()` + keepalive, but **propagates** exceptions so voice can speak failures). Migrated all 3 queue-coupled async sites: `codec_voice._stream_qwen` (`astream`, queue CRITICAL, per-token `<think>` strip + spoken error kept), `codec_agents.Agent.run` (`acall(raise_on_error=True)`, queue MEDIUM), agents research-refiner (`acall`, never-raise → defaults). Queue stays at every call site (`codec_llm` never owns the semaphore). Removed dead `_qwen_url()`. 12 tests (`tests/test_llm_async.py`); crew + voice regression green; zero net-new ruff; full suite 1485 passing, zero new. **A-12 streaming complete.** **Remaining A-12:** self_improve/watcher + skills tranche.
- PR-3E-skills-misc: A-12 final tranche ✅ (branch `fix/pr3-a12-skills-misc`, design-first → `docs/PR3E-SKILLS-MISC-DESIGN.md`). Migrated the last 6 non-hot graceful sites: `codec_self_improve._draft_skill` (`call(retries=2)` → None), `codec_watcher.handle_draft` (kept 3× notify-retry loop, swapped inner POST + `extract_content` → `call`), `skills/translate` + `create_skill` + `skill_forge` (graceful → `call` + fallback), `skills/fact_extract._call_llm` (`call(retries=3, raise_on_error=True)` → keeps `__ERR__` sentinel). Removed 3 dead imports; regenerated `skills/.manifest.json` (76 skills, `--check` clean). 7 tests (`tests/test_llm_skills_misc.py`); net-negative ruff; full suite 1492 passing, zero new. **A-12 FULLY CLOSED** — every chat/completions text site is on `codec_llm`; only vision POSTs (A-11) remain inline. **Wave-3 Audit-A remaining:** only PR-3F (optional, large — bridge unification A-19).
- PR-3F: A-19 — bridge unification ✅ (branch `fix/pr3f-bridge-unification`, design-first → `docs/PR3F-BRIDGE-UNIFICATION-DESIGN.md`; **Option 1 scoped**). New `codec_bridges.py` with the 4 shared helpers (`load_dispatch`, `try_skill`, `call_llm(channel,…)`, `save_to_memory(channel, conv_id,…)`); both bridges import `try_skill` + keep thin channel-injecting wrappers for `call_llm`/`save_to_memory` (call sites unchanged). **`process_message` left per-bridge** — flows intentionally drifted (telegram audio/Gemini, imessage goals/intent); the churny `call_llm` dup was already fixed in #71, so the full `BridgeRouter` (unifying process_message) was high-risk/low-value. `codec_bridges.py` seeds the "add a channel" surface. Removed telegram's dead `sqlite3` import; fixed 2 stale #71 invariants. 10 tests (`tests/test_bridges.py`); zero net-new ruff; full suite 1502 passing, zero new. **Audit-A (Wave 3) complete.**
- PR-3G: small misc ✅ (branch `fix/pr3g-small-misc-cleanup`) — closed A-9 (DISABLED overlay, ~90 LOC), A-10 (run_session_module, 33 LOC + orphan `import sys`), A-14 (close_session shadow import), A-18 (9 unused Pydantic models + dead typing import). A-13 (dashboard pattern blocker) verified **already closed by PR-2C**. 6 regression tests; zero net-new ruff (net −); full suite 1344 passing. **Deferred from this batch (each needs its own focused PR):** A-8 (codec_keyboard.py 398 LOC — verify-first delete-or-migrate decision), A-15 (config_version — additive migration feature touching `load_config`), A-20 (inline sqlite in the live dispatch path — reliability fix needing a CodecMemory method).
- A-15: config schema versioning ✅ (branch `fix/pr3-a15-config-versioning`; `CONFIG_SCHEMA_VERSION=1` + migration ladder + idempotent atomic write-back in `load_config`; never creates-on-missing or overwrites-corrupt; 12 tests; zero net-new ruff; full suite 1356 passing).
- A-20: inline-sqlite reliability fix ✅ (branch `fix/pr3-a20-inline-sqlite`; added `codec_core._db_connect()` with WAL+busy_timeout + `update_session_response()`; replaced codec.py's inline lock-prone UPDATE; retrofitted all 4 codec_core session connects; removed now-unused sqlite3/DB_PATH imports; 9 tests; net-negative ruff; full suite 1365 passing).
- A-8: codec_keyboard.py deleted ✅ (branch `fix/pr3-a8-delete-codec-keyboard`; verify-first confirmed dead — no prod importer, no PM2 entry, live keyboard path inline in codec.py, clean_transcript lives in codec_config; chose delete over migrate to avoid swapping battle-tested core-UX code for an untested module; redirected TestKeyboard to codec.py; full suite passing). **All eight PR-3G-cluster findings now closed** (A-8/9/10/13/14/15/18/20).

**Rationale:** Audit A is the broadest in scope — clean up patterns and dead code. PR-3A alone deletes ~730 LOC and improves the first-impression of the most-read file.

### Wave 4 — Reliability (target: 1 week)
**Findings:** all 22 in Audit C
**PR count estimate:** 5-6
- PR-4A: C-1 + H-1 — unified SIGTERM/SIGINT/atexit lifecycle helper (`codec_lifecycle.py`) wired into all 11 PM2 daemons. **Split: C-1 first (the CRITICAL main-daemon fix), H-1 (the other 10 daemons) follows.**
  - PR-4A (C-1) ✅ (branch `fix/pr4a-codec-graceful-shutdown`, design-first → `docs/PR4A-CODEC-GRACEFUL-SHUTDOWN-DESIGN.md`). Removed codec.py's no-op SIGINT/SIGTERM handlers; added `_graceful_shutdown` (terminate rec_proc/overlay_proc + unlink audio_path + exit 0 on signal path; idempotent, never-raises) registered via signal + atexit in `main()`. No more orphaned sox/tkinter + leaked temp files on PM2 restart. 5 tests (`tests/test_graceful_shutdown.py`); zero net-new ruff; full suite 1507 passing, zero new.
  - PR-4A-2: H-1 ✅ (branch `fix/pr4a2-lifecycle-helper`, design-first → `docs/PR4A2-LIFECYCLE-HELPER-DESIGN.md`). New `codec_lifecycle.install_handlers(cleanup_fn, name)` (stdlib-only; SIGTERM+SIGINT → run cleanup once, idempotent + never-raise, then `sys.exit(0)`; + atexit). Key insight: Python's default SIGTERM skips atexit/finally, so PM2 restart force-kills handler-less daemons. Wired the **5 no-handler daemons**: autopilot + agent_runner (clean-exit log; state already atomic), observer (namespaced `codec_obs_*.png` tempfile + glob-purge on shutdown → closes the leak), imessage (saves live `last_rowid` + `SERVICE_STOP` on SIGTERM — was Ctrl-C-only), telegram (`SERVICE_STOP` on SIGTERM). `codec.py` (C-1) + `codec_dictate` already done. **Deferred → PR-4A-3 (if it bites):** uvicorn services (dashboard, mcp_http) already get SIGTERM via uvicorn; their app-level WebSocket/OAuth/rate-window flush is a separate uvicorn-lifespan change. (heartbeat/watchdog/overlay/hotkey were NOT in the H-1 finding's location list — out of scope.) 13 tests (`tests/test_lifecycle.py`); 94 daemon-regression tests green; zero net-new ruff (all 5 daemons SAME histogram); full suite zero new (41 = 41 baseline). AGENTS.md §2 gains `codec_lifecycle.py`.
- PR-4B: C-2 ✅ (branch `fix/pr4b-pwa-response-bridge`, design-first → `docs/PR4B-PWA-RESPONSE-BRIDGE-DESIGN.md`). Chose the audit's preferred fix — move the bridge to the `conversations` DB, delete the racy `~/.codec/pwa_response.json` entirely. `/api/command` returns `request_id` = the user row's `conversations.id` (server-authoritative correlation); `/api/response` resolves via new pure helper `_latest_response_for_session(db, session_id, after_id, after_ts)` (`id > after_id ORDER BY id ASC LIMIT 1`). Read-the-source found a **latent** race that naive file-removal would have exposed: the old `timestamp > after` query depends on the client wall-clock, so a fast skill answer on a high-latency Cloudflare link (RTT/2 > skill_time) or client/server clock skew could miss → 5-min poll timeout (the file fast-path had masked it) — hence the server-authoritative rowid. Error path now persists an assistant error row; frontend sends `&after_id=` (primary) + keeps `&after=` (legacy fallback). All 4 defects closed; deferred: strict per-tab attribution in the rare two-tab *simultaneous-interleave* of a shared `flash-<date>` session (needs a reply→request id column = `conversations` schema change). 18 tests (`tests/test_pwa_response_bridge.py`); zero net-new ruff; full suite zero new (41 = 41 baseline). No AGENTS.md change (the file was undocumented/ephemeral).
- PR-4C: C-3 + C-4 + M-1 + M-2 ✅ (branch `fix/pr4c-json-write-safety`, design-first → `docs/PR4C-JSON-WRITE-SAFETY-DESIGN.md`). New `codec_jsonstore.py` (`atomic_write_json` + `file_lock` cross-process flock CM). C-3+M-1: `routes/_shared._write_notifications` → atomic write + cap 500 (kills the non-atomic writer → no corrupt-reseed). C-4+M-2: codec_ask_user's 3 pending-questions RMW sites wrapped in `file_lock(PENDING_QUESTIONS_PATH)` (cross-process serialization — no more stranded `ask()` waiters) + `_save_pending_questions` prunes resolved >24h. Chose a flock context manager over a function-based `update_json` (5 multi-statement RMW blocks). Notifications kept atomicity-only (no partial flock) per the audit. 12 tests (`tests/test_json_write_safety.py`); 51 ask_user tests green; zero net-new ruff; full suite 1519 passing, zero new.
- PR-4D: C-5 ✅ (branch `fix/pr4d-atomic-set-status`, design-first → `docs/PR4D-ATOMIC-SET-STATUS-DESIGN.md`; **Full close** scope chosen by Mickael). `_atomic_set_status -> bool` (True applied / False illegal-or-superseded-or-write-fail; never raises). Run-start `running` transition **guarded** — `_run_agent` returns without executing checkpoints if the agent was superseded (external abort/pause), killing the "execute an aborted agent" bug. The 6 in-loop terminal emits (blocked_on_permission/aborted/blocked_on_destructive/paused/completed/blocked_on_qwen) gated behind `if applied:` — no more misleading "Blocked" audit+notification while the PWA shows paused. **Rejected the audit's option-c** ("just propagate" turns a user pause into an abort via the outer handler's `→aborted`) **and option-a** ("narrow the except" still swallows the bad transition). No state-machine/schema change; `_atomic_set_status` has no external callers. 7 tests (`tests/test_atomic_set_status.py`); 109 agent regression tests green; zero net-new ruff; full suite zero new (41 = 41 baseline). No AGENTS.md change (internal helper; §10 don't-touch note already covers the daemon, design gate re-run per §11).
- PR-4E: H-3 ✅ (branch `fix/pr4e-audit-flock`, design-first → `docs/PR4E-AUDIT-FLOCK-DESIGN.md`). `codec_audit._write` wraps rotation + append in `codec_jsonstore.file_lock(_AUDIT_LOG)` (flock LOCK_EX on the stable `audit.log.lock` sidecar) inside the existing `_LOCK`. Serializes the rotate-or-write critical section across all 11 PM2 daemons → closes Race A (concurrent rotation), B (write-during-rotation, fd-follows-inode split), C (>PIPE_BUF interleaving). Reused the PR-4C flock primitive (stdlib-only → no cycle with the foundation `codec_audit`). Schema/HMAC/redaction/0600/rotation-cadence all unchanged; never-raises preserved. 6 tests (`tests/test_audit_flock.py`); 70 audit-subsystem regression tests green (incl. perf budgets — flock adds ~tens of µs); zero net-new ruff; full suite zero new (41 = 41 baseline). AGENTS.md §6 gains a one-line cross-process-flock note (schema untouched).
- PR-4F: H-2 — add `_state_lock` to `codec.py`'s state dict
- PR-4G: H-4 + H-6 + M-6 ✅ (branch `fix/pr4g-bounded-dicts`, design-first → `docs/PR4G-BOUNDED-DICTS-DESIGN.md`). Eviction for three unbounded in-memory dicts: `_agent_jobs` (`_evict_stale_agent_jobs` — terminal jobs >24h, snapshot-iter under new `_agent_jobs_lock`; add now lock-guarded too), `_pending_approvals` (`_evict_expired_approvals` — delete >120s any status, called in the list+count endpoints; 404-not-409 on stale clicks), `_resumable_sessions` (`VoicePipeline._prune_resumable` classmethod, now called in `__init__` + save, `_resume_timestamps` promoted to class attr; deviated from the audit's threading.Timer — init+save prune covers the usage-bounded leak without a thread). 9 tests (`tests/test_bounded_dicts.py`); 175 consumer-regression tests green (agent_plan/dashboard_api/agent_runner/bridges/voice); zero net-new ruff (all 4 files SAME); full suite zero new (41 = 41 baseline). **H-5 (`_RATE_WINDOW`) split → PR-4G-2**: `codec_mcp_http` imports `mcp`/`fastmcp` (absent locally + in CI) → not unit-testable; won't ship an untested change to the live claude.ai service.
- PR-4G-2: H-5 — `codec_mcp_http._RATE_WINDOW` evict empty deques (needs an `mcp`-stub test path or careful manual verify). Pending.
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
