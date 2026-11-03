# iMessage bridge: permission errors logged once, a slower poll, one notification (P-058)

Status: approved by the owner on 3 Nov 2026 ("go go build"). Card D-12, owner decision 14: his own
bridge was stopped on purpose on 29 Oct (Full Disk Access still denied); this change is for buyers,
whose launchd fleet runs the same bridge.

## What was wrong

`codec_imessage.get_new_messages` caught every Messages-database error, logged `DB read error:
authorization denied` and returned `[]`. The loop then slept 3 s and tried again, forever: 635,825
lines over 33 days (27.7k a day), while PM2 said `online` and Settings > Connectors said "Running".
Nobody was told which program macOS refused. The cause is most likely Full Disk Access, which macOS
grants per program; that is a guess, so the texts say "most likely".

## What changes

Only `codec_imessage.py` and `routes/connections.py` (plus tests).

1. **Reads are counted** (`DbReadHealth`, one instance `_db_health`, clock injectable).
   `get_new_messages` calls `failed(exc)` or `succeeded()`. A failure is a *denial* when the text says
   `authorization denied`, `not permitted` or `permission denied`, when it is a `PermissionError`, or
   when it says `unable to open database file` and opening `chat.db` directly raises `PermissionError`.
2. **Log lines:** the first failure is logged as before (`DB read error: ...`, plus which program needs
   Full Disk Access when it is a denial); after that at most one line per 5 minutes
   (`FAIL_LOG_EVERY = 300`), with the count in a row. When reading works again, one line says so.
3. **Slower poll:** after 10 failures in a row (`FAIL_SLOW_AFTER`) the loop sleeps
   `max(poll_interval, 300)` instead of 3 s. About 12 lines an hour instead of 1,200.
4. **One notification:** at the 10th denial in a row, one Inbox entry through the existing locked writer
   `routes._shared._save_notification` (its `_notif_lock` plus the cross-process `file_lock`; no new
   writer of `notifications.json`). It names `os.path.realpath(sys.executable)`, says where to add it
   (System Settings > Privacy & Security > Full Disk Access), and says to stop the bridge if it is not
   used. The time it was sent and the program are kept in the state file, so a restart does not send
   another; it is cleared when reading works again, so a later loss of access is told once more, and a
   different program (a Python upgrade moves the real path) is told again.
5. **State file** `HEALTH_FILE = ~/.codec/imessage_health.json` (module constant; written with
   `codec_jsonstore.atomic_write_json`, 0600):
   `{state, since, checked, failures, program, denied_since, notified_at}`, `state` one of `ok`,
   `needs_permission`, `error`, `stopped`. Written when the state changes, with every logged failure
   (so at least every 5 minutes while it cannot read), when the notification is sent, and on a clean
   stop (`stopped`, keeping `denied_since` and `notified_at`). No message text, sender or error text.
6. **No backlog after a denial:** when the database becomes readable again after a denial (in the same
   process or after a restart, read from `denied_since`), or when the first-run start position could not
   be read, the bridge starts from the newest message, as a first run does, instead of answering what
   came in while it could not read. Without this, granting Full Disk Access as the notification asks
   would make a running bridge answer from ROWID 0 (the oldest messages first, 20 every poll) when its
   start position was never read.
7. **Settings > Connectors:** `bridges()` gets the iMessage `running` value from
   `imessage_running(pm2_state)`: when PM2 says `online` (or there is no PM2, as on a buyer's launchd
   Mac) and the state file says `needs_permission` and was checked in the last 15 minutes, `running` is
   `"needs permission"`; the page shows "Not running (needs permission)". A PM2 `stopped` or `errored`
   wins, and a stale file is ignored. `messages_db_readable()` (the dashboard's own check) is unchanged.

No new audit event, no new dependency, no `memory.db`, audit envelope, sandbox or PM2 change.

## Test plan (`tests/test_imessage_backoff.py`, one test in `tests/test_connections.py`)

Every test uses a stub in place of `sqlite3` (or a tmp database), a fake clock, and tmp paths for
`MESSAGES_DB`, `STATE_FILE`, `HEALTH_FILE`, `CONFIG_PATH` and `routes._shared.NOTIFICATIONS_PATH`;
`main()` runs with a fake sleep, a stubbed lifecycle and audit, and stops after N denials.

- One day of denials through `main()`: the first line at once, then at most one per 300 s, at most
  290 lines in 24 h; sleeps 3 s for the first 9, then 300 s.
- 50 denials: exactly one notification, through `_save_notification`, naming the real interpreter path;
  a restart and 50 more: still one.
- The state file: `needs_permission`, 0600, written through `atomic_write_json`; `ok` after a good read,
  `stopped` after the cleanup.
- Recovery: messages that came in while denied are not answered; the next new one is.
- Classification: a locked database slows the poll after 10 but makes no notification.
- Connections: `needs permission` from a fresh file over PM2 `online`; PM2 `stopped` wins; a stale file
  is ignored; the two modules name the same file.

## Rollback

Revert the commit and restart `codec-imessage` and `codec-dashboard`. The state file can stay or be
deleted; nothing else reads it.
