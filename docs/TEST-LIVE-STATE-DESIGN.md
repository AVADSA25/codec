# The test suite stays off the owner's files, ~/.codec and Keychain (P-009)

Status: approved by the owner on 2026-11-03 ("go go build i trust ur judgment here"), audit plan of 25 Oct 2026, findings GP12-1, GP12-2, GP12-3, GP12-5, BD-8, plus the follow-up folded in on 3 Nov (P-081, P-062, P-060 and P-016 reviewers).

## What and why

A full `pytest` run on the Mac Studio reaches the owner's real state:

- `tests/test_file_write.py` writes and then deletes `~/Documents/plan.md`, `~/Desktop/scratch.md` and `~/Downloads/note.txt` (an owner file with one of those names would be overwritten and removed).
- Tests that post agent messages without patching `codec_agent_messaging` grow `~/.codec/agents/test_agent/messages.jsonl` (2.7 MB) and add test entries to the live `notifications.json`.
- `tests/test_mcp_all_tools.py` runs the `shift_report` skill, which posts a manual shift report and marks today as fired, so the owner's real idle report is suppressed (219 manual reports in the live list).
- `tests/test_full_product_audit.py` runs `DELETE FROM conversations WHERE session_id LIKE 'test_%'` (and three more patterns) on the live `memory.db` after every run.
- `codec_keychain` is not redirected: every run reads the real audit HMAC secret and other entries through `/usr/bin/security`, and the Keychain tests create and delete real `ai.avadigital.codec._test_*` items (13 are left on the Studio).
- `codec_ask_user` keeps its pending questions and question banners in the live files.

## Change (tests only, no runtime file)

`tests/conftest.py` gains three helpers in the style of `_isolate_audit_log` (run at conftest import time, before any test module is imported) and two fixtures. The code they share lives in `tests/live_state_guard.py`, a plain helper module (not collected).

1. `_isolate_keychain()`: writes a small fake `security` program to a temp folder and points `codec_keychain._SECURITY_BIN` at it; the headless fallback store (`secret.key`, `secrets.enc.json`) points at the same folder. The fake keeps its items in `items.json` next to itself and answers like the real tool: `find-generic-password` (with or without `-w`) 0 or 44, `add-generic-password` 0, or 45 when the item exists and `-U` is not given, `delete-generic-password` 0 or 44, and 51 for every command while a file named `locked` sits next to it. On a Mac `is_keychain_available()` is still true (the fake exists), so the Keychain code path is exercised; on Linux it is false as before.
2. `_isolate_notifications()`: points every module-level `notifications.json` / `pending_questions.json` path at one temp folder: `codec_ask_user` (`PENDING_QUESTIONS_PATH`, `NOTIFICATIONS_PATH`), `codec_agent_messaging` (`_CODEC_DIR`, `_AGENTS_DIR`, `_NOTIFICATIONS_PATH`), `codec_watchdog.NOTIFICATIONS_PATH`, `codec_daybreak._NOTIFICATIONS_PATH`, `routes._shared.NOTIFICATIONS_PATH`. Tests that patch these per test keep doing so. Paths built inside a function (`codec_heartbeat`, `codec_scheduler`) and the skill modules loaded by the registry are covered by the guard below.
3. `_guard_live_state()`: only when the owner's `~/.codec` existed before the run (so on CI it does nothing). It wraps `open` / `io.open`, `os.open`, `os.replace`, `os.rename`, `os.remove`, `os.unlink`, `sqlite3.connect` and `subprocess.Popen` in this process and refuses, with `LiveStateRefused` (a `PermissionError`), any write to `~/.codec/memory.db*`, `notifications.*`, `pending_questions.*`, `audit.log*` and `shift_report_state.*` (the last one added because a manual shift report test write suppresses the owner's real report), any `sqlite3.connect` to those files (read-only included), and any run of the real `/usr/bin/security`. Reads of the JSON files stay allowed. Every refusal is recorded, and the autouse fixture `_live_state_untouched` fails the test that caused one, even when the code under test swallows the error. `CODEC_REAL_KEYCHAIN_TESTS=1` lets the real `security` run, for the one opt-in real-Keychain test.
4. `_owner_watch_list` (session, autouse, macOS only): a read-only before/after look at a named list, not whole folders (live daemons write `audit.log` and the observer files every minute; Syncthing and other sessions write `~/Documents`):
   - `~/Documents/plan.md`, `~/Desktop/scratch.md`, `~/Downloads/note.txt`: never created, changed or removed;
   - `~/.codec/agents/test_agent/`: not created, recreated or grown;
   - `~/.codec/notifications.json`: no new or changed manual shift report entry and no new or changed test-named entry (an `agent_id` or `agent` field containing "test"); daily and idle reports from the live observer are not counted, and a live service trimming old entries is not a change;
   - `~/.codec/shift_report_state.json`: unchanged, compared only when no scheduled (time or idle) shift report was added during the run.
   It stats, hashes in memory and counts; it writes nothing and its message gives counts only, never file names.

Test fixes:

- `tests/test_file_write.py`: the three owner paths are checked with `_is_safe_target` only (nothing is written there); the real write goes to a fresh folder under `/tmp`.
- `tests/test_full_product_audit.py`: the live `memory.db` DELETE block goes; the schedule clean-up through the dashboard API stays. The module already skips its dashboard tests when the dashboard does not answer, so no token gate is added.
- `tests/test_mcp_all_tools.py`: `shift_report` joins `SKIP_SKILLS`, and so do `health_check`, `notification_reader` and `daily_kickoff` (found by the full run: they open the live `memory.db` and call the live dashboard with the internal token, which is now the fake one, so the dashboard wrote `internal_token_mismatch` lines to the live audit log).
- `tests/test_calc_pm2_guard.py`: the child process it starts points `codec_keychain` at a temp fallback store as well as the audit log.
- `tests/test_keychain.py`: one real-Keychain round trip, skipped unless `CODEC_REAL_KEYCHAIN_TESTS=1` on a Mac; it uses the per-test `_test_` service prefix and deletes its item.

Not done (out of scope, owner's word): no fake HOME, no global `_is_paid_edition` patch (it breaks `tests/test_license.py`), no edit of any live file, no Keychain item written or deleted. Purging the old junk (the `test_agent` folder, `agents/test.json`, `agents/testagent.json`, the manual shift report and test-named notification entries, the 13 `_test_*` Keychain items) is a separate delete that needs the owner's yes.

## API and data

None. No runtime module, schema, audit event or config key changes. `memory.db` isolation is #523's `_isolate_memory_db()` (P-026); this change does not duplicate it. Merge it after #523: without that redirect, the tests that open `memory.db` (for example `tests/test_auth_middleware.py`, `tests/test_heartbeat.py::test_heartbeat_runs`) are refused by the guard on the Studio. CI is not affected (no `~/.codec`, so the guard is off).

## Migration

None. Takes effect on the next `pytest` run; no service restart.

## Tests

`tests/test_live_state_guard.py`:

- the fake `security` answers 0/44/45/51 like the tool, and `codec_keychain` round-trips through it (its store holds the item; the real binary is never named);
- every redirected path is outside the real `~/.codec`;
- a guard built on a temp root refuses write opens, `os.open` with write flags, replace onto, delete of and `sqlite3.connect` to the protected names, allows reads and other names, and records each refusal (runs on CI);
- on the Mac, deliberate opens of the live paths (`os.open` with `O_WRONLY|O_APPEND` and no `O_CREAT`, a read-only `sqlite3` URI, `security -h`) are refused by the installed guard;
- the watch-list snapshot on a temp home reports a created file, a grown `test_agent` folder, a new manual shift report and a changed state file, by count, without file names, and ignores a new idle report and a trimmed list.

Proof on the Studio: one full run with the isolation in place, compared with the known failures, and a before/after record (no secret printed) of the audit secret's sha256, today's `audit.log` line count and the new lines' sources and signatures, the `conversations` row count (read-only URI) and the sha256 of `notifications.json`.

## Rollback

`git revert` of the PR. Only test files and this note change; nothing on the owner's Mac needs undoing.
