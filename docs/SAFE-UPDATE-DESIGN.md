# A safe first update: a working smoke check, a rollback that keeps work, a DMG checked from disk

Plan item P-027 (audit 25 Oct 2026; GAP-611, GAP-623, DC-006, DC-066). Idea of a backup before an update
taken from Hermes (MIT), idea only: no code or text.

## Why

- `install.sh --update` and `update.sh` ran `python3 codec_smoke_test.py`. That file is git-ignored
  (`.gitignore`) and on no disk, so the check always failed. `install.sh` then ran
  `git reset --hard <old commit>`: every update undid itself and threw away local edits to tracked files.
  `update.sh` printed a warning and then "Update complete" anyway.
- The real check, `scripts/smoke.py`, crashed: it read `DRAFT_KEYWORDS`, `is_draft` and `DRAFT_TASK_FILE` out of
  `codec.py` with regexes, and they moved to `codec_core.py` and `codec_config.py`. Its wake-word regex no longer
  matched either.
- Both scripts deployed skills and restarted PM2 before the check, so a bad commit was already running when the
  check failed.
- The paid app's updater (`codec_update.download_and_verify`) read the whole DMG into memory (`r.read()`), then
  wrote it out: two copies of the file in memory at once.
- Nothing kept a copy of `~/.codec` before an update that may migrate its databases or settings.

## What

**`scripts/smoke.py` (repaired, still a script, not a pytest module).** Every section runs inside a guard, so a
failure is one red line and the script carries on; any failed check exits 1, all passing exits 0. Import checks:
`codec_overlays`, `codec_watcher`, `codec_identity`, `codec_core`, `codec_config`. `is_draft` is the real
`codec_core.is_draft`, the draft-file check compares `codec_config.DRAFT_TASK_FILE` with
`codec_watcher.TASK_FILE`, and the wake-word check runs `codec.py`'s own `_is_wake_utterance` (taken out of the
source with `ast`, because importing `codec.py` starts the voice loop) on a mixed-case phrase. The name
`codec_smoke_test.py` stays git-ignored and is not recreated.

**`install.sh --update` and `update.sh`, same order:**
1. Record `ROLLBACK_COMMIT` (`git rev-parse HEAD`) and `git pull --ff-only`. Pull failure: exit 1, nothing changed.
2. When the pull moved HEAD, `python3 codec_preupdate.py --src <state>` copies the state (below). Failure: roll
   back, exit 1.
3. `install.sh` only: update the venv packages (as before).
4. `scripts/smoke.py`. Failure: `git reset --keep "$ROLLBACK_COMMIT"` (as `scripts/auto_pull.sh` does), print
   the commit, exit 1. `--keep` keeps uncommitted work and refuses, rather than overwrite, a local edit to a file
   the update changed; then the script says so and exits 2 with the checkout left at the new commit. Never
   `reset --hard`.
5. Only after a passing smoke: `deploy_skills.sh`, `sync_to_pm2.sh`, then "Update complete".

`update.sh` keeps `python3.13` when it exists, otherwise `python3`; `CODEC_PY` overrides it. Its body is one
`main` function, so `git pull` rewriting `update.sh` mid-run cannot change what the running copy does
(`install.sh`'s update branch is one `if` block, which bash reads whole). Both read the state folder from
`CODEC_STATE` (default `~/.codec`), the name `scripts/auto_pull.sh` already uses. `sync_to_pm2.sh`'s closing
hint points at `scripts/smoke.py` instead of the missing file.

**`codec_preupdate.py` (new, standard library plus `codec_backup`'s checks).** `snapshot(src, dest_root, label)`
writes `~/.codec/pre_update/<YYYYmmdd-HHMMSS>/`:
- every top-level `*.db` that is a SQLite file, copied with the sqlite backup API while CODEC runs (the same
  steps as `codec_backup.backup_sqlite`: hidden temp file, `journal_mode=DELETE`, `PRAGMA quick_check` must say
  `ok`); a `*.db` file that is not SQLite is listed as skipped;
- `state.tar.gz` with `codec_backup.STATE_ITEMS` (agents, config.json, notes, standing rules, schedules, voice 02,
  plugins.allowlist), every member read back;
- `manifest.json`: time, label, each copy's size, check and sha256.
The work happens in a hidden `.partial` folder renamed into place at the end, so a failed snapshot leaves no
folder that looks complete. Folders 0700, files 0600 (the copy holds secrets). `codec_backup`'s rotation only
reads `backups/`, so these folders are never pruned: CODEC never deletes a backup on its own, and each run prints
where the copy went and that old ones can be deleted by hand. No `~/.codec`: nothing to copy, success.
`tools/codec_restore.py check|restore --to` reads the copies as they are.

**DMG path (`codec_update.download_and_verify`).** The signature's presence is checked before downloading. The
download streams in 1 MiB pieces into a hidden `.partial` file in the destination folder (0600 from
`mkstemp`); it stops as soon as it passes the appcast's length (4 GiB when the appcast gives none). The Ed25519
signature is checked by the same `verify_ed25519` (the `cryptography` package, no own crypto) over an `mmap` of
that file, so the file is never read into memory. Then `snapshot()` runs (label `app <version>`), and only then
is the file renamed to its final name. A bad signature, a wrong length or a failed snapshot removes the
`.partial` file and leaves nothing at the final path. A failed snapshot raises `RuntimeError` (the route answers
500 with its text); the signature errors stay `ValueError` (400). The public key, feed URL and the
`~/Downloads` default are unchanged. `routes/update.py` is not changed (its event-loop wrap belongs to P-016).

## Data

New: `~/.codec/pre_update/` (one folder per update). No schema change, no audit event, no config key.

## Tests

- `tests/test_update.py`: a fixture appcast and a fixture DMG (random bytes, signed with a throwaway key) served
  from a `file://` URL, `dest_dir` and the state folders in `tmp_path`: a good file lands with a snapshot; a bad
  signature, a wrong length and a failed snapshot leave no file at the final path and no `.partial`. On macOS a
  child process downloads a 150 MB fixture and `/usr/bin/time -l` must report a peak memory footprint under
  100 MB (the maximum resident size is not used: the mapped file's clean pages count there).
- `tests/test_preupdate.py`: copies of a WAL database with fresh rows, 0600/0700, skipped non-SQLite file,
  missing source, a failed copy leaves no snapshot folder, the CLI.
- `tests/test_update_scripts.py`: a throwaway clone of a local bare fixture repo, `pm2`, `deploy_skills.sh` and
  `sync_to_pm2.sh` stubbed: passing smoke ends "Update complete" at the new commit with a snapshot; failing smoke
  returns to the recorded commit, keeps an untracked file and a local edit, deploys nothing, exits non-zero and
  `update.sh` prints no banner. `scripts/smoke.py` exits 0 on fixture modules and 1 when one import breaks; it
  runs on the real checkout only where no `~/.codec` exists (CI), never against the owner's state.

## Rollback

Revert the PR. The only new state is `~/.codec/pre_update/`, which can be deleted by hand. Nothing is
restarted by this change; `codec_update.py` is read by `codec-dashboard` when an update is downloaded, so the
DMG part applies after the owner restarts it.
