# Atomic state files and an overlay events trim (P-082)

## What

Five files in `~/.codec` are written in place: a kill or a crash in the middle of
the write leaves half a file.

| File | Writer | Half a file means |
|---|---|---|
| `.auth_sessions.json` | `routes/_shared._save_sessions` | every phone is logged out |
| `.e2e_keys.json` | `routes/_shared._save_e2e_keys` | every encrypted session must start again |
| `google_token.json` | `codec_google_auth._save_token` (dashboard, MCP, voice and bridges all refresh it) | a new Google consent |
| `.license_state.json` | `codec_license._write_grace` | the offline grace window is lost |
| `prompt_overrides.json` | `routes/prompts._save_prompt_overrides` | the owner's prompt edits are lost |

Each now writes through `codec_jsonstore.atomic_write_json`: a temp file in the
same folder (created 0600 by `mkstemp`), `fsync`, `os.replace`. A failed write
leaves the previous file as it was. A folder these writers create is made 0700
(`os.makedirs(..., mode=0o700, exist_ok=True)` before the write); an existing
folder's mode is never changed.

The shift report (`skills/shift_report.py`, codec-observer) already replaced
`notifications.json` atomically, but read and wrote it without the shared lock,
so an agent banner or a heartbeat alert written at the same moment could be lost.
It now goes through `codec_jsonstore.read_modify_write`, which holds the same
`notifications.json.lock` file lock that `routes/_shared._save_notification`,
`codec_agent_messaging` and the heartbeat hold.

`~/.codec/overlay_events.jsonl` is append-only and never trimmed, and the Swift
overlay re-reads the whole file every 0.15 s. The three writers
(`codec_overlays._emit`, `codec_core._swift_text_input`, `codec_dispatch`'s
`skill_fired` line) now create it with `os.open(..., 0o600)`.
`codec_heartbeat.trim_overlay_events()` runs on every heartbeat (codec-dashboard
every 30 min, codec-heartbeat every 20 min when it runs): when the file has not
changed for 60 s and holds more than 100 lines, it is replaced (temp file 0600,
`os.replace`) by its last 100 lines. The Swift poller already resets its offset
when the line count drops, so no Swift rebuild is needed.

## API and data changes

- `codec_jsonstore.atomic_write_json` gains `indent=` (default 2, unchanged for
  every caller). Sessions and E2E keys pass `indent=None`, so the bytes on disk
  are exactly what the old `json.dump(raw, f)` wrote. No phone is logged out and
  a rollback reads the file too.
- Google token, licence state and prompt overrides keep their JSON content; the
  token and prompts were already written with `indent=2`, the licence state
  gains the indentation (its reader is `json.loads`).
- New in `codec_heartbeat`: `OVERLAY_KEEP_LINES = 100`, `OVERLAY_QUIET_S = 60`,
  `trim_overlay_events(path=None, now_ts=None) -> int` (bytes removed, 0 when
  nothing was done; never raises). With no path it trims the writer's own path,
  `codec_overlays._EVENTS`. It reads at most the last 1 MB, so a file of any size
  costs the same. It checks size and time again just before the replace and
  gives up if a writer appended in between.
- No new audit events, no schema change, no new dependency.

## Migration

None. Old files load unchanged. Each file becomes 0600 on its next write; the
overlay events file becomes 0600 on its first trim.

## Test plan (`tests/test_atomic_state_files.py`, all under `tmp_path`)

- For each of the five writers: write once, patch `os.replace` to raise, write
  again: the previous file is intact and parses, and no temp file is left.
- Under `umask 022`: `google_token.json` is 0600; `overlay_events.jsonl` written
  by each of the three writers is 0600; a folder a writer creates is 0700; an
  existing 0755 folder stays 0755.
- A sessions file and an E2E keys file in the old format load, and the new
  writer's bytes equal the old writer's.
- Two threads: a shift report entry and an agent message entry, or a
  `_save_notification` entry, both survive (the shift report is held between its
  read and its write; without the lock the other entry is lost).
- Overlay trim: a 2 MB file with an old time keeps its last 100 lines (0600, by
  replace); one touched 10 s ago is untouched; a short file is untouched.
- `tests/conftest.py` points `codec_overlays._EVENTS` at a temp folder, so the
  tests that run a full heartbeat never trim the owner's file.

## Known limits

- An overlay event appended in the 0.15 s after a trim, before the Swift poller
  has read the shorter file, is skipped: the poller sees fewer lines than its
  offset and resets to the new count without reading the new line. It needs a
  write within that 0.15 s of a trim that only runs after 60 s with no write, so
  it is rare; closing it needs a Swift change (read by byte offset), out of scope
  here.
- A line appended between the last size check and the `os.replace` (a few
  microseconds) goes to the old file and is lost. Writers open, append and close
  at once, so this window is the only one.
- Each process keeps its old writer until it is restarted. `google_token.json`
  is also refreshed by the processes that run Google skills (open-codec, the
  bridges, the agent runner); they write in place until their next restart.

## Rollback

Revert the PR and restart the same services. The files written by the new code
are valid input for the old code (same JSON; sessions and E2E keys byte for byte).
