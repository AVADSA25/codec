# Licence key in the Keychain (R-03)

Status: approved by the owner on 12 Nov 2026, decision board card R-03: "Move it to the Keychain with the same first-run migration as the others (read Keychain, else config then blank it)." Closes the known issue of 27 Sep 2026 (`ava.license_key` stored as plain text in `config.json`).

## What and why

`~/.codec/config.json:ava.license_key` holds the paid app's licence key (a signed JWT). PR-2B and PR-2B-2 moved every other secret to the Keychain; this one stayed on disk, in the file that is backed up (`~/.codec/backups/`, `pre_update/`) and pasted into support threads. It is also the bearer the AVA proxy accepts.

It moves to the Keychain slot `ai.avadigital.codec.license_key` (`codec_keychain.KEY_LICENSE_KEY`), with the same first-read migration as the PR-2B-2 getters.

## Change

`codec_config.get_license_key(conf=None, path=None)`:

1. If the config the caller just read (`conf`, default this module's `cfg`) holds a non-empty `ava.license_key`, and the file it came from (`path`, default `CONFIG_PATH`) still holds that same value: store it in the Keychain, then blank that one field in that file with `update_config` (locked read-modify-write; every other key of the `ava` block and of the file is kept) and in `conf`. The value is returned.
   - Keychain empty: `codec_keychain.migrate_from_plaintext` (the PR-2B helper, one `keychain_migration` audit line).
   - Keychain already holds the same value (an old snapshot writer put it back): only the blank.
   - Keychain holds a different value: the config value is newer (the installer writes `ava.license_key` on every run, for example a reinstall with a new key), so it replaces the stored one (`keychain_set`), then the blank.
   - Keychain locked or the write failed: nothing is blanked and the plain value is returned, so CODEC keeps working (same as the other getters).
   - A dict that is not what the file holds (a test's dict, or a copy read before another process moved the key) is used as given and nothing is stored or written.
2. Otherwise the Keychain, through the 30 s cache (`_cached_secret("license_key")`).
3. Otherwise the env var `CODEC_LICENSE_KEY` (the other getters have one). Never stored.

Why `conf` and `path` and not only the module's `cfg`: every reader today reads the file fresh (`codec_license`, `codec_ava_client`, `codec_setup`, `codec_slash_commands`, each with its own `CONFIG_PATH`). The module's `cfg` is read once at import, so a key the installer writes while CODEC runs would be missed, and tests that point a module's `CONFIG_PATH` at `tmp_path` would read the owner's live file through `codec_config`. Passing the caller's own config and path keeps both right. `get_license_key()` with no argument still works like `get_telegram_bot_token()`.

Kill switch `LICENSE_KEY_MIGRATION_ENABLED=false`: nothing is moved or blanked; a plain value is returned as before, else the Keychain, else the env var. `tests/conftest.py` sets it to `false` for the whole suite, because some tests (`/version`, licence checks through skill runs) read the owner's live `config.json`; with migration on, such a test would copy the live key into the test Keychain and blank the live file. The tests of this change turn it on and use `tmp_path` files only.

Readers:

| Module | Before | After |
|---|---|---|
| `codec_license._license_token` | `license_token` or `ava.license_token` or `ava.license_key` | the same two legacy names first (unchanged; nothing writes them), then `get_license_key(cfg, CONFIG_PATH)` |
| `codec_ava_client.load_config` | `ava.get("license_key")` | `get_license_key(data, CONFIG_PATH)` |
| `codec_setup.set_provider("ava")`, `status()` | `ava.get("license_key")` | `get_license_key(cfg, CONFIG_PATH)`; `set_provider` saves the blanked dict, so it never writes the key back |
| `codec_slash_commands` `/version` | `ava.get("license_key")` | `get_license_key(cfg, CONFIG_PATH)` |
| `codec_slash_commands` `/who` | printed the first 16 characters as "License email" | line removed (it was the JWT header, not an e-mail) |
| `codec_ava_client` smoke test (`python codec_ava_client.py`) | printed 30 characters | prints `set` or `missing` |

Writers: CODEC itself never wrote the key. The buyer's installer (`ava-stack/installer-gui`, `SetupView.writeCodecConfig`) writes `ava.license_key` into `config.json`; that file is the inbox, and the first read moves the key (point 1). No change is needed there for the app to activate.

## Buyer app

The app runs the same modules from `Resources/app` under launchd and already keeps its other secrets in the Keychain through `/usr/bin/security`. First launch after install: the first licence check, setup status or proxy call moves the key and blanks the field. Reinstall with a new key: the new key replaces the stored one on the next read. The uninstaller (`packaging/macos/uninstall_codec.sh`) deletes `ai.avadigital.codec.license_key` with the other Keychain items (`KC_SERVICES`, checked against `codec_keychain.KEY_*` by `tests/test_uninstaller.py`).

## Deploy

On deploy the live config's key moves to the Keychain on the first read by each CODEC process that reads it (licence check, setup screen, AVA proxy call, `/version`). The first one blanks the field; the others then read the Keychain. No manual step. Processes that are not restarted keep running the old code and keep reading the field, which is blank once a new process has moved it: restart the CODEC processes together (`codec-dashboard`, `open-codec`, `codec-mcp-http` and the rest), as with any deploy.

## Tests

`tests/test_license_key_keychain.py`, modelled on `tests/test_remaining_secrets.py`: fallback Keychain store and a per-test service prefix under `tmp_path`, config files under `tmp_path`, `LICENSE_KEY_MIGRATION_ENABLED` turned on per test. Never the real Keychain, never a fake HOME.

- first read migrates: Keychain set, field blanked on disk and in the dict, siblings (`enabled`, `proxy_url`, other top-level keys) kept, one `keychain_migration` line;
- Keychain read when the field is blank; 30 s cache;
- env fallback, not stored;
- an injected dict whose value the file does not hold stores nothing and writes nothing;
- a newer key in the file replaces the stored one; an equal one is only blanked;
- Keychain write fails: the field is kept and the value returned;
- kill switch off: nothing moves;
- the four readers go through the getter (`codec_license`, `codec_ava_client`, `codec_setup`, `/version`), and `/who` prints no part of the key.

## Rollback

Revert the PR. The reverted code reads only `config.json`, where the field is now blank, so first put the key back: `security find-generic-password -s ai.avadigital.codec.license_key -w` gives it; write it into `ava.license_key`, then restart. Setting `LICENSE_KEY_MIGRATION_ENABLED=false` stops further moves but still reads the Keychain, so it needs no copy back.
