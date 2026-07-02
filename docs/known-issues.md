# Known issues — deferred, not fixed

Intentionally-deferred bugs and test failures with documented status, so we don't lose track. Each entry: file path / symbol or test, what's broken, why we deferred it, and the revisit-when target.

---

## Pre-existing test failures from main (Phase 1 Step 1 audit)

The 20 pre-existing failures from `pytest tests/ --ignore=tests/test_smoke.py` were classified in [`docs/PHASE1-STEP1-PREMERGE-AUDIT.md`](PHASE1-STEP1-PREMERGE-AUDIT.md). All are pre-existing on `main` and unrelated to the audit-unification work. They remain failing on `main` after the merge of PR #3.

The single most likely-to-bite-us-soon entry from that list is the `_safe_task` regex bug below; the others are either documentation drift, references to functions/symbols that no longer exist, or test/implementation mismatches in unrelated subsystems.

### `codec.py:255` — `_safe_task` osascript variable name fails the regex sanitizer test

| field | value |
|---|---|
| **file** | `codec.py` |
| **line** | 255 (after PR #3 merge — was 253 on the parent commit; shifted by 2 lines from the import-block edit) |
| **symbol** | local variable `_safe_task` inside `_dispatch_inner()` |
| **failing test** | `tests/test_security.py::test_osascript_inputs_sanitized` |
| **what's broken** | The test reads `codec.py`, regex-finds every variable name interpolated into a `display notification "..."` osascript call, and asserts the variable's name `startswith("safe_")`. The variable in question (`_safe_task`) is in fact properly sanitized — it runs `task[:50].replace('\\', '\\\\').replace('"', '\\"')` before interpolation. The bug is in the **naming convention**: the test wants the prefix `safe_` (no leading underscore), the implementation uses `_safe_` (leading underscore for module-private convention). |
| **status** | **deferred-not-fixed** |
| **why deferred** | (a) the variable IS sanitized — it's a naming-convention disagreement between the test and the implementation, not a real escape vector. (b) Renaming `_safe_task` is an unrelated change and would have padded PR #3 with noise outside its scope. (c) The test has been failing on `main` since at least the 20-pre-existing snapshot — not a regression introduced by audit-unification. |
| **revisit when** | Phase 1 Step 2 or any future PR that legitimately edits `_dispatch_inner`. Rename the local to `safe_task` (drop the leading underscore) and re-run the test — should flip green with no other changes. |
| **risk if left unfixed** | Low. The escape pattern is correct; the test's strictness on naming gives a false-negative warning that's already documented as known-failing. Anyone adding a new osascript interpolation should still use the `safe_` prefix per the test's intent. |

---

## Phase 1 Step 1 sign-off

> **Phase 1 Step 1 — production-stable as of 2026-05-01T09:48:43+02:00 (T+24h post-merge).** Merge commit: `45d4aa7`.

**Samples captured:** T+0 (09:23 GMT+2, ok), T+8h (17:42 GMT+2, ok), T+24h (09:48 GMT+2 next day, ok). T+4h / T+12h / T+16h / T+20h were missed (operator asleep). Each captured sample showed status=ok per the §5.4 rubric. Trailing-30m windows had `with_duration=0` in every captured sample (no claude.ai → MCP traffic in the sample windows; this is a low-traffic personal workstation), so latency comparison vs the 987.96 ms / 1907.78 ms anchor never had a quantitative match — but service health stayed green for the full 24-hour period and no production incidents were reported.

**Sign-off rationale:**
- All captured samples within the §5.4 `ok` flag rubric.
- Zero `test_audit_concurrent_no_corruption` failures observed; no audit log corruption surfaced.
- Zero orphan-cid spikes.
- 24h elapsed without a revert event; no operator intervention required.
- The `service_down` lifecycle emits visible at T+0 (Whisper / Kokoro / Vision intermittents) are previously-hidden events now visible per design intent (§0), not a regression.

**Methodology gap acknowledged:** the missed T+12h / T+16h / T+20h sample slots are a process gap — the user was asleep, no automated capture was scheduled. The Apple Reminders that fired at those local times did not auto-trigger captures; they pinged the user. For Phase 1 Step 2's post-merge watch, consider an autopilot trigger or PM2 cron skill if missed samples become a pattern.

**Phase 1 Step 2 work:** unblocked.

---

## Phase 1 Step 2 sign-off

> **Phase 1 Step 2 — production-stable as of 2026-05-01T09:55:00+02:00 (T+0 post-merge).** Merge commit: `15c6f70`.

**Samples captured:** T+0 (status=ok). T+4h through T+20h were skipped after operator decision: Step 1's 24h watch had already proven the audit envelope was stable, Step 2's hook layer with zero plugins is a passthrough wrapper that adds <1 ms overhead (validated by `tests/test_hook_audit_perf.py`), and the production state at merge time had `~/.codec/plugins/` empty. No new audit-error spike, no service degradation observed in the 4 hours of casual monitoring before sign-off was decided.

**Sign-off rationale:** zero plugins installed = zero hook side effects. The wrapper itself was extensively tested pre-merge (16 hook lifecycle tests, all passing). Deferred-watch decision documented here.

**Phase 1 Step 3 work:** unblocked.

---

## Phase 1 Step 3 sign-off (retroactive)

> **Phase 1 Step 3 — production-stable as of 2026-05-01T15:47:00+02:00 (T+0 post-merge).** Merge commit: `59bfbda`.

**Samples captured:** T+0 (status=ok, 0 Step 3 audit events emitted in window — all features dormant until invoked, which matches design). T+4h through T+20h **explicitly skipped per user instruction**.

**Why the watch was skipped:** Step 1 + Step 2 24h watches both came back clean with no production incidents. The pattern was established. More importantly, the previous attempts to run a structured 24h cadence via Apple Reminders + repeated pytest invocations caused the **2026-05-01 incident** (5 Apple Reminders fired by Claude Code via stdio MCP; cascade of Terminal popups from `memory_search`/`clipboard` skills triggered by repeated test_mcp_all_tools.py runs; 11 leaked AskUserQuestion notifications from un-monkeypatched test fixtures). Documented in `docs/INCIDENT-2026-05-01-spurious-skill-fires.md`. Test pollution made the cadence noisy and value-low.

**Sign-off rationale:**
- Step 3 introduces 89 new passing tests with 0 new failures (711 passed / 20 failed / 73 skipped — exactly the Step 1 + Step 2 baseline).
- All 6 new audit event types (`ask_user_question_emit`/`_answer`/`_timeout`, `stuck_warning`/`_escalated`, `step_budget_exhausted`) are dormant until invoked — no impact on idle traffic.
- 3 per-feature kill switches (`ASKUSER_ENABLED`/`STUCK_DETECTION_ENABLED`/`STEP_BUDGET_ENABLED`) for instant disable without code change.
- Pre-merge audit (`docs/PHASE1-STEP3-PREMERGE-AUDIT.md`) classified 0 of 20 baseline failures as Step-3-caused.

**Phase 1 Step 4 work:** unblocked.

---

## Phase 1 Step 4 sign-off

> **Phase 1 Step 4 — production-stable as of 2026-05-01T16:13:59+02:00 (T+0 post-merge + plugin install + PM2 restart).** Merge commit: `9858934`.

**Samples captured:** T+0 (status=ok). End-to-end verified by triggering a synthetic `weather` skill call after PM2 restart — `audit.log` immediately recorded `hook_fired` event with `extra.plugin_name="self_improve"` and `extra.hook_name="post_tool"`. Plugin is **live** and observable.

**Why no extended watch:** plugin is observe-only (post_tool/on_error) plus an on_operation_end snapshot that spawns at most one daemon thread per operation. The drafter thread calls the same `_draft_skill`/`_validate`/`_write_proposal` flow that was previously invoked nightly via `codec_self_improve.run_once()` — same code path, same proposal output dir, same dangerous-pattern gate. Behavior delta from "nightly polling" to "event-driven" is bounded and tested (21/21 `tests/test_self_improve_plugin.py`).

**Sign-off rationale:**
- Plugin file copied to `~/.codec/plugins/self_improve.py` and AST-discovered at PM2 restart (1 plugin discovered, name=`self_improve`, hooks=`['post_tool', 'on_error', 'on_operation_end']`).
- First real-traffic `hook_fired` audit event captured at 2026-05-01T14:13:59Z (= 16:13 CEST) confirming the post_tool hook fires on real skill execution.
- `codec_self_improve.run_once()` legacy path unchanged — both triggers coexist.
- Per-feature kill switch available (`SELF_IMPROVE_PLUGIN_ENABLED=false`).
- Per-tool throttle (30 min) prevents Qwen spam if same gap fires repeatedly.
- Self-recursion guard (`_SELF_TOOLS = {"self_improve", ""}`) prevents the plugin from firing on its own emits.

**Phase 1 status: COMPLETE.** All 4 steps merged + production-stable. See `docs/PHASE1-COMPLETE.md` for the consolidated state report.

---

## Phase 2 Step 5 sign-off

> **Phase 2 Step 5 — production-stable as of 2026-05-02T16:42:00+02:00 (T+0 post-merge + observer install + PM2 restart).** Merge commit: `824a52f`. Hotfix landed during sign-off window: PR #10 (`26e6add`) added `observer.ocr_enabled` config flag.

**Samples captured:** T+0 (status=ok). End-to-end verified by tailing `~/.codec/audit.log` after `pm2 restart codec-observer` — `observation_tick` (or `observation_tick_slow` when OCR disabled) emits every 5 s as designed. As of Phase 2 close: 65 `observation_tick` + 97 `observation_tick_slow` emits captured.

**Hotfix context:** initial Step 5 deploy with `ocr_enabled=true` (default) triggered a macOS Screen Recording permission popup-storm — over 100 popups while the system retried screencapture inside a `ThreadPoolExecutor` whose `with` block was blocking on `shutdown(wait=True)`. Root cause: the "100 ms" timeout inside the executor was actually waiting ~5 s for the popup-blocked screencapture to complete; the retry then triggered a SECOND popup. Hotfix PR #10 added `observer.ocr_enabled` (default `true`) with a graceful-degraded path that skips screencapture entirely when `false`. User runtime config patched to `observer.ocr_enabled: false` until Screen Recording is explicitly granted to both `python3.13` and `node` (PM2 parent).

**Sign-off rationale:**
- Observer daemon online and stable for 3+ minutes after restart with the hotfix applied.
- All 5 Step 5 audit event constants live in code; 2 of 5 (`observation_tick`, `observation_tick_slow`) directly observable in production audit log.
- `observation_summary_injected` is dormant until a chat / voice handler invokes the injection contract — which fires only when `transport=local` (always) or when cloud transport hits possessive-pronoun / continuation-phrase / `SKILL_NEEDS_OBSERVATION` flag.
- Per-feature kill switch via PM2 `pm2 stop codec-observer` + `observer.enabled: false`.
- Graceful-degraded code path proves `ocr_enabled=false` mode works as designed (97 `observation_tick_slow` emits without a single screencapture attempt or popup).
- Pre-merge tests: 33 passing (`tests/test_observer.py`).

**Phase 2 Step 6 work:** unblocked.

---

## Phase 2 Step 6 sign-off

> **Phase 2 Step 6 — production-stable as of 2026-05-02T18:25:00+02:00 (T+0 post-merge + PM2 restart of codec-observer + codec-dashboard).** Merge commit: `2d2ff3f`.

**Samples captured:** T+0 (status=ok, 0 trigger emits in window — dormant by design because no installed skill currently declares `SKILL_OBSERVATION_TRIGGER`). T+4h through T+20h skipped per the same rationale used in Phase 1 Step 3 — repeated cadence sampling adds noise without value when the feature is dormant on idle traffic.

**Sign-off rationale:**
- 35 new passing tests (`tests/test_triggers.py`, all mocking `codec_dispatch.run_skill`); 0 new failures.
- 3 audit event constants (`trigger_fired`, `trigger_skipped`, `trigger_killed`) live in `codec_audit.PHASE2_STEP6_EVENTS` frozenset; emit-on-fire wired through `codec_observer._eval_triggers`.
- `routes/triggers.py` mounted: `GET /api/triggers` returns 401 for unauthenticated requests (correct gate behavior); authenticated PWA requests return the AST-discovered trigger list.
- Per-trigger kill via `POST /api/triggers/{key}/kill` writes atomic tmp+rename to `~/.codec/triggers_killed.json`; full-system kill via `TRIGGERS_ENABLED=false` env var.
- Stable `sha8` key per `(skill_name, trigger_type, params_hash)` ensures kill state survives skill rename.
- `_eval_triggers(snapshot)` runs in `try/except` inside the observation loop; a broken trigger module never breaks the daemon.

**Why no T+24h watch:** trigger events stay dormant until a skill declares a `SKILL_OBSERVATION_TRIGGER` constant. Until then, `_eval_triggers` runs every tick and returns the empty list — measured zero overhead. Sampling for events that can't fire is process noise.

**Phase 2 Step 7 work:** unblocked.

---

## Phase 2 Step 7 sign-off

> **Phase 2 Step 7 — production-stable as of 2026-05-02T20:49:40+02:00 (T+0 post-merge + skill install + PM2 restart of codec-observer).** Merge commit: `0e40687`.

**Samples captured:** T+0 (status=ok). End-to-end verified by direct invocation `python3 -c "import shift_report; shift_report.run('shift report')"` immediately after deployment. Result captured in `~/.codec/audit.log`:

```
2026-05-02T18:49:40.547+00:00  shift_report_started    cid=5f188e5485e5  trigger_kind=manual
2026-05-02T18:49:40.555+00:00  shift_report_completed  cid=5f188e5485e5  sections_included=2  word_count=69  audit_records_scanned=305  duration_ms=8.28
```

Notification posted to `~/.codec/notifications.json` with `type="shift_report"`, `title="CODEC Shift Report — 2026-05-02"`, full markdown body.

**Sign-off rationale:**
- 20 new passing tests (`tests/test_shift_report.py`, all filesystem-mocked to `tmp_path`); 0 new failures.
- Both audit event constants (`shift_report_started`, `shift_report_completed`) emit paired with shared `correlation_id` per Step 1 §1.4 contract.
- Manual trigger path bypasses per-day dedup so user can always re-run on demand; `time` and `idle` trigger paths honor `~/.codec/shift_report_state.json` to enforce one-report-per-local-day.
- Skill installed at `~/.codec/skills/shift_report.py` (22151 bytes); discovered by `codec_skill_registry`; exposed via MCP per `SKILL_MCP_EXPOSE = True`.
- `_maybe_fire_shift_report(idle_seconds)` integrated inside `codec_observer.run_daemon` loop; called every observation tick after `_eval_triggers`.
- Per-feature kill switches: `SHIFT_REPORT_ENABLED=false` env var, `shift_report.enabled: false` config, OR remove `~/.codec/skills/shift_report.py` (skill not discovered → not callable).

**Why no T+24h watch:** `time` and `idle` trigger paths fire at most once per local-date. The first scheduled `time` trigger after merge would be tomorrow at the configured `daily_at_hour:daily_at_minute`. Manual path verified live at T+0 with full audit + notification + state-file proof. The dedup mechanism is unit-tested in `tests/test_shift_report.py::test_per_day_dedup_blocks_second_idle_fire`.

**Phase 2 status: COMPLETE.** All 3 steps merged + production-stable. See `docs/PHASE2-COMPLETE.md` for the consolidated state report.

---

*Last updated: 2026-05-02 (Step 7 sign-off; Phase 2 complete).*

## 2026-06-09 — fact_extract silently no-ops on fact storage
`skills/fact_extract.py:93-95` calls `mem.store_fact(...)` inside a swallowed
try/except AttributeError — but `CodecMemory` has no `store_fact` method (it lives in
`codec_memory_upgrade`). Extracted facts are therefore never written to the facts table.
Found during the Daybreak audit (2026-06-09); out of Daybreak scope. Fix: route to
`codec_memory_upgrade.store_fact` and add a round-trip test.

## Pilot e2e skill files fail skill-contract tests (2026-07-02)

`skills/pilot_full_test_e2e.py` and `skills/pilot_e2e_test_fetch_example_title.py`
lack the required `SKILL_NAME` / `SKILL_TRIGGERS` module attrs, so 4
`tests/test_skills.py` contract tests fail on every run (pre-existing on main,
confirmed 2026-07-02 during the log-review PRs). They look like leftover Pilot
e2e scratch files, not real skills. Fix: either add the required metadata +
regenerate `skills/.manifest.json`, or delete both files.
