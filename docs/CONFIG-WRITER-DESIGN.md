# One safe writer for config.json (P-054)

Owner's go, 3 Nov 2026: "go go build i trust ur judgment here" (the 25 Oct audit plan, item P-054).
Status: built in the PR that carries this file; live after the owner restarts `codec-dashboard`.

## What and why
Six places in the dashboard routes wrote `~/.codec/config.json` with a plain `open(CONFIG_PATH, "w")`:
the Settings save and the Advanced editor (`routes/config.py`), the heartbeat settings and alerts
(`routes/heartbeat.py`) and the three TOTP switches (`routes/auth.py`). Each one read the file first and,
when that read failed, carried on with `{}`. So a save made while the file was half written, empty or
unparseable replaced the whole config with a few keys: model, voice, keys and login settings gone,
silently. The plain write also truncates the file first, so a reader in another process can catch it
empty, and two saves at the same moment can lose each other's keys.

`codec_config._write_config_atomic` (the version stamp written by `load_config`) used one fixed name,
`config.json.tmp`, so two writers using that name at once could swap in each other's half-written file.

## Change
- **`codec_config.update_config(mutate_fn, path=None)`** (new). Wraps
  `codec_jsonstore.read_modify_write`: it holds the `config.json.lock` file lock across the read, the
  change and the atomic write (unique temporary file, fsync, replace, 0600). `mutate_fn(cfg)` changes
  the dict in place; what it returns, `update_config` returns. If `mutate_fn` raises, nothing is
  written. `path` defaults to `codec_config.CONFIG_PATH`; each route passes its own module's
  `CONFIG_PATH`, so tests point every route at a temporary file.
- **Refusal.** When the file exists but cannot be read, does not parse (including bytes that are not
  UTF-8), is not a JSON object, or is an empty object, `update_config` raises
  `codec_config.ConfigUnreadable` before calling `mutate_fn`, and the file keeps its exact bytes. A
  missing file starts from `{}` (first run). An empty `{}` does not last on a working Mac: the first
  `load_config` turns it into `{"config_version": 1}`.
- **Routes.** `PUT /api/config` and `PUT /api/config/raw` do their login-key check, validation and merge
  inside `mutate_fn`, against the file as it is under the lock; a refusal (403) or a validation error
  (422) raises out of `mutate_fn`, so nothing is written, with the same status and body as before. Both
  heartbeat saves and the TOTP confirm, disable and enable writes go through `update_config` too. An
  unreadable file answers **409** with the message "config.json could not be read (it is not valid
  JSON, or it is empty), so nothing was saved. Fix or restore the file, then save again."; the TOTP
  confirm and disable routes keep their existing 500 "Failed to ... config: <message>" shape. The pages
  already show `error` from the answer, so no page change.
- **`_write_config_atomic`** now writes through `codec_jsonstore.atomic_write_json` (unique temporary
  name, fsync). Its return value and warning are unchanged.
- `_upgrade_pin_hash` in `routes/auth.py` already used `read_modify_write` with its own empty-config
  refusal; it is left as it is.

## Not changed (other config.json writers, for later items)
- No lock: `codec_models.py` model-switch writers (`_write_active`, `_enter_cloud`, `_leave_cloud`,
  `_set_auto_flag`; AGENTS.md section 10), `codec_setup._save_config`, `codec_config.load_config`'s
  version stamp, `codec_config._blank_config_field` and `_blank_nested_config_field` (still the fixed
  `config.json.tmp`; on an unparseable file they write one blanked key over it),
  `codec_hue_discovery._atomic_write` (fixed `.tmp`), and the installers `setup_codec.py` and
  `setup_hue.py`.
- Locked, but a corrupt file reads as `{}` and is replaced: `codec_skill_switches.set_on`,
  `routes/memory_page.py` (fact_extract, retention), `routes/usage.py` (`llm_auto_fallback`),
  `codec_checkin.save_settings`.
- `skills/mcp_connect.py` writes `mcp_servers.json`, not config.json.

Moving each of these onto `update_config` is a one-function change per writer.

## Data, API, migration
No schema change, no new key, no new audit event. The only new file is the existing
`config.json.lock` sidecar, which `codec_skill_switches`, `routes/memory_page.py` and the PIN rehash
already create. Answers change only for an unreadable file (409 instead of a silent overwrite).

## Test plan
`tests/test_config_writer.py`, every test on a `tmp_path` file with each module's `CONFIG_PATH`
monkeypatched (never the real file, never a fake HOME):
- `update_config`: missing file created 0600; other keys kept; invalid JSON, empty file, `{}`, a JSON
  list and non-UTF-8 bytes raise `ConfigUnreadable` with the bytes unchanged and `mutate_fn` not
  called; a raising `mutate_fn` writes nothing.
- `_write_config_atomic` leaves a `config.json.tmp` that belongs to someone else untouched.
- With invalid JSON on disk: Settings save, Advanced save, both heartbeat saves and TOTP confirm return
  an error status and the file bytes are identical; TOTP enable and disable keep the other keys.
- Two threads: a Settings save held inside the lock while a heartbeat save starts; both keys survive.
- Existing route, auth and config tests still pass.

## Rollback
Revert the PR and restart `codec-dashboard`. Nothing on disk needs undoing: the config format is the
same and the lock sidecar is harmless.
