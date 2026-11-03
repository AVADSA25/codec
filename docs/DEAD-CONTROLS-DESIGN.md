# Controls that do nothing, and claims that are not true (P-035)

Owner's go, 3 Nov 2026: "go go build i trust ur judgment here" (the 25 Oct audit plan, item P-035).
The picks below were made by the session lead under that go and are quoted in the PR.
Status: built in the PR that carries this file; live after the owner restarts `codec-dashboard`.

## What and why
A control that saves and then changes nothing turns into a support message, and a new buyer's Inbox
showed four reports CODEC never wrote. Each item is fixed or removed, each with a test.

1. **No sample reports.** `routes/_shared._load_notifications` replaced a missing or unreadable
   `~/.codec/notifications.json` with four invented March-2026 reports ("Security Scan: 142
   dependencies, no CVEs", one of them unread) and wrote them to disk. The Inbox polls this function,
   so a clean Mac showed a badge for work that never happened. Now a missing file is an empty list and
   nothing is written. A file that is not valid JSON, or is valid JSON but not a list, is copied to
   `notifications.json.corrupt-<YYYYmmdd-HHMMSS>` (the file's own modification time, so repeated polls
   make one copy) and read as empty; the file itself is left as it is until the next real write.
2. **Mark-read takes the cross-process lock.** `POST /api/notifications/{id}/read` and
   `/api/notifications/read-all` held only the dashboard's thread lock, while the scheduler, heartbeat,
   ask_user and agent writers in other processes hold `codec_jsonstore.file_lock`. A mark-read racing
   an append from another process wrote back its old copy and dropped the new entry. Both now hold
   `_notif_lock` and `file_lock(NOTIFICATIONS_PATH)`, like `_save_notification` and `/api/inbox/read`.
   Read-all writes only when it marked something, so it never creates a file.
3. **Heartbeat settings removed (BO-6).** `GET/PUT /api/heartbeat/config` saved `heartbeat_enabled`,
   `heartbeat_interval` and `heartbeat_tasks`, which nothing reads: the heartbeat runs inside
   `codec-dashboard` every 30 minutes. The two routes, the "Heartbeat Configuration" card on Tasks >
   Heartbeat and its script are removed; the "How Heartbeat Works" text now says every 30 minutes
   instead of "at your chosen interval". Alerts (`/api/heartbeat/alerts`, read by the heartbeat) stay.
4. **Compare cloud tiers (DC-061).** `codec_compare.default_endpoints` called `ava.is_ready()`, but
   `AvaConfig.is_ready` is a property, so the call raised, was caught, and the cloud tiers never
   appeared. The call is fixed, and the tiers now also need `config.json:compare.cloud_tiers` to be
   exactly `true` (default: absent, so off). No paid cloud path is switched on by this change. The old
   list form of `cloud_tiers` (custom labels and models) is no longer read: it never took effect,
   because of the bug.
5. **Marketplace voice entry removed (DC-050).** `codec_marketplace.py` carried `SKILL_*` constants and
   a `run()` that installed a downloaded skill without the sha256 check the command line uses. Root
   modules are not scanned by the skill registry, so it could not be reached. The constants, `run()`
   and their tests go; the command line stays.
6. **Google Keep out of the daily briefing crew (DC-011).** `skills/google_keep.py` only answers that
   Keep has no API. The `daily_briefing` crew no longer lists it as a tool or tells the scout to check
   Keep.
7. **prompt_feeder (DC-004):** kept as it is (parked on purpose).
8. **Skill count.** The README badge and text, CONTRIBUTING.md and FEATURES.md said 76, 88, 89 or 75.
   They now state the number of non-underscore `skills/*.py` files, and a test computes that number and
   checks every current claim against it (the dated v3.2 snapshot line in FEATURES.md stays as
   history). The test that pinned 76 now compares with the same count, and a test checks that the
   Skills page lists one entry per registered skill.

## API and data
- Removed: `GET /api/heartbeat/config`, `PUT /api/heartbeat/config` (404 now). Keys already in a
  config.json stay and are ignored, as before.
- `/api/notifications`, `/count`, `/api/inbox` answers are unchanged in shape; on a clean Mac they are
  empty.
- New side file only when the notifications file is unreadable: `notifications.json.corrupt-<time>`.
- New optional key `compare.cloud_tiers: true`. No new audit event, no schema change.

## Migration
None. An install that already has the four sample reports keeps them until they fall off the list or
the owner clears them; nothing rewrites a real notifications file except mark-read.

## Tests
- `tests/test_notifications_start_empty.py`: an empty temp folder stays empty after the list, count and
  Inbox reads, all empty with badge 0; a corrupt file is copied aside once and its bytes kept.
- `tests/test_notifications_lock.py`: mark-read and read-all, each interleaved with an append from a
  second Python process on a temp file, keep both.
- `tests/test_route_extractions.py`, `tests/test_config_writer.py`: the heartbeat config routes and the
  page card are gone; alerts still save and merge.
- `tests/test_compare.py`: the property is used (real `AvaConfig`), tiers need the switch exactly
  `true`.
- `tests/test_marketplace.py`, `tests/test_all_crews_build.py`, `tests/test_readme_investor.py`,
  `tests/test_skills_page.py`.

## Rollback
Revert the PR and restart `codec-dashboard`. The sample reports are not restored, and a corrupt-file
copy made meanwhile stays where it is.
