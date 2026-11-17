# Headless secret store: every write under one lock

Status: fix for the known issue "Headless secret store: other keys are written without the lock" (found 2026-11-03 in the P-081 review). Follows docs/AUDIT-SECRET-KEEP-DESIGN.md (P-081).

## What and why

On a machine without the macOS Keychain (Linux CI, any headless install), `codec_keychain` keeps secrets in `~/.codec/secrets.enc.json`, encrypted with the key in `~/.codec/secret.key`. P-081 put the audit secret's read and create inside `codec_jsonstore.file_lock(secrets.enc.json)`, but every other write did load, change, save with no lock:

- `_fallback_set` and `_fallback_delete`: two processes saving at once each write back the store they read, so one change is lost. That includes the audit secret: a daemon saving `internal_token` can write back a store read before the audit secret was added, the next cache miss finds it missing and creates a new one, and lines signed with the old one read as broken.
- `_fallback_key`: two processes starting at once both see no key file, both create one through the same `secret.key.tmp` name, and one can end up encrypting with a key that is not the stored one (or fail when the other moved the temp file away).
- Both writers use one fixed temp name (`.enc.json.tmp`, `.key.tmp`), so two unlocked saves can also make one of them fail outright.

## Change (codec_keychain.py, fallback store only)

- One lock: `codec_jsonstore.file_lock(_FALLBACK_STORE_PATH)` (sidecar `secrets.enc.json.lock`), the one the audit path already takes.
- `_fallback_set` takes the lock and calls `_fallback_set_in_lock`, the old body. `_fallback_delete` does its load, pop, save inside the lock.
- `_fallback_key` returns the key at once when the file exists. When it is missing it takes the lock and calls `_fallback_key_in_lock`, which checks again and creates the key only if it is still missing, so racing processes end with one key.
- The lock is not re-entrant. Code that already holds it calls the `_in_lock` helpers: `_fallback_set_in_lock` reads or creates the key with `_fallback_key_in_lock`, and `_audit_secret_create` now calls `_fallback_set_in_lock` (calling the locked `_fallback_set` there would wait forever on its own lock).
- `_fallback_get` reads the key file directly and never creates a key: a stored entry cannot be decoded with a fresh key anyway, and a read inside the audit path must not take the lock. A missing key file now reads as None (before, it made a new key that decoded nothing).
- Reads take no lock: every save is an atomic replace.
- The audit-secret path stays silent: no `_kc_log_event`, no `codec_audit` call while the lock is held (AGENTS.md section 10). `keychain_set` writes its audit line after `_fallback_set` has released the lock, so the audit writer, which takes the same lock for the secret, never waits on it.

Not changed: everything that runs when the real Keychain is available (`_keychain_*`, the public functions' Keychain branch, the Keychain branch of the audit path). On the Mac no fallback file and no lock file are touched. No change to the store format, the key format, key names or audit events.

## Tests

`tests/test_headless_secret_lock.py`, with the fallback store under `tmp_path`, `is_keychain_available` patched to False, racers pointing `codec_audit` at `tmp_path` and `_SECURITY_BIN` at a path that does not exist. No HOME change, no real Keychain, no real `~/.codec`.

- Four processes at once (three sets of different keys, one delete), each save slowed: all three keys stored, the deleted one gone.
- Four processes call `_fallback_key()` at once on a fresh folder, key creation slowed: all get the same key, the stored one, mode 0600.
- One process creates the audit secret while another stores `internal_token` through `keychain_set`: both survive, and the stored audit secret is the one the first process signs with.
- The audit path never waits on its own lock: on an empty store it creates the key and the secret; with an entry but no key file it returns None, replaces nothing and makes no key.
- With the Keychain available (fake `security`), set, get, delete and the audit secret create no fallback file and no lock file.

The first four fail on the code before this change. `tests/test_audit_secret_keep.py`'s racer now slows `_fallback_set_in_lock`, the writer the audit path calls.

## Rollback

Revert the PR. No data to undo: the store and key files keep the same format.
