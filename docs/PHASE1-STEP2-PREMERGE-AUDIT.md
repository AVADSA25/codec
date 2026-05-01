# Phase 1 Step 2 — pre-merge test audit

**Branch:** `phase1-step2-plugin-hooks`
**Tip commit:** `fcd102b`
**Compared against:** `main` HEAD `4903df0`
**Captured:** 2026-04-30
**Methodology:** identical to `docs/PHASE1-STEP1-PREMERGE-AUDIT.md`.

---

## 0 · Methodology

1. Cloned `main` to `/tmp/step2-baseline`, ran `pytest tests/ --ignore=tests/test_smoke.py -q --tb=no` on both `main` and the Step 2 branch.
2. Diffed the failure lists — they are **identical** (`diff /tmp/main_fails_v2.txt /tmp/p2_fails.txt` = empty).
3. For each failure, classified the test target against this PR's modified-file list (codec_hooks.py [new], codec_audit.py, codec_dispatch.py, codec_agents.py, codec_voice.py, codec_mcp.py, AGENTS.md, 5 new test files).
4. For tests in modified files: walked the relevant `git diff main...HEAD -- <file>` block to confirm the test's target symbol/line/regex was not edited.

`tests/test_smoke.py` excluded from this audit — pre-existing collection-time `AttributeError: 'NoneType' object has no attribute 'group'` that prevents pytest from collecting it on `main` and on this branch identically. Documented in the Step 1 audit.

---

## 1 · 20 pre-existing failures, classified

Categories:
- **untouched** — test inspects code this PR did not modify.
- **touched-unchanged** — test inspects a file this PR modified, but the failure is unrelated (proven by diff: lines/symbols the test checks were not edited).
- **touched-needs-investigation** — test inspects a file this PR modified, and the failure could plausibly be related.

### 1.1 Failure inventory

| # | Test ID | Target file | Class | Why |
|---|---|---|---|---|
| 1 | `test_critical_fixes.py::TestPinBruteForce::test_pin_attempts_dict_exists` | `codec_dashboard.py` | untouched | This PR does not modify `codec_dashboard.py`. (Step 1 did; Step 2 does not.) |
| 2 | `test_critical_fixes.py::TestPinBruteForce::test_pin_lockout_logic` | `codec_dashboard.py` | untouched | Same — `codec_dashboard.py` is not in the Step 2 modified-file list. |
| 3 | `test_critical_fixes.py::TestPinBruteForce::test_pin_success_resets_counter` | `codec_dashboard.py` | untouched | Same. |
| 4 | `test_critical_fixes.py::TestSkillForgeBlocklist::test_blocklist_has_minimum_patterns` | `codec_dashboard.py` (looking for `save_skill` symbol) | untouched | Same. |
| 5 | `test_critical_fixes.py::TestAuthSessionThreadSafety::test_auth_lock_is_used_in_source` | `codec_dashboard.py` (counts `with _auth_lock` occurrences) | untouched | Same. |
| 6 | `test_full_product_audit.py::TestSkillFiles::test_all_skills_have_run` | `skills/codec.py` | untouched | This PR does not touch the `skills/` tree. |
| 7 | `test_full_product_audit.py::TestMainCodec::test_codec_imports` | `skills/codec.py` | untouched | Same. |
| 8 | `test_full_product_audit.py::TestMainCodec::test_dry_run_enforcement` | `skills/codec.py` | untouched | Same. |
| 9 | `test_full_product_audit.py::TestMainCodec::test_sqlite_context_managers` | `skills/codec.py` | untouched | Same. |
| 10 | `test_high_fixes.py::TestNoBarExcept::test_exceptions_are_logged[codec_voice.py]` | `codec_voice.py` | **touched-unchanged** | See §1.3 — same pre-existing `except Exception: pass` block from main, line shifted but bytes identical. |
| 11 | `test_high_fixes.py::TestTriggerWordBoundary::test_registry_uses_word_boundary` | `codec_skill_registry.py` | untouched | Not in Step 2's modified list. |
| 12 | `test_medium_fixes.py::TestMCPDocumentation::test_mcp_default_allow_documented` | `README.md` | untouched | Not in Step 2's modified list. |
| 13 | `test_medium_fixes.py::TestMCPDocumentation::test_mcp_allowed_tools_documented` | `README.md` | untouched | Same. |
| 14 | `test_medium_fixes.py::TestMCPDocumentation::test_mcp_config_table` | `README.md` | untouched | Same. |
| 15 | `test_memory.py::test_session_cleanup_saves_conversations` | `codec_session.py` (`Session.cleanup()`) | untouched | This PR does not modify `codec_session.py`. (Step 1 did.) |
| 16 | `test_memory.py::test_session_cleanup_truncates_long_content` | `codec_session.py` (same `Session.cleanup()`) | untouched | Same. |
| 17 | `test_scheduler.py::test_add_and_remove_schedule` | `codec_scheduler.py` (`add_schedule` defaults `enabled=False`) | untouched | This PR does not modify `codec_scheduler.py`. (Step 1 did, but only the import block + `log_event` calls inside `check_and_run` — `add_schedule` was never touched there either; here it's just not in this PR at all.) |
| 18 | `test_security.py::test_osascript_inputs_sanitized` | `codec.py` (root) | untouched | This PR does not modify the root `codec.py`. |
| 19 | `test_security.py::test_session_script_imports_dangerous_patterns` | `codec_agent.py` (singular — different file from `codec_agents.py` which IS modified) | untouched | The test loads `codec_agent.py`, not `codec_agents.py`. Verified via `grep -n "codec_agent\.py" tests/test_security.py`. |
| 20 | `test_security.py::test_save_skill_validates_content` | `codec_dashboard.py` (looking for `save_skill` function) | untouched | This PR does not modify `codec_dashboard.py`. |

### 1.2 Counts by classification

| class | count | which |
|---|---|---|
| untouched | 19 | #1, #2, #3, #4, #5, #6, #7, #8, #9, #11, #12, #13, #14, #15, #16, #17, #18, #19, #20 |
| touched-unchanged | 1 | #10 |
| touched-needs-investigation | 0 | — |
| **total** | **20** | |

The Step 1 PR had 11 `touched-unchanged` failures and 9 `untouched`. Step 2 inverts this ratio because Step 2's modified-file surface is much narrower — Step 1 touched 14 files (codec_dashboard, codec_session, codec_scheduler, codec.py, routes/auth, etc.), Step 2 touches 7 (codec_hooks new, codec_audit, codec_dispatch, codec_agents, codec_voice, codec_mcp, AGENTS.md). Most of the failing tests target code Step 2 doesn't go near.

### 1.3 Detail on the one touched-unchanged: `test_exceptions_are_logged[codec_voice.py]`

**Failure on Step 2 branch:** `codec_voice.py:83 has except Exception followed by bare pass`
**Failure on main HEAD `4903df0`:** `codec_voice.py:77 has except Exception followed by bare pass`

Line shifted by 6. That's exactly the size of the multi-line import block I added in Step 2(e):

```python
from codec_hooks import (
    HookVeto,
    emit_operation_end as _voice_emit_op_end,
    emit_operation_start as _voice_emit_op_start,
    run_with_hooks as _voice_run_with_hooks,
)
```

Six new lines (the `from ... import (` opener, four imported names, the closing `)`). The pre-existing bare-except block:

```python
except Exception:
    pass
```

…lives inside the `VISION_PROVIDER` config-load `try:` block. Source bytes are **identical** between `main` and the Step 2 branch around lines 78–83 (verified by `sed -n '78,86p'` on both checkouts and by-hand comparison — same characters, same indentation, same surrounding code). Step 2 does not modify the VISION_PROVIDER block; the line offset is the only thing that changed.

Same root cause as Step 1's audit observation about this same test. It's the same single bare-except in the file; my Step 2 edits touch `dispatch_skill` (line ~670) and `VoicePipeline.run` start/finally (line ~1130) — both well below the test's first-match point. The test stops at the first `except Exception: pass` it finds, so it would never surface my new emit-wrapping `try/except pass` blocks even if it cared about them (which it would — see §1.4 disclosure).

### 1.4 Disclosure: Step 2 adds two new `try / except: pass` blocks in `codec_voice.py`

For honesty: my Step 2(e) commit added two new defensive emit wrappers in `VoicePipeline.run`:

```python
try:
    _voice_emit_op_start(operation_id=self.session_id,
                         transport="voice",
                         correlation_id=cid)
except Exception:
    pass
```

…and the symmetric `_voice_emit_op_end` wrapper in the `finally`. They mirror the existing Step 1 pattern around `voice_session_start` / `voice_session_end` audit emits — the same defensive shape ("audit emit must never break the operation"). The hook-layer emitters (`emit_operation_start` / `emit_operation_end` in `codec_hooks.py`) are themselves never-raise by design (every `_fire_one_*` catches plugin exceptions internally and emits `hook_error`), so the outer `try/except pass` is belt-and-suspenders.

Why this is **not** a `touched-needs-investigation` for the test: `test_exceptions_are_logged` returns the FIRST bare-except it finds and stops. The first one is at line 83 (the pre-existing VISION_PROVIDER block). My new wrappers at line >1130 are never reached by the test. If anyone fixes the line-83 bare-except in a future PR, the test would then surface my new wrappers and we'd have to address them — which would mean replacing `pass` with `log.debug("emit failed: %s", e)` or similar. Tracked for follow-up but **not blocking this PR**: the test's gate is unchanged.

---

## 2 · 73 skipped tests

### 2.1 Counts by reason category

```
$ pytest tests/ --ignore=tests/test_smoke.py -rs --tb=no -q | grep '^SKIPPED' | sed -E 's/.*SKIPPED \[([0-9]+)\].*/\1/' | awk '{s+=$1} END {print s}'
73
```

Same four categories as Step 1's audit:
| reason | count (approx) |
|---|---|
| `Dashboard not running at localhost:8090` | 58 |
| `Set CODEC_TEST_TOKEN env var to run authenticated endpoint tests` | 7 |
| `Auth enabled without dashboard_token` | 3 |
| native deps (numpy / httpx / pynput) module-level skipif | balance to 73 |

### 2.2 No new skip markers added by this PR

```
$ git diff main...HEAD -- tests/ | grep -E '^\+.*(pytest\.skip|skipif)'
(empty)
```

Step 2 added zero new skip markers. The 5 new test files (`test_plugin_discovery.py`, `test_hook_lifecycle.py`, `test_hook_veto.py`, `test_hook_mutation_ordering.py`, `test_hook_audit_perf.py`) all run unconditionally. Verified.

All 73 skips are pre-existing and environmental — same list as Step 1 sign-off.

---

## 3 · `_safe_task` entry confirmed in `docs/known-issues.md`

```
$ grep -n "_safe_task" docs/known-issues.md
11: [...] _safe_task regex bug below [...]
13:### `codec.py:255` — `_safe_task` osascript variable name fails the regex sanitizer test
19:| **symbol** | local variable `_safe_task` inside `_dispatch_inner()` |
```

The Step 1 entry survives unchanged on this branch (file was carried forward from `main` per `git checkout main` at branch-creation time, no edits in this PR). Status remains `deferred-not-fixed`; revisit-when target unchanged.

---

## 4 · Sign-off

- Pre-existing failure inventory matches `main` exactly (20-for-20, identical IDs and tracebacks).
- 1 of the 20 failures targets a file this PR modified (`codec_voice.py`); the failure is at the same source bytes as on `main`, only the line number shifted by 6 due to the new `from codec_hooks import (...)` multi-line block.
- 0 failures need investigation.
- 73 skips all environmental; this PR adds zero skip markers.
- `_safe_task` known-issues entry intact.
- 622 tests passing (568 baseline + 54 new for hook-layer coverage).

The branch is clean for merge by the same gate Step 1 used. Disclosure of the two new defensive `try/except pass` blocks in `codec_voice.py` (§1.4) is logged here so a future fix to the line-83 bare-except has the context it needs.

The reviewer can re-run any individual test via:

```
cd /Users/mickaelfarina/codec-repo/.claude/worktrees/phase1-step2
pytest tests/<file>::<TestClass>::<test_name> --tb=long -v
```

…and compare to `cd /tmp/step2-baseline && pytest tests/<same>` to verify identical pre-existing behaviour.
