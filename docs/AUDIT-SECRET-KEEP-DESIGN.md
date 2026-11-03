# Audit log keeps its signing secret and counts the lines it drops (P-081)

Status: approved by the owner on 2026-11-03 ("go go build i trust ur judgment here"), audit plan of 25 Oct 2026, findings BC-3 and BH-6.

## What and why

Two faults in the audit trail, both in section 10 zones (the `audit_hmac_secret` Keychain entry and the audit envelope).

1. **The secret can be replaced.** `codec_keychain.get_audit_hmac_secret()` treated every non-zero answer from `security find-generic-password` as "missing": exit 44 (not found), 51 (locked), a timeout and any other error all led to a new secret written with `add-generic-password -U`, which overwrites. Ten daemons starting together on a fresh Mac, or one daemon reading while the Keychain is locked, can each write their own secret. Lines signed with the replaced secret then read as tampered (a false alarm), and the real secret is gone. There was no cross-process lock on the fallback store and no negative cache, so a locked Keychain was asked again on every audit line.
2. **Dropped lines are silent.** `codec_audit._write` ends both of its steps with `except Exception: return/pass`. A full disk or an unwritable log stops the audit trail with no counter and no log line, and `verify_audit_log()` still reports integrity OK on what is left.

## Change

`codec_keychain.py` (the audit secret path only; every other key is unchanged):

- `_audit_secret_read()` sorts the answer into three states: `ok` (exit 0), `missing` (exit 44 only) and `down` (exit 51 locked, any other exit, timeout, program not runnable). On the fallback store, `missing` means no file or no entry; an unreadable store or an entry that does not decode is `down`.
- Only `missing` creates a secret. On the Keychain it is created with `add-generic-password` **without `-U`**: if another process made it first the tool answers 45 (already exists), and we read and use the stored one. On the fallback store the read and the create happen inside `codec_jsonstore.file_lock(<store>)` (sidecar `secrets.enc.json.lock`), re-reading inside the lock, so racing processes end with one secret.
- `down`, a value that is not hex, or a failed create return None (lines are written unsigned, as before) and are remembered for 30 s (`_AUDIT_HMAC_DOWN_TTL`): no new `security` call until then. One stdlib warning per retry, so at most one per 30 s per process.
- Still silent: no `_kc_log_event`, no `audit()` on this path (the non-reentrant `_LOCK` rule). Stdlib logging only.
- The two helpers this replaces (`_keychain_get_silent`, `_keychain_set_silent`) are removed; nothing else used them.

`codec_audit.py`:

- `_write` counts every line it cannot write in a module counter (`write_failures()`), per process, and logs a stdlib warning at most once per 60 s with the running total and the error type (never the line). No audit line, no change to the record, the HMAC or the file.

`routes/health.py`: `GET /api/status` (signed-in only) adds `audit_write_failures`, the dashboard process's count since it started (None if it cannot be read).

## API and data

No schema change, no key rename, no new audit event, no new file except the fallback store's `.lock` sidecar (headless only). The envelope is byte-for-byte the same (golden-record test). The Keychain entry name and format (64 hex characters) are unchanged.

## Migration

None. Existing secrets are read as before. Takes effect after the owner restarts the daemons that import `codec_audit` (all CODEC PM2 processes, for example `pm2 restart codec-dashboard open-codec codec-mcp-http ...`); until then they run the old code.

## Tests

`tests/test_audit_secret_keep.py`. Every test points `_SECURITY_BIN` at a fake `security` script under `tmp_path` (a small file store that answers 0/44/45 like the real tool, with switches for 51, other codes and a slow answer), and points the fallback store, the audit log and its `.lock` at tmp paths. None sets HOME or touches the real Keychain.

- golden record: the line written for fixed inputs equals the one captured on main before this change; `verify_audit_log()` is OK.
- exit 51: None, no add, no second `security` call within 30 s, one call after 30 s, stored secret untouched, nothing written to the audit log.
- exit 36, exit 1, timeout: same, never treated as missing.
- exit 44: one add, without `-U`, the stored value is the returned one, read back after the cache is dropped, still one add.
- add refused with 45: the secret another process stored is used.
- not-hex value: None, never replaced.
- three processes racing on the fallback store: all end with the same secret, the stored one (this test fails on the old code).
- unwritable log (a folder at the log path): counter goes up per line, one warning per 60 s.
- full disk (ENOSPC): verify OK before and after, the lost lines are counted.
- `GET /api/status` shows `audit_write_failures`.

## Rollback

Revert the PR and restart the same daemons. No data to undo: the secret is never rewritten by the new code, and the counter lives in memory.
