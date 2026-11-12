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
   - `~/.codec/notifications.json`: no new or changed manual shift report entry and no new or changed test-named entry (an `agent_id` or `agent` field containing "test"); daily and idle reports from the live observer are not counted, and a live service trimming old entries is not a change, nor is the owner reading his inbox (the `read` flag and the `auto_` id the inbox gives an entry written without one are left out of the comparison);
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

## Follow-up, 12 Nov 2026: overlay events, licence files, config.json

Found by two builds: a full run on the Studio appended `skill_fired` lines (calculator, terminal) to the live `~/.codec/overlay_events.jsonl`, which the Swift overlay reads, and rewrote `license_pubkey.pem` and `.license_state.json` in the same second.

- **Overlay.** `codec_dispatch.run_skill` (the `skill_fired` line) and `codec_core._swift_text_input` (the input request) built their own `~/.codec/overlay_events.jsonl` path, so conftest's redirect of `codec_overlays._EVENTS` missed them. Both now write through `codec_overlays._EVENTS`, the path the heartbeat's trim already uses. The Swift overlay still reads the same file; nothing changes outside tests. `tests/test_atomic_state_files.py`, `test_one_loader.py` and `test_skill_timeouts.py` pointed the file at their tmp folder through `os.path.expanduser`; they now set `codec_overlays._EVENTS` as well.
- **Licence.** `run_skill` asks `codec_license.feature_allowed` before every skill. With the owner's config (paid edition) that fetched the public key from the licence server and wrote the key cache and the grace state; `tests/test_top10_action_list.py` also stored a placeholder key in the cache. `codec_license` now builds `CODEC_HOME` from `CODEC_LICENSE_DIR` when set (tests only; nothing in CODEC sets it), and conftest sets it to an empty temp folder before anything imports the module, so running the module's code again keeps the temp folder. `tests/test_license.py`'s fixture used `importlib.reload`, which finds the file again on `sys.path`; skills put `~/codec-repo` first there, so in a worktree run the reload loaded the main checkout's copy with the live paths (the first full run of this change: the guard refused its writes, nothing was written, but the live config and key were read and the key server was called). The fixture now clears the module's caches instead of reloading it. Side effect: since today's deploy moved the live `ava.license_key` to the Keychain (R-03), the three API tests in `tests/test_agent_plan.py` (`test_post_api_agents_creates_drafts`, `_approve_full_flow`, `_reject_sets_reason`) got 402 on the Studio from the licence gate reading the live paid config; with the temp folder they pass (checked both ways). Its `config.json` does not exist, so the suite's licence check reads `{}` and answers `oss`, as on CI: no network call, no key read, nothing written under `~/.codec`.
- **Guard.** `PROTECTED_NAMES` gains `overlay_events.`, `license_pubkey.`, `.license_state.` and `config.json` (prefixes, so `config.json.tmp` and `config.json.lock` are covered). A write open, replace, rename or delete of them in the real `~/.codec` raises `LiveStateRefused` and fails the test. Reads stay allowed.
- **Not done: reads of the live `config.json`.** About 58 runtime modules build their own `~/.codec/config.json` path and read it, so tests on the Studio still see the owner's settings (CI sees none). Pointing them all at one path is a runtime change across many modules; it is written up with a plan in docs/known-issues.md ("Tests still read the owner's live config.json").

Tests (`tests/test_live_state_guard.py`): the overlay and licence paths are outside `~/.codec`, also when `codec_license`'s code runs again; an unpatched `run_skill` writes its `skill_fired` line to the temp file and the licence check answers `oss`; a fetched key and a grace time land in the temp folder; the guard refuses the four new names on a temp root and, on the Mac, on the real paths. Negative controls run once on the Studio: with the conftest overlay redirect removed, the skill-run test failed with "os.open for write of the owner's ~/.codec/overlay_events.* refused"; with the licence redirect removed, the licence test failed with "open for write of the owner's ~/.codec/license_pubkey.* refused". The live files were unchanged both times; the redirects were restored.
