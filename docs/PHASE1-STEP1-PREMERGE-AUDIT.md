# Phase 1 Step 1 — pre-merge test audit

**Branch:** `phase1-step1-audit-unification`
**Tip commit:** `027beaf`
**Compared against:** `main` HEAD `805ead1`
**Captured:** 2026-04-30

---

## 0 · Methodology

1. Cloned `main` to `/tmp/main-baseline`, ran `pytest tests/ --ignore=tests/test_smoke.py -q --tb=line` on both `main` and the branch.
2. Diffed the failure lists — they are **identical** (20 failures on each side; same test IDs, same one-line errors except for one line-number shift caused by code I added above an existing bug).
3. For each failure, verified the test fixture against the diff `git diff main...HEAD -- <file>` to confirm whether the failing assertion targets code that this PR modified.
4. For tests where I touched the surrounding file: confirmed by reading the diff that the specific lines/symbols the test checks were **not** modified.

`tests/test_smoke.py` itself is excluded from this audit — it has a pre-existing collection-time `AttributeError: 'NoneType' object has no attribute 'group'` that prevents pytest from collecting it on `main` and on this branch, identically. Documented in the design doc baseline; not a regression.

---

## 1 · 20 pre-existing failures, classified

Categories:
- **untouched** — test inspects code this PR did not modify.
- **touched-unchanged** — test inspects a file this PR modified, but the failure is unrelated to the changes (proven by diff: the lines/symbols the test checks were not edited).
- **touched-needs-investigation** — test inspects a file this PR modified, and the failure could plausibly be related.

### 1.1 Failure inventory

| # | Test ID | Failure (one-line) | Class | Why |
|---|---|---|---|---|
| 1 | `test_critical_fixes.py::TestPinBruteForce::test_pin_attempts_dict_exists` | `assert hasattr(codec_dashboard, "_pin_attempts")` is `False` | touched-unchanged | `_pin_attempts` lives in `routes/_shared.py`, imported into `routes/auth.py`. Test inspects the wrong module. My diff to `codec_dashboard.py` (`git diff main...HEAD -- codec_dashboard.py`) contains zero references to `_pin_attempts`. |
| 2 | `test_critical_fixes.py::TestPinBruteForce::test_pin_lockout_logic` | `AttributeError: module 'codec_dashboard' has no attribute '_pin_attempts'` | touched-unchanged | Same root cause as #1 — same symbol. |
| 3 | `test_critical_fixes.py::TestPinBruteForce::test_pin_success_resets_counter` | `AttributeError: module 'codec_dashboard' has no attribute '_pin_attempts'` | touched-unchanged | Same root cause as #1. |
| 4 | `test_critical_fixes.py::TestSkillForgeBlocklist::test_blocklist_has_minimum_patterns` | `inspect.getsource(codec_dashboard.save_skill)` → `AttributeError: module 'codec_dashboard' has no attribute 'save_skill'. Did you mean: 'save_file'?` | touched-unchanged | `save_skill` doesn't exist in `codec_dashboard.py` on `main` (verified: `grep -n "def save_skill" codec_dashboard.py` empty on both sides). My diff doesn't add or remove any function definitions in this file. |
| 5 | `test_critical_fixes.py::TestAuthSessionThreadSafety::test_auth_lock_is_used_in_source` | `Expected at least 4 lock acquisitions, found 2` (counts `with _auth_lock` in `codec_dashboard.py` source only) | touched-unchanged | Lock usage is now mostly in `routes/auth.py` (5 occurrences) and `routes/_shared.py` (1). The test only inspects `codec_dashboard.py`, which has 2 — same on `main` and on this branch. My diff to `codec_dashboard.py` contains zero `_auth_lock` references. |
| 6 | `test_full_product_audit.py::TestSkillFiles::test_all_skills_have_run` | `Skills missing run(): ['codec.py']` (refers to `skills/codec.py`) | untouched | This PR did not modify `skills/codec.py`. (My only `codec.py` edits were to the **root** `codec.py`.) |
| 7 | `test_full_product_audit.py::TestMainCodec::test_codec_imports` | `assert hasattr(codec, 'audit')` is `False` (where `codec` = `skills/codec.py`) | untouched | Same — `skills/codec.py` is not in this PR. |
| 8 | `test_full_product_audit.py::TestMainCodec::test_dry_run_enforcement` | `assert "DRY_RUN" in code` (where `code` = contents of `skills/codec.py`) | untouched | Same — `skills/codec.py` not modified. |
| 9 | `test_full_product_audit.py::TestMainCodec::test_sqlite_context_managers` | `Only 0 context managers found` (in `skills/codec.py`) | untouched | Same — `skills/codec.py` not modified. |
| 10 | `test_high_fixes.py::TestNoBarExcept::test_exceptions_are_logged[codec_voice.py]` | `codec_voice.py:77 has except Exception followed by bare pass` | touched-unchanged | The bare `except Exception: pass` block is at lines 76–77 on this branch, was at lines 66–67 on `main`. Shift of 10 lines is exactly the length of the contextvar import block I added at the top of the file (verified: same source bytes around the `except`). The pre-existing bare except is **not** code this PR introduced. |
| 11 | `test_high_fixes.py::TestTriggerWordBoundary::test_registry_uses_word_boundary` | `match_trigger must use word boundary regex` (inspects `codec_skill_registry.py`) | untouched | This PR did not modify `codec_skill_registry.py`. |
| 12 | `test_medium_fixes.py::TestMCPDocumentation::test_mcp_default_allow_documented` | `README missing mcp_default_allow docs` | untouched | This PR did not modify `README.md`. |
| 13 | `test_medium_fixes.py::TestMCPDocumentation::test_mcp_allowed_tools_documented` | `README missing mcp_allowed_tools docs` | untouched | Same. |
| 14 | `test_medium_fixes.py::TestMCPDocumentation::test_mcp_config_table` | `README missing MCP config table` | untouched | Same. |
| 15 | `test_memory.py::test_session_cleanup_saves_conversations` | `assert 0 == 4` (cleanup wrote no rows) | touched-unchanged | The test exercises `Session.cleanup()` in `codec_session.py`. My diff to `codec_session.py` (`git diff main...HEAD -- codec_session.py`) only touches: (1) the `try/except ImportError` import block at lines 22–25, (2) three `log_event` call sites at lines 672/680/686 inside `_run_shell`. `cleanup()` itself (line ~970+) is not modified. The 0-rows result is a pre-existing bug in the cleanup logic. |
| 16 | `test_memory.py::test_session_cleanup_truncates_long_content` | `TypeError: 'NoneType' object is not subscriptable` (no row returned) | touched-unchanged | Same root cause as #15 — cleanup writes nothing, so `fetchone()` returns `None`. |
| 17 | `test_scheduler.py::test_add_and_remove_schedule` | `assert s["enabled"] is True` (got `False`) | touched-unchanged | `add_schedule()` in `codec_scheduler.py:62` defaults `"enabled": False`. The test expects `True`. My diff to `codec_scheduler.py` (`git diff main...HEAD -- codec_scheduler.py`) only touches the import block and the two `log_event` calls inside `check_and_run`. `add_schedule` is not modified. Implementation/test mismatch is pre-existing. |
| 18 | `test_security.py::test_osascript_inputs_sanitized` | `osascript embeds unsanitized variable '_safe_task' — must use safe_ prefix` | touched-unchanged | Inspects root `codec.py`. The variable `_safe_task` is at line 255 (now 256 after my import-block diff) — declared in `_dispatch_inner` and used in `subprocess.Popen([..., 'display notification "Heard: {_safe_task}"', ...])`. My diff does not touch this `subprocess.Popen` line or rename the variable. The test bug (regex `\w+` matches `_safe_task`; assertion wants `safe_` prefix without leading underscore) is pre-existing. |
| 19 | `test_security.py::test_session_script_imports_dangerous_patterns` | `codec_agent.py must import DANGEROUS_PATTERNS from codec_config` | untouched | File is `codec_agent.py` (singular — different from `codec_agents.py`). This PR did not modify `codec_agent.py`. |
| 20 | `test_security.py::test_save_skill_validates_content` | `save_skill must validate SKILL_DESCRIPTION presence` (got empty source — function doesn't exist) | touched-unchanged | Same root cause as #4 — `save_skill` doesn't exist in `codec_dashboard.py` on either side; `inspect.getsource` returns `""` which contains no substring. |

### 1.2 Counts by classification

| class | count | which |
|---|---|---|
| untouched | 9 | #6, #7, #8, #9, #11, #12, #13, #14, #19 |
| touched-unchanged | 11 | #1, #2, #3, #4, #5, #10, #15, #16, #17, #18, #20 |
| touched-needs-investigation | 0 | — |
| **total** | **20** | |

### 1.3 Why none reach `touched-needs-investigation`

For every failure that lands in a file I modified, the audit chain is:
1. Read `git diff main...HEAD -- <file>` end-to-end.
2. Confirm the diff does not touch the symbol/line/regex the test inspects.
3. Confirm the same failure with the same root-cause message reproduces on `main` (already verified at the file-list level: `diff /tmp/main_fails.txt /tmp/branch_fails.txt` is empty).

For #10 specifically — the only failure where the line number changed — I verified the same `except Exception:` / `pass` block source bytes are present at the new line. The only change is the line offset (caused by my contextvar import 10 lines higher in the file), which is irrelevant to the test (it asserts on the pattern, not the line number).

---

## 2 · 73 skipped tests

### 2.1 Reason categories

| reason | count |
|---|---|
| `Dashboard not running at localhost:8090` | 58 |
| `Set CODEC_TEST_TOKEN env var to run authenticated endpoint tests` | 5 |
| `Set CODEC_TEST_TOKEN env var to run malformed input tests` | 2 |
| `Auth enabled without dashboard_token` | 3 |
| (module-level `pytest.skip` for `numpy/httpx`/`pynput` import errors and similar) | balance to 73 |

### 2.2 All skips are environmental, not silenced in this PR

`grep -rn "pytest.mark.skip\|pytest.skip\|@skip\|@unittest.skip" tests/` shows **only pre-existing, environment-conditional** skip markers:

```
tests/test_session_execution.py  pytest.skip(f"codec_core import failed (likely pynput/native dep): {e}")
tests/test_session_execution.py  pytest.skip(f"codec_session import failed: {e}")
tests/test_session_execution.py  pytest.skip(f"codec_agent import failed: {e}")
tests/test_dashboard.py          pytest.skip("Auth enabled without dashboard_token")
tests/test_dashboard.py          pytestmark = pytest.mark.skipif(... no dashboard_token ...)
tests/test_full_product_audit.py requires_dashboard = pytest.mark.skipif(... port 8090 ...)
tests/test_high_fixes.py         pytest.skip(f"{filepath} not found")
tests/test_dashboard_api.py      pytest.skip("No dashboard token configured")
tests/test_dashboard_api.py      @pytest.mark.skipif(no test_token)
tests/test_voice_pipeline.py     pytest.skip("numpy or httpx not available", allow_module_level=True)
```

Verification that this PR did not add any new skip markers:

```
$ git diff main...HEAD -- tests/ | grep -E '^\+.*(pytest.skip|@skip|skipif)'
(empty)
```

All 73 skips are due to:
- The dashboard PM2 service not being running at `:8090` on this development machine (production runs it under `pm2 start codec-dashboard`).
- The `CODEC_TEST_TOKEN` environment variable not being set (an opt-in for hitting authenticated endpoints — the user did not opt in for this audit run).
- Optional native deps (`pynput`, `numpy`/`httpx`) only present in the production virtualenv.
- A config-file gate (`auth_pin_hash` / `dashboard_token`) intentionally absent in the worktree's `~/.codec/config.json`.

None were silenced by this PR.

---

## 3 · Sign-off

- Pre-existing failure inventory matches `main` exactly (20-for-20).
- 11 of the 20 failures touch files this PR modified, but the diff does not touch the symbol/line/pattern the test inspects in any of them.
- 0 failures need investigation.
- 73 skips are all environmental; this PR adds no new skip markers.

The branch is clean for merge by the contract: no new failures, no silenced tests, no test-modification bypassing.

The reviewer can re-run any individual test via:

```
cd /Users/mickaelfarina/codec-repo/.claude/worktrees/phase1-step1
pytest tests/<file>::<TestClass>::<test_name> --tb=long -v
```

…and compare to `cd /tmp/main-baseline && pytest tests/<same>` to verify identical pre-existing behaviour.
