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

*Last updated: 2026-05-01 (Step 4 sign-off; Phase 1 complete).*
