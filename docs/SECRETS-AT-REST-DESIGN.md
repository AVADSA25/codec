# Secrets at rest: session id, test Keychain items, old config copies (P-088)

Audit findings GP06-8 and GP09-4 (25 Oct 2026). Owner approval for the plan: 2026-11-03.

## What and why

1. **Touch ID session id.** `/api/auth/verify` used the `token` the Swift helper
   (`codec_auth/main.swift`) printed as the dashboard session id. The helper fills that token with
   `SecRandomCopyBytes` and ignores its status, so on a failed call the session id would be 64 zeros.
   The route now makes the session id itself with `secrets.token_hex(32)` and ignores the helper's
   token, exactly like the PIN route. The helper is not changed and not rebuilt.
2. **Test Keychain items.** The Keychain tests (`test_audit_flock`, `test_audit_integrity`,
   `test_auth_middleware`, `test_keychain`, `test_remaining_secrets`) set an
   `ai.avadigital.codec._test_*` service prefix. P-009 (#528) already points
   `codec_keychain._SECURITY_BIN` at a fake `security` program for the whole suite
   (tests/conftest.py, items in a temp JSON file) and makes a run of the real `/usr/bin/security`
   fail the test. The only real-Keychain test is opt-in (`CODEC_REAL_KEYCHAIN_TESTS=1`) and deletes
   its item in a `finally`. None of the five sets HOME. Nothing to change in the tests; this item
   checks it: the `_test_` names in the real Keychain are the same before and after a run of the five.
3. **The 13 leftover `_test_` items** and 4. **old config copies** (an unsalted SHA-256
   `auth_pin_hash` from before the argon2id upgrade, and the licence JWT) are owner steps. This item
   adds a read-only listing, `tools/list_old_secrets.py`, so he sees exactly what would go before he
   says yes.
5. The live licence token stays in `config.json` (codec_license, codec_ava_client, codec_setup and
   codec_slash_commands read it there). No new read path is built.

## The listing (`tools/list_old_secrets.py`)

- Keychain: runs `security dump-keychain` without `-d`, so macOS returns item attributes and no
  values, and keeps the generic-password items whose service starts with `ai.avadigital.codec`.
  Three groups: test leftovers (service starts with `ai.avadigital.codec._test_`), never delete
  (`ai.avadigital.codec.audit_hmac_secret` and `ai.avadigital.codec.internal_token`, every account
  listed, so the second audit-secret account is visible), and a count of the other CODEC items.
- Config copies: every file in `~/.codec` and `~/.codec/backups` whose name starts with `config`,
  except `config.json` itself and its `.lock`/`.tmp`. A file is reported when it holds an
  `auth_pin_hash` of 64 hex digits (the old form; argon2id values start with `$argon2id$`) or any
  value of JWT shape (`eyJ...` three base64url parts). Setting names only; a file that is not JSON
  is matched on its raw text and reported as such.
- Prints names (items, accounts, files, settings), never a value. `--json` gives the same report
  as JSON. `--commands` prints, without running them, the `security delete-generic-password -s ...
  -a ...` lines for the test leftovers only; it refuses to print one for any other item.
- Writes, deletes and changes nothing. Stdlib only. Not imported by any service.

## Data, API, audit

No schema, config, audit or API change. The session table entry is the same shape; only where the
id comes from changes. No new audit event.

## Migration

None. Sessions made before the restart keep working until they expire. The owner restarts
`codec-dashboard` to load the route change.

## Owner steps (after the listing and his word)

- Delete each test leftover the listing names (`--commands` prints the lines), then check the
  listing shows 0 test leftovers and `verify_audit_log()` reports `integrity_ok` before and after.
  Never delete or rewrite an `audit_hmac_secret` or `internal_token` entry.
- Copy each config copy the listing names to `~/Documents/CODEC-private` before rewriting it
  without the old PIN hash and the JWT. New copies made later will hold the live licence token
  again while it stays in `config.json`.
- Whether to keep a single licence copy is his decision, logged as a dated line in
  `~/.claude/DECISIONS.md`.

## Test plan

- `tests/test_auth_cookie.py`: a fake helper answering a token of 64 zeros; the session id in the
  cookie and the session table is 64 hex digits, not the zeros, and two logins get different ids.
- `tests/test_list_old_secrets.py`: a fake `security` (tmp_path) printing a canned dump; grouping,
  hex-encoded attributes, the argv never asks for values, `--commands` lists test items only;
  config copies in tmp_path: old hash and JWT found by setting name, argon2id hash and config.json
  not matched, no value ever in the output.
- Gate: listing names before and after running the five Keychain test files are equal.

## Rollback

`git revert` of the route, test and tool commits. Restored config copies come from the
`~/Documents/CODEC-private` copies; deleted `_test_` items were test leftovers and need no restore.
