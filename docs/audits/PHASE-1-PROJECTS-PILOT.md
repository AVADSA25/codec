# PHASE 1 AUDIT B — PROJECTS (+ PILOT) SUBSYSTEM

**Date:** 2026-05-24
**Auditor:** parallel specialist review (security · architecture · correctness · test-coverage), consolidated + code-verified.
**Scope:** The Phase-3 **Projects** autonomous-agent runtime — `codec_agent_plan.py` (1,069 LOC), `codec_agent_runner.py` (1,214), `codec_agent_messaging.py` (384), `routes/agents.py` (740). **Mode:** AUDIT-ONLY — no code changed.
**Pilot:** CODEC Pilot lives in the sister `~/codec/pilot/` checkout, **not in this repo** — its audit is **deferred** until that tree is available (see §Pilot).

---

## Summary

- **Total findings:** 20 — **CRITICAL 3 · HIGH 6 · MEDIUM 7 · LOW 4** (Projects only; Pilot deferred).
- **Headline (the most important finding in all of Phase 1 for the "runs autonomously on your Mac overnight" pitch):** the autonomous agent's **permission model is structurally bypassable**. `permission_gate` and the destructive-consent gate both trust **booleans the controlled LLM emits about itself** (B-2), and the strict-consent function the runner imports **does not exist** (B-1) — so an agent can read/write outside its manifest and run destructive ops with no real gate. Layered on top, `/api/agents/*` mutations have no per-agent authorization and `/grant` skips the path blocklist (B-3), and `grants.json` — the actual enforcement input — isn't covered by the plan-hash tamper check (B-4).
- **Verified:** B-1, B-2, B-3, B-4, B-5, B-6 were confirmed directly against the source (line refs below), not taken on the reviewers' word.
- **Net:** the plan→approve→execute *design* is clean and the error→status mapping is thoughtful, but the **enforcement layer trusts the thing it's meant to contain**, and the **crash-resume + concurrency** paths (the whole point of an overnight agent) have real correctness gaps.

---

## Methodology

Four read-only specialist passes over the four modules + their six test files (`test_agent_plan/runner/run_helpers/messaging/agents_crews/chat_plan_persistence`), then dedupe + a direct code re-verification of every CRITICAL/HIGH. IDs below are the consolidated set; the per-pass IDs (PB-S/A/C/T*) are retained in git history.

---

## Findings

### B-1 — Destructive-consent gate imports a non-existent function [CRITICAL]
**What:** `codec_agent_runner.py:529` does `from codec_ask_user import strict_consent_gate`, but `codec_ask_user` exposes no such symbol (only `ask()` / `submit_answer()`); it exists solely as a mock in `tests/test_agent_runner.py`. **Verified.**
**Why it matters:** in production the import raises, `_strict_consent` returns not-approved, and any destructive op the LLM *does* flag gets stuck on `blocked_on_destructive` — while the literal-verb consent prompt the design promises **never actually runs**. The gate is dead code kept green by a test mock.
**Fix:** call the real `codec_ask_user.ask(..., destructive=True, destructive_verb=<server-chosen>)`; add an import-time smoke test so a missing symbol fails CI instead of being mocked away.

### B-2 — `permission_gate` + destructive gate are driven by LLM-self-declared flags [CRITICAL]
**What:** the gate only checks paths/domains/consent when `action.touches_path` / `reads_path` / `network_call` / `is_destructive` are True (`codec_agent_runner.py:196,204,216` + `is_destructive` at `:504`), and all four come straight from Qwen's JSON (prompt instructs the model to self-report them, `:270-274`). **Verified.**
**Why it matters:** the manifest is advisory — the LLM emits `touches_path:false` while putting a sensitive path in free-text, and the skill acts on it ungated. Amplified by **prompt injection** (B-2a): skill output is fed back verbatim into the next action prompt (`:715-721`), so attacker-controlled content the agent *reads* can steer it to emit gate-passing actions.
**Fix:** derive resource use server-side from the resolved skill's declared capability + parse the actual path/URL args; never trust model-asserted booleans; default-deny any action whose args can't be statically classified; wrap tool output as a delimited untrusted block.

### B-3 — `/api/agents/*` mutations lack per-agent authz; `/grant` bypasses the path blocklist [CRITICAL]
**What:** `approve`/`reject`/`revise`/`abort`/`resume`/`grant`/`extend_budget` take only `agent_id` from the path with no ownership/role check (`routes/agents.py:326,443,...`); `/grant` appends an arbitrary `value` for any `kind` with **no `_PATH_BLOCKLIST_SUBSTRINGS` / realpath validation**. **Verified** (no per-endpoint gate; only the global dashboard `AuthMiddleware`).
**Why it matters:** any dashboard-authenticated or localhost-foothold caller can approve a pending plan or grant `write_paths=/` to a running agent → arbitrary-write primitive that also defeats the PR-1D blocklist. The global auth + loopback binding (PR-2A) is the only thing standing in front of it.
**Fix:** validate grant `value` through the blocklist + realpath; treat grant-widening as a destructive action requiring consent; consider per-agent ownership if the dashboard ever goes multi-user.

### B-4 — `grants.json` is not covered by the plan-hash tamper check [HIGH]
**What:** run-start verifies `manifest.plan_hash == sha256(plan.json)` (`runner:~819`) but `grants.json` — the file actually loaded to enforce permissions — is never hashed/verified. **Verified.**
**Why it matters:** post-approval edits to `grants.json` (add write_paths/skills/domains) survive every restart and run unchallenged; the documented "tamper → abort" covers the plan but not the enforcement input.
**Fix:** fold a hash of `grants.json` into the approval manifest; re-verify both at run start.

### B-5 — Resume-after-crash loses in-checkpoint history → non-idempotent replay [HIGH]
**What:** `state.json` persists only `current_checkpoint` at checkpoint *completion* (`runner:962-967`); `history` is rebuilt as `[]` on every `_run_agent` (`:865`). A crash mid-checkpoint replays the whole checkpoint from step 0. **Verified.**
**Why it matters:** the documented "worst case: one op re-fires" understates it — a 40-step checkpoint re-runs all 40; non-idempotent skills duplicate work (re-download, re-append, duplicate send) and destructive ops re-hit the (broken) consent path.
**Fix:** persist `history` (or a compacted form) per step/checkpoint + an idempotency marker for executed destructive/network actions; reload on resume.

### B-6 — User replies to a running agent are silently ignored [HIGH]
**What:** `get_unread_user_replies` is defined (`messaging.py:358`) and `POST /api/agents/{id}/messages` writes `user_reply` lines, but the runner **never calls it** (zero call sites). **Verified.**
**Why it matters:** a documented Step-10 feature (feed user replies into the next action) is entirely unwired — answering a running agent does nothing, which is both a UX dead-end and a safety gap (you can't course-correct a misbehaving agent mid-run).
**Fix:** call `get_unread_user_replies(agent_id, since_ts)` in the checkpoint loop, inject into history, advance `since_ts` (track a consumed-offset, not a float ts — see B-20).

### B-7 — No single state-machine authority (cross-process status races) [HIGH]
**What:** the daemon (`_atomic_set_status`) and the PWA (`set_status`, `plan.py:654-666`) both write the same `manifest.json` status cross-process with only per-write atomicity and **no flock**; the `current in _VALID_TRANSITIONS` CAS check is not atomic with the write.
**Why it matters:** a daemon `running→blocked` and a concurrent PWA `running→paused` race on the read → last-writer-wins drops a transition. Unlike `audit.log` (flock'd in PR-4E), agent state has no cross-process serialization, so the transition-validity guarantee is illusory under concurrency.
**Fix:** one `flock`-guarded compare-and-swap status helper in `codec_agent_plan`, used identically by daemon and routes.

### B-8 — `blocked_on_destructive` is an unrecoverable dead-end [HIGH]
**What:** a destructive-consent timeout → `blocked_on_destructive`, but the daemon tick only auto-resumes `approved`/`running`/`blocked_on_qwen`, and `/grant` only unblocks `blocked_on_permission` (`runner:1119-1156`, `routes:~460`).
**Why it matters:** an agent that hits the (broken, B-1) destructive gate is stuck with no re-prompt and no documented recovery action — it silently dies overnight.
**Fix:** add a `blocked_on_destructive` resume branch that re-issues consent, and surface a PWA action for it.

### B-9 — Approval is a non-transactional multi-file write [HIGH]
**What:** `approve_plan` writes `grants.json` → manifest+`plan_hash` → `set_status("approved")` as three sequential atomic writes (`plan.py:~1005-1012`). A crash between leaves grants written but no hash (daemon aborts on missing hash) or status stuck at `awaiting_approval`; the recovery transition `awaiting_approval→aborted` isn't even legal.
**Why it matters:** a half-completed approval silently bricks the agent with no retry path.
**Fix:** write `plan_hash` into the same manifest object `set_status` persists (one write); add a daemon reconciliation pass (status `approved` but hash missing → recompute).

### B-10 — Agent state files are world-readable [MEDIUM]
**What:** `plan.json` / `grants.json` / `messages.jsonl` / `agent_silence.json` are written with the default umask (no `0o600`), unlike `audit.log` / plugins which the repo explicitly chmods 600 (`plan.py:146-154`, `messaging.py:86-104`).
**Why it matters:** plan descriptions, user replies, and skill results (file contents, fetched data) sit in `~/.codec/agents/<id>/` readable by any local user/process — inconsistent with the repo's hardened secret-storage posture.
**Fix:** `os.open(..., 0o600)` + `0o700` dirs, matching the audit-log pattern.

### B-11 — `notifications.json` write bypasses the cross-process flock contract [MEDIUM]
**What:** `post_message` does an un-locked read-modify-write of `notifications.json` via its own `_atomic_write_json` (`messaging.py:~215`), while every other writer (scheduler, heartbeat, ask_user, dashboard) goes through `codec_jsonstore` + `file_lock` (PR-4C). **Verified** (different helper, no shared lock).
**Why it matters:** the runner is a separate process; a concurrent agent banner + scheduler notification = lost update (atomic-rename preserves file integrity but not the read-modify-write window).
**Fix:** route through `codec_jsonstore.atomic_write_json` + `file_lock`.

### B-12 — `_qwen_next_action` conflates four concerns + reverse-engineers state from skill strings [MEDIUM]
**What:** one function does prompt composition, history trim, **regex reconstruction of multi-file iteration state out of `file_ops` result *strings*** (`runner:379-425`), brace-balanced JSON extraction, and Action construction.
**Why it matters:** any change to a skill's output format silently breaks multi-file checkpoints with no error, and the function is near-untestable as a unit.
**Fix:** split into `build_prompt()` / `parse_action(text)` / a typed iteration tracker that consumes structured history (have `_run_skill` record a typed result, not a 500-char slice).

### B-13 — No plan/grants schema migration ladder [MEDIUM]
**What:** `plan_from_dict` hard-rejects `schema != 1` with `ValueError` (`plan.py:122-123`); `grants.json` carries a bare `"schema":1` with no reader check. No migration analogue to `codec_config._CONFIG_MIGRATIONS`.
**Why it matters:** the moment `PLAN_SCHEMA_VERSION` bumps to 2, every existing on-disk plan becomes permanently unloadable — every in-flight + historical Project breaks on upgrade.
**Fix:** add a `_migrate_plan_vN(d)` ladder invoked before the strict check; give grants a real version constant.

### B-14 — Step budget bounds checkpoints, not LLM calls; `extend_budget` is unbounded [MEDIUM]
**What:** each budgeted step can fire ≥2 `_qwen_next_action` calls (correction nudge + retry, `runner:~689,492`), so a model that never emits `checkpoint_done` burns up to ~4× the intended calls; `extend_budget` adds up to 100 steps per call with no cumulative ceiling (`routes:~479`).
**Why it matters:** the step budget is the only backstop against a runaway/looping agent; both the multiplier and unbounded extension defeat it.
**Fix:** count every `_qwen_next_action` against a hard cap; cap cumulative budget overrides; gate `extend_budget` behind consent/authz.

### B-15 — `open-folder` runs `open` on a stored path with no revalidation [MEDIUM]
**What:** `routes/agents.py:~590` runs `subprocess.Popen(["open", project_dir])` on `manifest.project_dir` (argv-form, so no shell injection) with no realpath confinement.
**Why it matters:** `project_dir` is influenced by the (slugified) title or a tampered manifest; `open` will launch apps/bundles for non-dir targets — a low-effort local-trigger primitive when combined with B-3's missing authz.
**Fix:** realpath-confine `project_dir` under the configured project root; reject symlinks.

### B-16 — Two agent runtimes share one URL namespace + storage dir [MEDIUM]
**What:** in-memory crew/custom agents (`_agent_jobs`, lost on restart) and on-disk Phase-3 Projects (run by the separate `codec-agent-runner` PM2 process) share the `/api/agents/*` prefix and `~/.codec/agents/` with no shared lifecycle, status vocabulary, or persistence model.
**Why it matters:** latent collision (a custom-agent slug could shadow a Project dir) + permanent contributor confusion about which system a change touches.
**Fix:** namespace them (`/api/crews/*` vs `/api/projects/*`) and/or migrate crew jobs onto the on-disk state layer.

### B-17 — Outbound channel dispatch uses a plaintext token + exfils agent content [LOW]
**What:** `post_message` best-effort POSTs agent title+body to Telegram using a token from plaintext `config.json` (`messaging.py:~304-338`), outside the Keychain hardening (PR-2B).
**Why it matters:** agent output (which can include read file contents / fetched data) leaves the machine, and the token isn't Keychain-stored — a local-first / exfil tension.
**Fix:** route channel secrets through `codec_keychain`; gate outbound dispatch of agent content behind explicit per-agent opt-in.

### B-18 — `_path_allowed` collapses glob grants to their directory root [LOW]
**What:** a grant of `~/Documents/*.md` is reduced to root `~/Documents` (`runner:160-170`, documented trade-off).
**Why it matters:** any write-path grant silently authorizes the whole parent tree, widening blast radius (acceptable only because realpath is applied).
**Fix:** match against the full glob (`fnmatch` on realpath) instead of collapsing to the directory.

### B-19 — Dataclass splat of user/LLM JSON raises unhandled `TypeError` [LOW]
**What:** `plan_from_dict` does `Checkpoint(**cp)` / `PermissionManifest(**d[...])` on raw JSON (`plan.py:124`); an extra key (LLM emits `priority`, or a malformed PWA `revise` payload) raises `TypeError` not all callers catch.
**Why it matters:** malformed input yields a 500 / unhandled error instead of a clean 4xx.
**Fix:** filter to known dataclass fields before splatting, or wrap in a uniform `PlanValidationError`.

### B-20 — Reply dedup by strict-`>` millisecond timestamp [LOW]
**What:** `get_unread_user_replies` dedups by `ts > since_ts` on ms ISO timestamps (`messaging.py:379`).
**Why it matters:** two replies in the same ms (or `since_ts` taken from the last-read reply's ts) can drop or double-read a reply.
**Fix:** track a consumed-offset (byte position / monotonic reply index), not a float comparison. (Pairs with B-6.)

---

## Test-coverage gaps (fixes must land with these)

The suite is well-engineered for the happy path but **the security + crash-recovery boundaries are thin**: no test exercises a destructive op with `codec_ask_user` unavailable (B-1), a double-unauthorized action through `_run_agent` (B-2), `/api/agents/*` without auth (B-3), grants tamper (B-4), crash-resume history loss (B-5), or malformed/fenced Qwen output. The permission gate + plan-hash check are security boundaries that warrant **mutation testing** (`mutmut` on `permission_gate` + the hash-verify block; target >85% kill). Concurrency tests use `time.sleep(0.3)` (flaky — prefer a `threading.Barrier`).

---

## Pilot (deferred)

CODEC Pilot (`pilot/`: `pilot_runner.py` FastAPI :8094, `pilot_agent.py` ReAct loop, `replay.py` XPath→CSS→LLM rescue, `compiler.py` trace→skill, `hitl.py`) is a **separate checkout (`~/codec/pilot/`) not present in this repo**, so it could not be reviewed. Pilot is high-risk (headless Chromium on CDP, auto-compiled replay skills, an HTTP API exposed via a Cloudflare tunnel) and should get the same 4-pass review once the tree is available — flagged in `docs/HANDOFF-MICKAEL.md`. Likely focus: the `pilot_*.py` skill auto-compile/approval gate (does it go through PR-1A's load-time AST gate?), CDP exposure, the :8094 API authz, and selector-rescue prompt-injection.

---

## Suggested Wave 7 burn-down (sized like the other waves)

1. **PR-7A (CRITICAL):** B-1 wire the real `codec_ask_user.ask` destructive gate + import smoke test. *(small, high-value, fail-safe today but the consent UX is dead.)*
2. **PR-7B (CRITICAL):** B-2 server-side resource derivation for `permission_gate` (stop trusting LLM booleans) + delimited untrusted tool output. *(the core fix; design-first.)*
3. **PR-7C (CRITICAL/HIGH):** B-3 + B-4 — blocklist/realpath-validate `/grant`, hash `grants.json` into the tamper check.
4. **PR-7D (HIGH):** B-7 single flock'd CAS status helper (also resolves B-9 approval-crash + the illegal-abort transition).
5. **PR-7E (HIGH):** B-5 + B-6 + B-8 — persist resume history + idempotency markers, wire user-reply pickup, recover `blocked_on_destructive`.
6. **PR-7F (MEDIUM cluster):** B-10 0600 perms · B-11 flock notifications · B-13 schema migration ladder.
7. Remaining MEDIUM/LOW (B-12, B-14, B-15, B-16, B-17, B-18, B-19, B-20) as cleanup PRs.
8. **Pilot audit** once the `pilot/` tree is provided.

Each fix lands design-first → TDD → CI-green, same as Waves 4–6. **B-1/B-2/B-3 should be treated as the highest-priority remaining work in all of Phase 1** — they're the live security boundary of an agent that executes autonomously on the user's machine.
