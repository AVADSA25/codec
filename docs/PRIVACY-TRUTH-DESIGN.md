# Privacy page, README, demo script and uninstaller say what the code does — design

Status: approved for implementation (plan item P-019, audit 25 Oct 2026; owner Q74; "go go build", 3 Nov 2026).
Depends on P-020 / D-37 (local-first defaults), merged in #490.

## What and why

The local-first promise is sold on these pages, and several sentences on them are false:

- Two privacy files that disagree: root `PRIVACY.md` (linked from README, SECURITY.md, CONTRIBUTING.md) and
  `docs/PRIVACY.md` (GDPR and AI Act text, pinned by `tests/test_privacy_doc.py`).
- `PRIVACY.md` says "every outbound call is to a host you configured" (the update check goes to GitHub, the paid
  app fetches the licence key from AVA, nobody configured either) and "the audit log records every call" (model
  calls, the update check and the licence fetch write no audit line).
- It says Google tokens are "stored in Keychain, never on disk" (they are in `~/.codec/google_token.json`), names
  Keychain items `codec.*` (the code uses `ai.avadigital.codec.*`), points at a `trusted_skill_manifest.json` that
  does not exist, an LM Studio default on port 1234 (the default is `http://localhost:8083/v1`), a Sparkle cache
  folder that does not exist (the update is saved to `~/Downloads`), and a Twilio bridge that is not in the code.
  Its delete steps miss memory.db, clipboard.db, notes, agents, the Google token, config and backups.
- README lines 164, 428 and 691 say "50MB rotation"; the audit log rotates daily and keeps 30 days
  (`codec_audit._rotate_if_needed`, `_RETAIN_DAYS`). README 430 and DEMO_SCRIPT.md beat 14 say "no telemetry" /
  "every LLM call logged". The README "External services" line leaves out GitHub and Google.
- `uninstall_codec.sh` deletes 10 Keychain names, one of them (`license`) never used by any version of the code,
  and misses `vapid_private_key`, `mcp_tokens` (several items under one service) and every `key_slot` the owner
  names (`mimo_api_key`, `delegate_webhook_key`). It leaves `~/.pm2/logs/codec-*` and the app window's
  `~/Library/Caches/ai.avadigital.codec` behind, and its dry run does not say what `~/.codec` holds.

## Changes

1. **One privacy file.** Root `PRIVACY.md` becomes the only real file: storage table, Keychain list, outbound
   calls (automatic ones first: update check, paid-app licence key), what the audit log does and does not record,
   deletion, then the GDPR / AI Act / children sections moved from `docs/PRIVACY.md`. `docs/PRIVACY.md` becomes a
   five-line pointer to `../PRIVACY.md`.
2. **README, DEMO_SCRIPT.md:** lines 164, 428, 430, 691, the "External services" line and beat 14 rewritten to
   match the code (daily rotation, 30 days, 16 filter groups in the Audit view, no analytics, the two automatic
   requests named). Other README text untouched. FEATURES.md is not edited.
3. **Uninstaller (`packaging/macos/uninstall_codec.sh`):**
   - `KC_SERVICES` = exactly the names the code uses (the `KEY_*` constants in `codec_keychain.py`,
     `codec_push.KEYCHAIN_KEY`, `skills/mcp_connect._TOKEN_SERVICE`); `license` is dropped (the licence key lives
     in `config.json`, removed with `~/.codec`).
   - On a real run it also finds every other `ai.avadigital.codec.*` generic password with
     `security dump-keychain` (attributes only, no `-d`, so no secret is read and no prompt is shown) and deletes
     each service until none is left (one `delete-generic-password` removes one item; `mcp_tokens` holds several).
   - With a test `--home` it prints the names a real run would delete and never calls `security`.
   - New steps under `--yes`: `~/.pm2/logs/codec-*` and the app's `Caches`, `WebKit` and `HTTPStorages` folders
     named after the bundle id `ai.avadigital.codec`.
   - The `~/.codec` step lists what is in it (memory.db, clipboard.db, notes, agents, Google token, config,
     backups, ...) with present/absent, then keeps it unless `--purge-data`. Manual steps add `~/codec-projects`,
     `~/codec-workspace`, PM2 apps and Google access. Dry run stays the default.

No code path, config key, audit event or schema changes. No skill edit, so no manifest change.

## Tests

- `tests/test_privacy_doc.py` (existing; now reads root `PRIVACY.md`): the old GDPR / AI Act / contact / processor
  assertions, plus one assertion per claim tied to a code constant: update feed URL (`codec_update.DEFAULT_FEED_URL`)
  and 6-hour interval (`codec_dashboard.html`), licence key URL and 1-hour cache (`codec_license`), Gemini host and
  the `"local"` vision default (`codec_vision`), 12-word search query (`routes/chat.SEARCH_QUERY_MAX_WORDS`) and
  search hosts (`codec_search`), web-search skill phrases (`skills/web_search.SKILL_TRIGGERS`), AVA proxy host and
  default model (`codec_setup`), push hosts (`codec_push`), local default model URL (`codec_config`), Keychain names
  equal to the code's, no audit call in `codec_update` / `codec_license` / `codec_llm`, `web_search_sent` in
  `routes/chat.py`, rotation daily + `_RETAIN_DAYS`, 16 Audit filter groups (`codec_dashboard.html`), retention
  defaults (`codec_backup.RETENTION_DEFAULTS`), each deletion path equal to its module constant, Google token on disk,
  and `docs/PRIVACY.md` being a pointer.
- `tests/test_public_claims_true.py` (existing): README and DEMO_SCRIPT.md no longer say "50MB" or "no telemetry",
  README states daily rotation and the retained days from `_RETAIN_DAYS`, and the "16" in README equals the number
  of Audit filter groups.
- `tests/test_uninstaller.py` (existing): `KC_SERVICES` and the names the dry run prints equal a `git grep` of the
  code's Keychain names; the dry run lists memory.db, clipboard.db, notes, agents, the Google token, config,
  backups, PM2 logs and the Caches folder (each name read from its code constant) and deletes nothing; `--yes`
  removes PM2 logs and the caches but keeps `~/.codec`. All run in a temp `--home`, so `security` is never called.

## Rollback

`git revert` of the PR restores the old docs, tests and script. A real uninstall cannot be undone: it runs only on
the MacBook Air, with the owner's word, after he has copied anything he wants from the Air's `~/.codec`. Never
`--yes` on the Mac Studio (it deletes `audit_hmac_secret`, `internal_token` and the other live secrets).
