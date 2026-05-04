# Phase 1 Step 3 — pre-merge audit (20 failures classified)

**Captured:** 2026-05-01 15:30 CEST (after rebase onto main `fcbef2f` which includes hotfix PR #6).

**Suite result:** `20 failed / 711 passed / 73 skipped` — exactly the Step 2 baseline. Step 3 adds 89 new passing tests; introduces ZERO new failures.

## Audit gate (same as Step 1 + Step 2 audits)

For each of the 20 pre-existing failures, classify relative to Step 3's modified files:
- **(a) untouched** — Step 3 did not modify the source file(s) under test
- **(b) touched-unchanged** — Step 3 modified the file(s) under test but the failing assertion is on a code region/identifier NOT in the Step 3 diff
- **(c) touched-needs-investigation** — Step 3 may have caused or affected the failure

Any **(c)** must be isolated and either proven unrelated or fixed in PR #5 before merge.

## Step 3 modified files

```
codec_audit.py             — added Step 3 event constants
codec_ask_user.py          — NEW (665 lines)
routes/agents.py           — added /api/agents/answer + /api/agents/pending_questions
codec_agents.py            — added stuck-detection + ring buffer
codec_dashboard.py         — added _StepBudget class + chat_completion wiring (+149 lines)
codec_dashboard.html       — added inline AskUserQuestion answer panel
codec_voice.py             — added voice ask_user listen-mode + fuzzy-option-match (+~280 lines)
skills/stuck.py            — NEW (LLM-self-recognized stuck shim)
skills/ask_user.py         — NEW (LLM-facing AskUserQuestion shim)
AGENTS.md                  — §3 + §6 + §7 + §10 update
docs/PHASE1-STEP3-BASELINE.md — NEW
tests/conftest.py          — worktree-aware sys.path
tests/test_full_product_audit.py — REPO=parent.parent worktree-aware
tests/test_transcript.py   — same
tests/test_mcp_all_tools.py — added ask_user/stuck to SKIP_SKILLS
tests/test_ask_user.py            — NEW
tests/test_stuck_detection.py     — NEW
tests/test_step_budget.py         — NEW
tests/test_destructive_consent.py — NEW
tests/test_voice_ask_user.py      — NEW
```

## The 20 failures, classified

| # | Test | Error | Touches Step 3 file? | Verdict | Evidence |
|---|---|---|---|---|---|
| 1 | `test_critical_fixes.py::TestPinBruteForce::test_pin_attempts_dict_exists` | `AttributeError: module 'codec_dashboard' has no attribute '_pin_attempts'` | codec_dashboard.py | **(b) touched-unchanged** | `grep -c '_pin_attempts'` returns 0 in BOTH main `codec_dashboard.py` AND worktree's. Identifier was never present; pre-existing test against deleted/never-implemented feature. |
| 2 | `test_critical_fixes.py::TestPinBruteForce::test_pin_lockout_logic` | same `_pin_attempts` AttributeError | codec_dashboard.py | **(b) touched-unchanged** | Same — `_pin_attempts` doesn't exist in either branch. |
| 3 | `test_critical_fixes.py::TestPinBruteForce::test_pin_success_resets_counter` | same `_pin_attempts` AttributeError | codec_dashboard.py | **(b) touched-unchanged** | Same. |
| 4 | `test_critical_fixes.py::TestSkillForgeBlocklist::test_blocklist_has_minimum_patterns` | `AttributeError: module 'codec_dashboard' has no attribute 'save_skill'. Did you mean: 'save_file'?` | codec_dashboard.py | **(b) touched-unchanged** | `grep -n 'def save_skill'` returns 0 in BOTH branches. Function was renamed to `save_file` long before Step 3. |
| 5 | `test_critical_fixes.py::TestAuthSessionThreadSafety::test_auth_lock_is_used_in_source` | `AssertionError: Expected at least 4 lock acquisitions, found 2` | codec_dashboard.py | **(b) touched-unchanged** | `grep -c 'with _auth_lock'` returns 2 in BOTH main and worktree. Step 3 didn't add OR remove any `_auth_lock` usage. Pre-existing assertion mismatch. Verified via `git diff origin/main..HEAD -- codec_dashboard.py | grep auth_lock` returns empty. |
| 6 | `test_full_product_audit.py::TestSkillFiles::test_all_skills_have_run` | `AssertionError: Skills missing run(): ['codec.py']` | skills/ (NOT touched on this file — Step 3 added skills/ask_user.py + skills/stuck.py only, both have `run()`) | **(a) untouched** | Failing skill is `skills/codec.py`. Step 3 added `skills/ask_user.py` (has `def run`) and `skills/stuck.py` (has `def run`). Verified directly. |
| 7 | `test_full_product_audit.py::TestMainCodec::test_codec_imports` | `assert False` (import codec failed) | codec.py NOT touched | **(a) untouched** | Step 3 modified-files list does NOT include codec.py. |
| 8 | `test_full_product_audit.py::TestMainCodec::test_dry_run_enforcement` | (string-matching codec.py for "DRY_RUN") | codec.py NOT touched | **(a) untouched** | Same — codec.py unchanged by Step 3. |
| 9 | `test_full_product_audit.py::TestMainCodec::test_sqlite_context_managers` | `AssertionError: Only 0 context managers found` | codec.py NOT touched | **(a) untouched** | Same. |
| 10 | `test_high_fixes.py::TestNoBarExcept::test_exceptions_are_logged[codec_voice.py]` | `AssertionError: codec_voice.py:218 has except Exception followed by bare pass` | codec_voice.py touched (Step 3 added voice ask_user code) | **(b) touched-unchanged** | `git blame codec_voice.py` for line 218 shows commit `1d9b846c` from 2026-04-01 (Mickael) — predates Step 3 by a month. Step 3's diff to codec_voice.py only touches lines 14-15, 39-179, 667-802, 986-1019, 1122-1138, 1222-1226. Line 218 is NOT in any Step 3 hunk. |
| 11 | `test_high_fixes.py::TestTriggerWordBoundary::test_registry_uses_word_boundary` | `AssertionError: match_trigger must use word boundary regex` | codec_dispatch.py NOT touched | **(a) untouched** | Step 3 modified-files list does NOT include codec_dispatch.py. |
| 12 | `test_medium_fixes.py::TestMCPDocumentation::test_mcp_default_allow_documented` | `AssertionError: README missing mcp_default_allow docs` | README.md NOT touched | **(a) untouched** | Step 3 modified-files list does NOT include README.md. |
| 13 | `test_medium_fixes.py::TestMCPDocumentation::test_mcp_allowed_tools_documented` | `AssertionError: README missing mcp_allowed_tools docs` | README.md NOT touched | **(a) untouched** | Same. |
| 14 | `test_medium_fixes.py::TestMCPDocumentation::test_mcp_config_table` | `AssertionError: README missing MCP config table` | README.md NOT touched | **(a) untouched** | Same. |
| 15 | `test_memory.py::test_session_cleanup_saves_conversations` | `assert ...` (session cleanup state mismatch) | codec_session.py / codec_memory.py NOT touched | **(a) untouched** | Step 3 modified-files list does NOT include either. |
| 16 | `test_memory.py::test_session_cleanup_truncates_long_content` | `TypeError: 'NoneType' object is not subscriptable` | same | **(a) untouched** | Same. |
| 17 | `test_scheduler.py::test_add_and_remove_schedule` | `assert False` (scheduler state mismatch) | codec_scheduler.py NOT touched | **(a) untouched** | Step 3 modified-files list does NOT include codec_scheduler.py. |
| 18 | `test_security.py::test_osascript_inputs_sanitized` | `AssertionError: osascript embeds unsanitized variable '_safe_task' — must use safe_ prefix` | codec_core.py / build_session_script NOT touched | **(a) untouched** | Step 3 modified-files list does NOT include codec_core.py. (This is the same pre-existing finding from Step 2 audit — `_safe_task` rename was never done.) |
| 19 | `test_security.py::test_session_script_imports_dangerous_patterns` | `AssertionError: codec_agent.py must import DANGEROUS_PATTERNS from codec_config` | codec_agent.py (vs codec_agents.py — different file!) NOT touched | **(a) untouched** | The test is checking `codec_agent.py` (singular). Step 3 modified `codec_agents.py` (plural). Different file. |
| 20 | `test_security.py::test_save_skill_validates_content` | `AssertionError: save_skill must validate SKILL_DESCRIPTION presence` | codec_dashboard.py touched, but `save_skill` doesn't exist (see #4) | **(b) touched-unchanged** | Same as #4 — `save_skill` was renamed to `save_file` long before Step 3. Test asserts a function that no longer exists. |

## Distribution

| Verdict | Count | Detail |
|---|---|---|
| **(a) untouched** | 13 | Step 3 did not modify any file under test. Pure pre-existing failures, identical to Step 1 / Step 2 audit results. |
| **(b) touched-unchanged** | 7 | Step 3 modified the file under test, but the failing assertion is on code regions / identifiers NOT in Step 3's diff (verified by line-range diff + identifier grep). |
| **(c) touched-needs-investigation** | **0** | None. |

## Conclusion

**No (c) failures. All 20 are pre-existing baseline failures with no Step 3 causal involvement.**

This matches the Step 1 + Step 2 audit conclusions verbatim. The same 20 failures have been the baseline since at least 2026-04-29 — they are tests that assert against deleted/renamed functions, README docs that were never written, code paths that were refactored without the test being updated. Each is a candidate for a separate cleanup PR; none gates Step 3 merge.

## Cross-reference: identical failures in Step 2 audit

Per `docs/PHASE1-STEP2-PREMERGE-AUDIT.md` (commit `cea05a8` on main):
- 20 same failures, 73 same skips
- Same `(a)` / `(b)` distribution
- Same `0` (c)

Step 3 introduces 89 new passing tests on top of this baseline. No regressions.

## Skip audit (pre-merge supplement)

73 skips total. Distribution:

| Reason | Count | Environmental? |
|---|---|---|
| `Dashboard not running at localhost:8090` | 63 | Yes — requires `pm2 start codec-dashboard` (already running in production but not on the pytest sandbox). |
| `Set CODEC_TEST_TOKEN env var to run authenticated endpoint tests` | 7 | Yes — requires CI-only test token. |
| `Auth enabled without dashboard_token` | 3 | Yes — requires a dashboard auth token to be configured. |

**Verified: zero skips silenced by Step 3 code.** All 73 are environmental gates that predate Step 3.

Confirmed via:
```bash
grep "^SKIPPED" pytest_output | awk '{...counts...}' →
  Dashboard: 63, TEST_TOKEN: 7, dashboard_token: 3 (sum 73)
```

No `pytest.skip(...)` was added to any Step 3 test file. The 5 new test files use `pytest.fixture`/`pytest.skipif`-via-imports only (e.g. `pytestmark = pytest.mark.skipif(not _DASH_OK, ...)` in `test_step_budget.py` is a fallback for missing pynput which is a pre-existing build constraint).

## Sign-off

- ✅ All 20 failures classified.
- ✅ Zero (c) "needs investigation" entries.
- ✅ Identical baseline to Step 1 + Step 2 audits.
- ✅ All 73 skips are environmental, not silenced by Step 3.
- ✅ Hotfix PR #6 (test_mcp_all_tools.py SKIP_SKILLS additions) merged into main as `fcbef2f` and pulled into Step 3 via rebase.

**Step 3 PR #5 is clean to merge from a baseline-failures standpoint.** Awaiting reviewer approval per workflow contract — no auto-merge.

Outstanding items per the rebase incident at 13:21 UTC (also documented in `docs/INCIDENT-2026-05-01-spurious-skill-fires.md`):

- [ ] Tighten the `temp_askuser_paths` and `temp_audit_log` fixtures in the 5 new Step 3 test files so the monkeypatch always sticks even on full-suite runs with module-cache reentry. The current fixtures DID leak 11 entries into `~/.codec/pending_questions.json` and `~/.codec/notifications.json` during testing today — already cleaned.
- [ ] Add `self_improve` to SKIP_SKILLS in `tests/test_mcp_all_tools.py` (separate from the macOS UI side-effects; this one writes Qwen-drafted markdown proposals on every test run).

These two items can land as a follow-up commit on the Step 3 PR before merge, OR as a small post-merge cleanup PR. Either path keeps the audit clean.
