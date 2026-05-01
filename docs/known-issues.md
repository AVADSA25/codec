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

## Phase 1 Step 2 sign-off (TBD)

Reserved for the §10 sign-off line once Step 2's six samples (T+0/+4h/+8h/+12h/+16h/+20h) land within 1.3× baseline. Same shape as Step 1 above. Updated when T+20h sample is captured to PHASE1-STEP2-POSTMERGE-SAMPLES.md. Until that line is added, Phase 1 Step 4 (codec_self_improve plugin migration) does **not** start.

---

*Last updated: 2026-05-01 (Step 1 sign-off after retroactive 24h watch).*
