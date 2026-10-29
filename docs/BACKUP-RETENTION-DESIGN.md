# Backups that check themselves, a restore tool, and stated retention

Plan item P-018 (audit 25 Oct 2026). Owner decision D-38, 29 Oct 2026: "Chats 90 days as a visible setting;
clipboard and webcam photos 30 days; delete the old March memory backup after a verified restore works; show
every deletion in backup status."

## Why

- The heartbeat copied memory.db, qchat.db and vibe.db every day with no check: a damaged database would have
  been copied into every daily file, one by one, until no good copy was left. Copies were 0644.
- Three writers made memory.db copies with two names: the heartbeat (`memory_<date>.db`, 7 kept), the launchd
  job `com.codec.memory-backup` running `scripts/backup_memory.sh` at 03:15 (`memory-<date>.db`, 30 kept), and
  "backup now" in `skills/backup_status.py`, which used `shutil.copy2` on a WAL database (the `-wal` file, with
  the newest rows, was left behind) and wrote over the launchd job's good copy of the day.
- config.json, notes/, standing_rules.json, schedules.json, voices/codec-02 and plugins.allowlist were in no
  backup. There was no way to restore anything.
- The heartbeat deleted conversations older than 90 days with no setting and no record of what it deleted.
  Clipboard history and webcam photos were kept forever.

## What

`codec_backup.py` is the one module. Every path hangs off `codec_backup.CODEC_HOME` (`~/.codec`).

**One SQLite backup writer: `backup_sqlite(db, prefix, keep, force)`.**
1. Copy through the sqlite backup API (safe while CODEC writes, WAL included) into a hidden temp file that is
   0600 from the start, in `~/.codec/backups/` (0700). The copy is switched to `journal_mode=DELETE`, so it is
   one self-contained file.
2. `PRAGMA quick_check` on the new copy. Only `ok` passes.
3. Pass: the copy replaces `<prefix>_<date>.db` atomically, the series keeps its newest `keep` dates (older
   names of the same series rotate with it), kept files are tightened to 0600, the status file records it, and
   a past alert is cleared (`codec_alerts.alert_resolved`).
4. Fail: the temp file is removed, nothing is replaced, the older copies stay, the owner gets one alert
   (`codec_alerts.alert_once("backup_check:<prefix>")`, at most every 6 h) and one `backup_check_failed` audit
   line. "Backup now" uses `force`, so it can make a new copy the same day, and still never replaces a good copy
   with a bad one.

Callers: the heartbeat (`backup_memory_db` -> `daily_backups`: memory.db 30 kept, qchat.db and vibe.db 7 kept,
the state tar), the launchd script (`python3 -m codec_backup memory`; it does nothing when the heartbeat already
made the day's checked copy), the skill's "backup now", and the retention and restore steps below. The
heartbeat's `_backup_one_db` and `_backup_agents_state` are removed. memory.db keeps 30 because the launchd
copy it replaces kept 30; the old `memory-<date>.db` files rotate in the same 30-date series instead of being
orphaned.

**The state tar** (`backup_state`): the heartbeat's agents tar, extended. `state_<date>.tar.gz` holds `agents/`,
`config.json`, `notes/`, `standing_rules.json`, `schedules.json`, `voices/codec-02/` and `plugins.allowlist`;
no links, devices, `.lock`, `.tmp` or `.partial` files. 0600, the newest 7 dates kept (the old `agents_<date>`
tars rotate with them). Every member is read back (tar structure and gzip CRC) before the tar replaces anything.
It holds config.json, so it is as private as config.json: 0600 in a 0700 folder, on the same disk.

**Retention** (`run_retention`, once a day from the heartbeat, after the backups):

| What | Setting (`config.json:retention`) | Default | Allowed |
|---|---|---|---|
| Conversations in memory.db (what CODEC recalls from chats and voice) | `chats_days` | 90 | 0, or 7 to 3650 |
| Clipboard history (`clipboard.db`, table `clips`) | `clipboard_days` | 30 | 0, or 1 to 3650 |
| Webcam photos (`photos/webcam_<date>_<time>.jpg`, by the time in the name) | `photos_days` | 30 | 0, or 1 to 3650 |

Defaults: Owner decision D-38, 29 Oct 2026. 0 keeps that kind forever. A value outside the allowed range is
ignored and the default is used (it never deletes more than asked).

- Conversations: no clean-up runs without a memory.db copy that passed its check the same day. Before the
  first clean-up with a new number (including the first run after this ships, and any change later), an extra
  checked copy is taken, `memory_pre-retention_<date>.db` (3 kept), so a wrong clean-up can be undone from it.
  The delete itself is the existing `CodecMemory.cleanup` (DELETE, FTS rebuild, VACUUM). No schema change.
- Clipboard: rows older than the cutoff are deleted, then VACUUM, so the text leaves the file.
- Photos: only regular files named the way CODEC's webcam routes name them; other files and links are left alone.
- Every run records, per kind, the time and the count (`~/.codec/backup_status.json`, 0600). Each non-zero
  count is also kept in a list of the last 50 deletions and written as one `retention_cleanup` audit line
  (kind, count, days; never content).

**Settings > Memory, "How long CODEC keeps things"**: the three numbers, saved to `config.json:retention`
through `PUT /api/memory/settings` (`{"retention": {...}}`, checked before anything is saved; a bad number is a
400 and changes nothing), one `memory_changed` audit line with action `retention_set`. Lowering a number asks
once more on the button. The section shows the counts of the last clean-up. The chat list (qchat.db) is not
trimmed, and the page says so.

**Backup status** (`codec_backup.status_report`, used by the `backup_status` skill): for each database the last
checked copy, its size and age, the check result (and a failed attempt if the last one failed), how many days
are kept; the state tar and what it holds; the retention numbers; the last clean-up's counts and the recent
deletions. File names, sizes and counts only. The skill stays MCP-exposed; it has no restore.

## Restore rules (`tools/codec_restore.py`)

A tool for the owner at the Mac, never a skill: no chat, voice, agent, MCP or HTTP path reaches it.

- `list`, `check BACKUP`: read only. `check` runs quick_check on a database or reads every member of a tar.
- `restore BACKUP --to DIR`: into a new or empty folder outside `~/.codec`. Writes nothing live, so it asks
  nothing. A database is copied and proved by sha256 (equal to the backup) and quick_check; a tar is unpacked
  with tarfile's `data` filter and every file's sha256 is compared with the backup's. This is the "verified
  restore" D-38 asks for before the old March backup is deleted.
- `restore BACKUP --live [--only ITEM ...]`: writes AGENTS.md section 10 files, so it asks first through
  `codec_ask_user.ask(destructive=True, destructive_verb="restore")` (the question shows in CODEC's Inbox on the
  Mac and the phone) and goes ahead only when `codec_ask_user.answer_consents(answer, "restore")` holds: "yes",
  "ok", "Cancel", a refusal, a timeout or ask_user switched off all refuse. It reads only from
  `~/.codec/backups`.
  - A database: the current one is first copied through `backup_sqlite` (`<name>_before-restore_<date>.db`, 3
    kept). If that copy fails its check (the current file is damaged, the usual reason to restore), it stops
    unless `--replace-damaged` is given. The restore uses the sqlite backup API into the live file, so CODEC can
    stay running; afterwards quick_check and a content checksum against the backup.
  - The state tar: unpacked into a hidden staging folder and checked by sha256 before anything live is touched;
    then each item's current file or folder moves to `backups/before-restore_<time>/` and the restored one takes
    its place. Restart CODEC afterwards so it reads the restored settings (the owner's action).
- **plugins.allowlist is never restored**, in either mode and not even with `--only`: a restored allowlist
  would grant plugins trust. It is in the tar only so the owner can compare it by hand (`check`). Plugins are
  approved again with `plugin_approve`.
- One `backup_restored` audit line per restore or refusal (backup name, mode, item names, result).

## Cost

Disk on this design's numbers: about 30 small memory.db copies, 7 copies each of qchat.db and vibe.db, 7 state
tars (the voice reference clip does not compress) and up to 3 pre-retention copies: tens of MB at most.
Time: one backup-API copy and one quick_check per database per day (milliseconds at these sizes), one tar per
day. No new process, no new dependency, no new PM2 service.

## Tests

- `tests/test_codec_backup.py`: a checked 0600 copy with N kept; a deliberately corrupted fixture is rejected,
  the older copies are kept byte-identical and the alert fires; a bad copy never replaces a good one of the same
  day; old script names rotate and are tightened; the state tar holds every item, no links or lock files, 0600,
  7 kept; retention defaults; memory.db copied before the first clean-up with a new setting and the deleted
  rows recoverable from it; no clean-up without a checked copy; old clipboard rows and photos deleted, other
  files and links kept; 0 keeps everything; status shows the checks, the tar, the numbers and the counts; the
  skill's "backup now" goes through the one writer; the heartbeat calls codec_backup and has no writer of its
  own; the suite's `CODEC_HOME` is a temp folder.
- `tests/test_codec_restore.py`: database and tar round trips into a temp folder with equal checksums; the
  allowlist left out; refusals for a folder inside `~/.codec`, a full folder, a backup outside the backups
  folder; a live restore needs the literal word (six refusing answers); the before-restore copy; a live state
  restore moves the current files aside and leaves the live allowlist alone; the tool is not a skill.
- `tests/test_backup_retention_settings.py`: the Settings route shows the defaults, saves a number without
  touching the rest of config.json, refuses bad numbers without saving anything, shows the last counts; the
  page has the fields.
- `tests/conftest.py` points `codec_backup.CODEC_HOME` at a temp folder at import time, and
  `tests/test_heartbeat.py` stubs the backup and the memory clean-up: its real heartbeat cycle used to back up
  into and clean the owner's own memory.db on every test run.

## Takes effect

After a restart of `codec-dashboard` (the heartbeat runs inside it); the launchd script picks up the new code at
its next 03:15 run. Restarts are the owner's action. On a Mac where every clipboard entry and photo is older than
30 days, the first daily run deletes all of them; set the number to 0 in Settings > Memory (or
`config.json:retention`) before the restart to keep them.

## Rollback

`git revert` of the PR. Backups already written stay (deleting them is the owner's word); the old code ignores
`state_*.tar.gz`, `memory_pre-retention_*` and `backup_status.json`. The old heartbeat goes back to deleting
conversations after 90 days and keeping clipboard and photos forever; `config.json:retention` is then unused.
A wrong clean-up is recoverable from `memory_pre-retention_<date>.db` (taken before the first run with each new
number) or the daily copies, with `tools/codec_restore.py`.

## Not in this change

- A second location: backups stay on the same disk, so a dead disk is not covered.
- qchat.db (the chat list) has no retention and was never trimmed; `routes/qchat.py` keeps its own one-time
  pre-migration copy of qchat.db. Both are left as they are.
- Deleting the old April-dated memory backup (`memory.db.bak-<epoch>`, the "March" one): the owner's word,
  after a verified restore with `tools/codec_restore.py restore <daily copy> --to <folder>`.
