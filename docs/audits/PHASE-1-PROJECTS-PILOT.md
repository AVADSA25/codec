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

> **✅ FIXED by PR-7A (2026-05-24).** `_strict_consent` now routes through the real `codec_ask_user.ask(destructive=True, destructive_verb="confirm", …)` and maps its return (`TIMEOUT_SENTINEL`/`DISABLED_SENTINEL` → blocked, never approved; verb-matched answer → approved). Fail-safe: any error/timeout/disabled → not approved. 4 regression tests (`tests/test_strict_consent_fix.py`) exercise the **real** body (the gap B-1 left) + assert the phantom symbol can't return; the 51 existing runner tests stay green. *(B-2 — the gate trusting LLM-self-declared flags — is the separate PR-7B and is what makes this consent prompt actually fire for currently-unflagged ops.)*

**What:** `codec_agent_runner.py:529` does `from codec_ask_user import strict_consent_gate`, but `codec_ask_user` exposes no such symbol (only `ask()` / `submit_answer()`); it exists solely as a mock in `tests/test_agent_runner.py`. **Verified.**
**Why it matters:** in production the import raises, `_strict_consent` returns not-approved, and any destructive op the LLM *does* flag gets stuck on `blocked_on_destructive` — while the literal-verb consent prompt the design promises **never actually runs**. The gate is dead code kept green by a test mock.
**Fix:** call the real `codec_ask_user.ask(..., destructive=True, destructive_verb=<server-chosen>)`; add an import-time smoke test so a missing symbol fails CI instead of being mocked away.

### B-2 — `permission_gate` + destructive gate are driven by LLM-self-declared flags [CRITICAL]

> **🟡 PARTIALLY FIXED by PR-7B (2026-05-24).** Destructiveness is now **server-derived (OR-only)**: `_effective_destructive(action) = LLM_flag OR _server_destructive_signal(action)`, where the server signal = the skill being in `codec_config._HTTP_BLOCKED` (shell/code/process control) OR an irreversible-intent verb (delete/remove/send/transfer/pay/kill/…) in the task. So the agent **can no longer skip the consent gate by emitting `is_destructive=false`** on a dangerous skill or an obviously destructive task — the LLM can only *upgrade* risk, never downgrade. 6 tests (`tests/test_derive_destructive.py`); the 51 runner tests unaffected. **Still open (B-2 remainder):** the `touches_path`/`reads_path`/`network_call` *category* + their path/domain *values* are still LLM-declared — closing that needs a per-skill **capability model** (curated table vs new `SKILL_CAPABILITIES` metadata across ~76 skills), an XL design-first change with a real architecture decision for Mickael. Tracked in `docs/HANDOFF-MICKAEL.md`.

**What:** the gate only checks paths/domains/consent when `action.touches_path` / `reads_path` / `network_call` / `is_destructive` are True (`codec_agent_runner.py:196,204,216` + `is_destructive` at `:504`), and all four come straight from Qwen's JSON (prompt instructs the model to self-report them, `:270-274`). **Verified.**
**Why it matters:** the manifest is advisory — the LLM emits `touches_path:false` while putting a sensitive path in free-text, and the skill acts on it ungated. Amplified by **prompt injection** (B-2a): skill output is fed back verbatim into the next action prompt (`:715-721`), so attacker-controlled content the agent *reads* can steer it to emit gate-passing actions.
**Fix:** derive resource use server-side from the resolved skill's declared capability + parse the actual path/URL args; never trust model-asserted booleans; default-deny any action whose args can't be statically classified; wrap tool output as a delimited untrusted block.

### B-3 — `/api/agents/*` mutations lack per-agent authz; `/grant` bypasses the path blocklist [CRITICAL]

> **🟡 PARTIALLY FIXED by PR-7C (2026-05-24).** The arbitrary-write part is closed: `grant_permission` now runs `read_paths`/`write_paths` values through `_grant_path_unsafe` → **400** for a `..` traversal, a `_cap._is_path_blocklisted` sensitive path (`~/.ssh`, `~/.codec/*`, `/etc`, …), or an over-broad root (`/`, bare `$HOME`, `/usr`, `/System`, …). So `POST /grant {write_paths:"/"}` is refused and not saved. 22 tests (`tests/test_grant_blocklist.py`); existing endpoint tests green. **Still open (deferred, noted in HANDOFF):** per-agent *ownership* authz — any dashboard-authenticated caller can still grant to any agent. That only matters if the dashboard goes multi-user; today it's single-user behind the global `AuthMiddleware` + loopback binding (PR-2A/2D).

**What:** `approve`/`reject`/`revise`/`abort`/`resume`/`grant`/`extend_budget` take only `agent_id` from the path with no ownership/role check (`routes/agents.py:326,443,...`); `/grant` appends an arbitrary `value` for any `kind` with **no `_PATH_BLOCKLIST_SUBSTRINGS` / realpath validation**. **Verified** (no per-endpoint gate; only the global dashboard `AuthMiddleware`).
**Why it matters:** any dashboard-authenticated or localhost-foothold caller can approve a pending plan or grant `write_paths=/` to a running agent → arbitrary-write primitive that also defeats the PR-1D blocklist. The global auth + loopback binding (PR-2A) is the only thing standing in front of it.
**Fix:** validate grant `value` through the blocklist + realpath; treat grant-widening as a destructive action requiring consent; consider per-agent ownership if the dashboard ever goes multi-user.

### B-4 — `grants.json` is not covered by the plan-hash tamper check [HIGH]

> **✅ FIXED by PR-7E (2026-05-24).** New `compute_grants_hash` / `set_grants_hash` (mirroring `compute_plan_hash`): `approve_plan` stores `manifest.grants_hash`, the `/api/agents/grant` endpoint re-syncs it after every legit grant, and `_run_agent` verifies it at run start — **mismatch → abort (`grants_tampered`)**. Absence heals-forward (recompute + store + warn) rather than aborts, so agents approved before this field existed aren't broken on upgrade (trade-off documented in the design doc). 4 tests (`tests/test_grants_tamper.py`); 135 agent tests stay green; ruff delta zero.

**What:** run-start verifies `manifest.plan_hash == sha256(plan.json)` (`runner:~819`) but `grants.json` — the file actually loaded to enforce permissions — is never hashed/verified. **Verified.**
**Why it matters:** post-approval edits to `grants.json` (add write_paths/skills/domains) survive every restart and run unchallenged; the documented "tamper → abort" covers the plan but not the enforcement input.
**Fix:** fold a hash of `grants.json` into the approval manifest; re-verify both at run start.

### B-5 — Resume-after-crash loses in-checkpoint history → non-idempotent replay [HIGH]
**What:** `state.json` persists only `current_checkpoint` at checkpoint *completion* (`runner:962-967`); `history` is rebuilt as `[]` on every `_run_agent` (`:865`). A crash mid-checkpoint replays the whole checkpoint from step 0. **Verified.**
**Why it matters:** the documented "worst case: one op re-fires" understates it — a 40-step checkpoint re-runs all 40; non-idempotent skills duplicate work (re-download, re-append, duplicate send) and destructive ops re-hit the (broken) consent path.
**Fix:** persist `history` (or a compacted form) per step/checkpoint + an idempotency marker for executed destructive/network actions; reload on resume.

> **✅ FIXED by PR-7I (2026-05-24).** Two parts. **(1) Incremental history persistence:** `_execute_checkpoint` now writes the running `history` to `state.json` (`cp_history` + `cp_in_progress`) after **every step** via `_persist_checkpoint_progress` (load-modify-save, preserves `last_reply_ts`/`step_budget_overrides`). `_run_agent` reloads it when it re-enters the in-progress checkpoint, so resume continues mid-checkpoint instead of replaying from step 0. **(2) At-most-once destructive guard:** before executing an action where `_effective_destructive()` is true (the same server-derived predicate the consent gate uses, so irreversible network sends are covered), a 16-hex `_fingerprint(cp_id, skill, task)` is appended to a persisted `executed_destructive` ledger **before** `_run_skill` runs. On resume, a fingerprint already in the ledger means the op already fired (or may have) → it is **skipped, not re-executed**, and consent is **not** re-prompted; a `[SKIPPED ON RESUME …]` history entry tells the model to advance. So an irreversible op fires **at most once** across a crash. Per-checkpoint progress is cleared on checkpoint completion (the completion save is now a load-modify-save, fixing the prior full-overwrite that silently dropped `last_reply_ts`/`step_budget_overrides`). Idempotent network GETs are intentionally still retryable. 5 tests (`tests/test_resume_history.py`); 120 agent suites green; full suite zero new failures. Design: `docs/PR7I-RESUME-HISTORY-DESIGN.md`.

### B-6 — User replies to a running agent are silently ignored [HIGH]

> **✅ FIXED by PR-7F (2026-05-24).** `_run_agent` now calls `_drain_user_replies(agent_id, since_ts)` at each checkpoint start, injecting any `user_reply` posted since the last check into the `history` the next `_qwen_next_action` sees (rendered as a `[USER REPLY] …` entry), and advances/persists `state.last_reply_ts` so replies aren't re-read after a restart. So answering a running agent now actually steers it. 3 tests (`tests/test_user_replies.py`) incl. an integration test asserting the reply reaches the model; 64 runner tests stay green. (The same-millisecond cursor edge is the separate LOW **B-20**.)

**What:** `get_unread_user_replies` is defined (`messaging.py:358`) and `POST /api/agents/{id}/messages` writes `user_reply` lines, but the runner **never calls it** (zero call sites). **Verified.**
**Why it matters:** a documented Step-10 feature (feed user replies into the next action) is entirely unwired — answering a running agent does nothing, which is both a UX dead-end and a safety gap (you can't course-correct a misbehaving agent mid-run).
**Fix:** call `get_unread_user_replies(agent_id, since_ts)` in the checkpoint loop, inject into history, advance `since_ts` (track a consumed-offset, not a float ts — see B-20).

### B-7 — No single state-machine authority (cross-process status races) [HIGH]

> **✅ FIXED by PR-7D (2026-05-24).** `set_status` was already the single writer (the daemon's `_atomic_set_status` wraps it); PR-7D makes its load→validate→write CAS **cross-process atomic** by wrapping it in `codec_jsonstore.file_lock(manifest.json)` — the same flock primitive PR-4E uses for `audit.log`. So a daemon write and a concurrent dashboard write can no longer clobber each other or skip a transition; the `_VALID_TRANSITIONS` check is now atomic with the write. Graceful nullcontext fallback if `codec_jsonstore` is unavailable. 3 tests (`tests/test_status_cas.py`); 110 agent-plan/runner/atomic-status tests stay green. **B-9** (approve_plan's non-transactional 3-file write + the illegal `awaiting_approval→aborted` recovery) is a separate follow-up.

**What:** the daemon (`_atomic_set_status`) and the PWA (`set_status`, `plan.py:654-666`) both write the same `manifest.json` status cross-process with only per-write atomicity and **no flock**; the `current in _VALID_TRANSITIONS` CAS check is not atomic with the write.
**Why it matters:** a daemon `running→blocked` and a concurrent PWA `running→paused` race on the read → last-writer-wins drops a transition. Unlike `audit.log` (flock'd in PR-4E), agent state has no cross-process serialization, so the transition-validity guarantee is illusory under concurrency.
**Fix:** one `flock`-guarded compare-and-swap status helper in `codec_agent_plan`, used identically by daemon and routes.

### B-8 — `blocked_on_destructive` is an unrecoverable dead-end [HIGH]

> **✅ FIXED by PR-7G (2026-05-24).** The `blocked_on_destructive` branch set the status but posted **no notification** (unlike `blocked_on_permission`), so the user had no banner + no recovery affordance. PR-7G posts an `agent_blocked` notification with **Resume** (→ `/api/agents/{id}/resume`) + **Abort** actions: Resume re-runs from the checkpoint and re-issues the consent prompt (B-1), which is now a working path (legal transition + daemon re-run). Auto-re-running is deliberately not done (would spam consent). 1 test in `tests/test_destructive_recovery.py`. **Same PR also completed B-2 in the loop** — `_execute_checkpoint` now gates on `_effective_destructive(action)` (line 763), not the raw `action.is_destructive`, so an unflagged-but-server-destructive action actually reaches the gate (a gap left by PR-7B, which had only put the derivation *inside* `_enforce_destructive_gate`).

**What:** a destructive-consent timeout → `blocked_on_destructive`, but the daemon tick only auto-resumes `approved`/`running`/`blocked_on_qwen`, and `/grant` only unblocks `blocked_on_permission` (`runner:1119-1156`, `routes:~460`).
**Why it matters:** an agent that hits the (broken, B-1) destructive gate is stuck with no re-prompt and no documented recovery action — it silently dies overnight.
**Fix:** add a `blocked_on_destructive` resume branch that re-issues consent, and surface a PWA action for it.

### B-9 — Approval is a non-transactional multi-file write [HIGH]
**What:** `approve_plan` writes `grants.json` → manifest+`plan_hash` → `set_status("approved")` as three sequential atomic writes (`plan.py:~1005-1012`). A crash between leaves grants written but no hash (daemon aborts on missing hash) or status stuck at `awaiting_approval`; the recovery transition `awaiting_approval→aborted` isn't even legal.
**Why it matters:** a half-completed approval silently bricks the agent with no retry path.
**Fix:** write `plan_hash` into the same manifest object `set_status` persists (one write); add a daemon reconciliation pass (status `approved` but hash missing → recompute).

> **✅ FIXED by PR-7H (2026-05-24).** `set_status` gained an `extra=` dict merged into the manifest **inside the same `_status_lock` flock block** as the status transition (one `save_manifest`). `approve_plan` now stamps `plan_hash` + `grants_hash` + `approved_at` AND flips status to `approved` in that **single atomic write** — collapsing the prior two manifest writes. The invariant is now structural: `status == "approved"` cannot coexist with a missing hash, so there is no crash window that bricks run-start tamper detection. The illegal recovery path is also closed: `_VALID_TRANSITIONS` now allows `aborted` out of all three pre-approval states (`draft_pending`, `awaiting_approval`, `revised`) — a draft the LLM mangled or an approval the user no longer wants can be cleanly terminated instead of orphaned. Additive transitions only (no edge removed, per AGENTS.md §10). 4 tests (`tests/test_atomic_approval.py`); 109 agent-plan/runner/destructive/atomic tests stay green; full suite zero new failures. Design: `docs/PR7H-ATOMIC-APPROVAL-DESIGN.md`. (The daemon reconciliation pass suggested above is now unnecessary — atomicity makes the inconsistent state unreachable.)

### B-10 — Agent state files are world-readable [MEDIUM]
**What:** `plan.json` / `grants.json` / `messages.jsonl` / `agent_silence.json` are written with the default umask (no `0o600`), unlike `audit.log` / plugins which the repo explicitly chmods 600 (`plan.py:146-154`, `messaging.py:86-104`).
**Why it matters:** plan descriptions, user replies, and skill results (file contents, fetched data) sit in `~/.codec/agents/<id>/` readable by any local user/process — inconsistent with the repo's hardened secret-storage posture.
**Fix:** `os.open(..., 0o600)` + `0o700` dirs, matching the audit-log pattern.

> **✅ FIXED by PR-7J (2026-05-24).** The two module-local `_atomic_write_json` helpers (`plan.py` + `messaging.py`) and `messaging.py:_append_jsonl` now create files via `os.open(..., O_CREAT, 0o600)` (bypasses umask → never a world-readable window), defensively `chmod 0o600` (covers stale tmp / pre-existing logs), and `chmod 0o700` the parent dir. Covers `plan.json` / `state.json` / `manifest.json` / `grants.json` / `agent_global_grants.json` / `agent_silence.json` / `messages.jsonl`, plus `notifications.json` (now written via `codec_jsonstore.atomic_write_json`, which also chmods 0600 — see B-11). Try/except-wrapped for RO/FUSE mounts (same pattern as PR-2E). 4 perms tests (`tests/test_state_hardening.py`). Design: `docs/PR7J-STATE-HARDENING-DESIGN.md`.

### B-11 — `notifications.json` write bypasses the cross-process flock contract [MEDIUM]
**What:** `post_message` does an un-locked read-modify-write of `notifications.json` via its own `_atomic_write_json` (`messaging.py:~215`), while every other writer (scheduler, heartbeat, ask_user, dashboard) goes through `codec_jsonstore` + `file_lock` (PR-4C). **Verified** (different helper, no shared lock).
**Why it matters:** the runner is a separate process; a concurrent agent banner + scheduler notification = lost update (atomic-rename preserves file integrity but not the read-modify-write window).
**Fix:** route through `codec_jsonstore.atomic_write_json` + `file_lock`.

> **✅ FIXED by PR-7J (2026-05-24).** `post_message` now wraps the **whole** `notifications.json` read → batch-merge → write in `_notifications_lock()` (= `codec_jsonstore.file_lock(_NOTIFICATIONS_PATH)`, nullcontext fallback for headless/CI — same shape as `_status_lock`), and writes via `codec_jsonstore.atomic_write_json`. So the runner's banner write now shares the one cross-process critical section every other notifications writer (scheduler/heartbeat/ask_user/dashboard) already uses — no more lost-update window. 1 flock test (`tests/test_state_hardening.py`) spying on `codec_jsonstore.file_lock`. Design: `docs/PR7J-STATE-HARDENING-DESIGN.md`.

### B-12 — `_qwen_next_action` conflates four concerns + reverse-engineers state from skill strings [MEDIUM]
**What:** one function does prompt composition, history trim, **regex reconstruction of multi-file iteration state out of `file_ops` result *strings*** (`runner:379-425`), brace-balanced JSON extraction, and Action construction.
**Why it matters:** any change to a skill's output format silently breaks multi-file checkpoints with no error, and the function is near-untestable as a unit.
**Fix:** split into `build_prompt()` / `parse_action(text)` / a typed iteration tracker that consumes structured history (have `_run_skill` record a typed result, not a 500-char slice).

### B-13 — No plan/grants schema migration ladder [MEDIUM]
**What:** `plan_from_dict` hard-rejects `schema != 1` with `ValueError` (`plan.py:122-123`); `grants.json` carries a bare `"schema":1` with no reader check. No migration analogue to `codec_config._CONFIG_MIGRATIONS`.
**Why it matters:** the moment `PLAN_SCHEMA_VERSION` bumps to 2, every existing on-disk plan becomes permanently unloadable — every in-flight + historical Project breaks on upgrade.
**Fix:** add a `_migrate_plan_vN(d)` ladder invoked before the strict check; give grants a real version constant.

> **✅ FIXED by PR-7K (2026-05-24).** `plan_from_dict` now runs `_migrate_plan_dict(d)` — an ordered `_PLAN_MIGRATIONS` ladder (analogue of `codec_config._CONFIG_MIGRATIONS`, keyed by source version) — **before** the strict version check. The ladder is empty at v1 (first version), but the mechanism is in place so a future `PLAN_SCHEMA_VERSION` bump upgrades on-disk plans instead of bricking them; a schema newer than current (or a ladder gap) still rejects cleanly. Per-agent grants now carry `GRANTS_SCHEMA_VERSION` (was a bare literal `"schema": 1`). The grants *load-time* migration hook was intentionally deferred — `compute_grants_hash` (B-4) hashes the loaded grants, so a future grants migration must re-sync the manifest hash (documented inline). 3 ladder tests (`tests/test_plan_loading.py`, incl. one registering a synthetic migration). Design: `docs/PR7K-PLAN-LOADING-DESIGN.md`.

### B-14 — Step budget bounds checkpoints, not LLM calls; `extend_budget` is unbounded [MEDIUM]
**What:** each budgeted step can fire ≥2 `_qwen_next_action` calls (correction nudge + retry, `runner:~689,492`), so a model that never emits `checkpoint_done` burns up to ~4× the intended calls; `extend_budget` adds up to 100 steps per call with no cumulative ceiling (`routes:~479`).
**Why it matters:** the step budget is the only backstop against a runaway/looping agent; both the multiplier and unbounded extension defeat it.
**Fix:** count every `_qwen_next_action` against a hard cap; cap cumulative budget overrides; gate `extend_budget` behind consent/authz.

### B-15 — `open-folder` runs `open` on a stored path with no revalidation [MEDIUM]
**What:** `routes/agents.py:~590` runs `subprocess.Popen(["open", project_dir])` on `manifest.project_dir` (argv-form, so no shell injection) with no realpath confinement.
**Why it matters:** `project_dir` is influenced by the (slugified) title or a tampered manifest; `open` will launch apps/bundles for non-dir targets — a low-effort local-trigger primitive when combined with B-3's missing authz.
**Fix:** realpath-confine `project_dir` under the configured project root; reject symlinks.

> **✅ FIXED by PR-7L (2026-05-24).** New `_project_dir_confined()` realpath-resolves `project_dir` and the configured project root (`_cap._PROJECT_ROOT` = `~/codec-projects`) and requires the former to be at/under the latter, AND rejects symlinked project dirs outright (`os.path.islink`). `open_folder` calls it after the `isdir` check; a path outside the root returns **400** (not `open`) + emits an `open_folder_blocked` audit. The read-only listing endpoints (`get_artifacts` / `list_agent_files`) are lower-risk (no exec) and left as a documented follow-up. 3 tests (`tests/test_path_safety.py`). Design: `docs/PR7L-PATH-SAFETY-DESIGN.md`.

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

> **✅ FIXED by PR-7L (2026-05-24).** `_path_allowed` now, for a glob grant, requires the action's realpath to **both** be contained under the realpath'd grant root (PR-1D's safety, kept) **and** match the realpath-anchored glob via `fnmatch` — so `~/Documents/*.md` authorizes `.md` files, not `secrets.key`. This **only tightens** (the fnmatch test is layered on top of containment; nothing PR-1D accepted-for-safety is newly accepted). Invariants preserved: `..` rejection + action realpath unchanged; plain directory grants still authorize their subtree; `**` grants (incl. the production default `{project_dir}/**`) still match recursively because fnmatch `*`→`.*` crosses `/`. 4 tests (`tests/test_path_safety.py`, incl. regression guards for `**`/plain-dir/`..`). Design: `docs/PR7L-PATH-SAFETY-DESIGN.md`.

### B-19 — Dataclass splat of user/LLM JSON raises unhandled `TypeError` [LOW]
**What:** `plan_from_dict` does `Checkpoint(**cp)` / `PermissionManifest(**d[...])` on raw JSON (`plan.py:124`); an extra key (LLM emits `priority`, or a malformed PWA `revise` payload) raises `TypeError` not all callers catch.
**Why it matters:** malformed input yields a 500 / unhandled error instead of a clean 4xx.
**Fix:** filter to known dataclass fields before splatting, or wrap in a uniform `PlanValidationError`.

> **✅ FIXED by PR-7K (2026-05-24).** Checkpoint/PermissionManifest construction is now `_checkpoint_from_dict` / `_manifest_from_dict`, which filter each raw dict to the dataclass's known fields (`_CHECKPOINT_FIELDS` / `_MANIFEST_FIELDS`) before splatting — an extra LLM/PWA key (e.g. `priority`, `max_spend_usd`) is dropped instead of raising. `plan_from_dict`'s construction is wrapped so a *missing required* key raises a clean `ValueError` (which `PlanValidationError` extends and every caller's `except (KeyError, ValueError, TypeError)` already handles) rather than a bare `TypeError` escaping `load_plan` → a 500. 3 tests (`tests/test_plan_loading.py`). Design: `docs/PR7K-PLAN-LOADING-DESIGN.md`.

### B-20 — Reply dedup by strict-`>` millisecond timestamp [LOW]
**What:** `get_unread_user_replies` dedups by `ts > since_ts` on ms ISO timestamps (`messaging.py:379`).
**Why it matters:** two replies in the same ms (or `since_ts` taken from the last-read reply's ts) can drop or double-read a reply.
**Fix:** track a consumed-offset (byte position / monotonic reply index), not a float comparison. (Pairs with B-6.)

> **✅ FIXED by PR-7M (2026-05-24).** `get_unread_user_replies(agent_id, since_index)` now dedups by a monotonic consumed-offset (the i-th `user_reply` line has index i; records with index ≥ `since_index` are returned) — no timestamp parsing at all, so same-millisecond replies can't be dropped/double-read. `_drain_user_replies` returns `(entries, since_index + len(replies))`, advancing by the reply COUNT (so an empty-body reply still advances and isn't re-read forever). The runner state key is now `replies_consumed` (int); a legacy `last_reply_ts` heals forward via `count_user_replies` (treat all pre-upgrade replies as consumed → none re-injected). 5 tests (`tests/test_reply_offset.py`) + the PR-7F drain tests updated to the offset contract. Design: `docs/PR7M-REPLY-OFFSET-DESIGN.md`.

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

> **Actually landed (the real PR sequence diverged from the suggestion above):**
> PR-7A = B-1 · PR-7B + PR-7G = B-2 (server-side derivation, then loop enforcement) · PR-7C = B-3 · PR-7E = B-4 · PR-7D = B-7 (flock CAS only — it did **not** also fix B-9 as originally suggested) · PR-7F = B-6 (user-reply pickup) · PR-7G = B-8 · **PR-7H = B-9** (atomic approval write + pre-approval abort) · **PR-7I = B-5** (resume keeps in-checkpoint history + at-most-once destructive replay). **EVERY Audit-B CRITICAL/HIGH is now closed: B-1, B-2, B-3, B-4, B-5, B-6, B-7, B-8, B-9.** MEDIUM/LOW burn-down: **PR-7J = B-10 + B-11** (0600 perms + notifications flock) · **PR-7K = B-13 + B-19** (plan schema migration ladder + dataclass-splat guard) · **PR-7L = B-15 + B-18** (open-folder realpath confinement + glob-grant precision) · **PR-7M = B-20** (reply dedup by consumed-offset). Remaining: B-12, B-14, B-16, B-17 + the Pilot audit (needs the `~/codec/pilot/` checkout).
