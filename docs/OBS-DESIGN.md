# OBS — Observer disk file made safe

Item 1 of docs/UI-PHASE2-3-PLAN.md. Owner decision 2026-09-29: keep the file, make it safe.

## What

The observer daemon (`codec_observer.py`, PM2 `codec-observer`) mirrors its ring buffer to
`~/.codec/observer_buffer.json` after every poll, so skills in other processes can answer
"what was I doing?" (`skills/observer_recall.py`). Today that file:

- is written with the default umask (0644 on this Mac) through a fixed `.tmp` name;
- holds the first 200 characters of every clipboard change;
- is never removed: it survives the kill switch, the long-idle reset and a service stop;
- contradicts AGENTS.md and docs/PRIVACY.md, which say the observer is "RAM only".

After this change:

1. **Owner-only, atomic.** The mirror is written through `codec_jsonstore.atomic_write_json`
   (unique same-dir tmp from `mkstemp`, which is 0600 from creation, fsync, `os.replace`, chmod 0600).
   A pre-existing 0644 file is replaced by a 0600 one on the first write.
2. **No clipboard text.** Each entry's `clipboard` block is reduced to
   `{"content_type": ..., "length": ...}` on disk. `poll()` now records the clipboard length in the
   RAM snapshot so the disk form can carry it. RAM keeps the 200-character preview, because the
   local-model injection summary uses it; that path is unchanged.
3. **Wiped on pause, long idle and shutdown.** New `_wipe_disk_buffer()` deletes the mirror (and a
   legacy fixed-name `observer_buffer.json.tmp`, if one was left by the old writer). It runs:
   - when the daemon is paused by the kill switch (`OBSERVER_ENABLED=false`), once per pause, together
     with clearing the RAM buffer;
   - when the long-idle reset clears the RAM buffer (`reset_on_long_idle`);
   - in the SIGTERM / SIGINT / atexit cleanup, so a service stop or restart leaves no copy behind.
     A restart already discarded the RAM buffer, and the first poll after a restart overwrote the file,
     so this removes nothing that recall could still use.
   P3.7 adds the timed pause flag (`~/.codec/observer_paused_until`) and reuses `_wipe_disk_buffer()`.
4. **Privacy text corrected** in AGENTS.md (§3 Continuous Observation Loop), docs/PRIVACY.md (§1 table)
   and the `codec_observer.py` module docstring. Historical design docs (PHASE2-BLUEPRINT,
   PHASE2-STEP5-DESIGN, PHASE2-COMPLETE) keep their original text.

Window titles, screen text (OCR) and recent file paths stay in the file: `observer_recall` needs them,
and the owner's decision covers only clipboard text.

## Reading of "pause"

Today the only pause is the kill switch (`OBSERVER_ENABLED=false`), and stopping the service. Both wipe.
The user-facing pause menu is P3.7.

## API or schema change

- On-disk entry: `clipboard` becomes `{"content_type": str, "length": int}` or `null`; no `preview` key.
- RAM snapshot: `clipboard` gains `length` (additive). Audit events are unchanged.
- No config key, endpoint or dependency added.

## Test plan

tests/test_observer.py, new section "Disk mirror (OBS)":
- the mirror is 0600 even when a 0644 file was there before;
- a poll with clipboard text, then a persist: the file holds the type and the length, never the text;
- a paused daemon iteration deletes the mirror and clears RAM;
- the long-idle reset deletes the mirror;
- the shutdown cleanup registered with `codec_lifecycle` deletes the mirror.

tests/test_observer_recall.py: the round-trip test writes an entry with a clipboard block through the
safe persist and `observer_recall.run("what was I doing?")` still names the app.

Negative control: the new tests are run against the old `codec_observer.py` and must fail.

Live check after deploy: `~/.codec/observer_buffer.json` is 0600 with no `preview` key, and
`observer_recall.run("what was I doing?")` on the live file names an app.

## Rollback

Revert the PR and restart codec-observer. The old writer overwrites the file on its next poll (0644 again,
with clipboard text).
