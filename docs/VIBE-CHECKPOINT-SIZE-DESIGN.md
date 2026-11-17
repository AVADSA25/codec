# Vibe checkpoints: keep each line small in big folders (design note, not built)

Status: design only. The `.vibe/` record format is a don't-touch zone (AGENTS.md section 10), so
building this waits for the owner's word.

## What is wrong

Every line of `.vibe/checkpoints.jsonl` holds the whole file map of the project (`"files"`: path to
SHA-256), written by `codec_vibe_projects._checkpoint` and read back whole by `_load_checkpoints`.
That was fine for created projects (200 files at most). An attached folder may hold 2,000 files
(P-050, docs/VIBE-ATTACH-FOLDER-DESIGN.md "Known limits").

Measured on the owner's test folder `~/Projects/vibe-attach-test` (1,217 files, 17 Nov 2026): one
line is about 119 KB. At the 2,000-file limit, with longer paths, a line is 200-300 KB.

- An agent run takes a checkpoint after every file change, so a 30-step run writes 3-9 MB.
- `_load_checkpoints` is called from 25 places (every checkpoint, list, diff, restore and agent
  step). Each call reads and parses the whole file. The work grows with the square of the
  number of checkpoints.
- Nothing prunes the file. Deleting lines would break restore.

Created projects are not affected in practice. Their maps are small.

## Proposal

Store the file map once, as an object, and point to it from the line.

1. `_checkpoint` writes the map as canonical JSON (`sort_keys`, no spaces) into `objects/<sha256>`,
   through the existing `_store_blob`. The line holds `"files_ref": "<sha256>"` and
   `"files_count": N`, and no `"files"`. Two checkpoints with the same map share one object.
2. A new reader `_files(pdir, rec)` returns `rec["files"]` when the line has one (every line
   written today) and otherwise loads and checks the referenced object. A small cache keyed by
   the SHA (the last 8 maps) avoids reading the same object twice during one request.
3. `_load_checkpoints` accepts a line with either `files` (dict) or `files_ref` (64 hex). Every
   caller that reads `rec["files"]` goes through `_files()` instead. A test fails if a new caller
   reads the key directly.
4. `_public` uses the stored `files_count`.
5. The newest line is also kept in memory per project, keyed by file size and mtime, so taking a
   checkpoint no longer parses the whole history to find its parent.
6. Only attached folders get the new lines at first (`files_ref` when the record is an attached
   folder). Created projects keep writing `files` byte for byte, so their fixture
   (`tests/fixtures/vibe_created_project_before_attach.json`) is unchanged. Switching created
   projects too can be a later step.

A line becomes about 400 bytes. A 30-step run then writes about 12 KB of lines plus one map
object per changed state. Maps that only differ by the changed file still each cost one object,
about the same size as the old line, so the disk total is about the same at first. The gain is
in reading: listing, diffs and agent steps parse small lines and load at most two maps.

To cut the disk total too, a later step can store a map as a delta from its parent map
(`"base": <sha>, "set": {...}, "del": [...]`), with a full map every 50 checkpoints. That is
more code and more ways to fail, so it is left out of the first build.

## Rollback

Code from before this change reads `files` only. It skips the new lines (`isinstance(rec.get("files"), dict)`
fails), so after a rollback the newer checkpoints of attached folders are hidden from the
Versions drawer and from restore, and the next checkpoint's parent is the last old-style line.
No file in the folder and no stored object is lost. Rolling forward again shows them. A
`tools/` script could rewrite `files_ref` lines back to full `files` lines before a rollback,
if that ever matters.

## Tests

- An attached folder with 2,000 small files: one checkpoint line stays under 1 KB. Listing the
  history parses no map object.
- Restore, undo of the AI's files, rewind, diff and copy-to-new-project give the same results
  on a history mixing old `files` lines and new `files_ref` lines.
- A missing or damaged map object gives the same 500 "A stored file version is missing" as a
  missing file object.
- Created projects: lines are byte for byte as before (the existing fixture test).
- A test reads `codec_vibe_projects.py` and fails on any `rec["files"]` / `["files"]` read
  outside `_files()`.

## Open question for the owner

Build it? It changes how `.vibe/` history is stored for attached folders only. The recommendation
is yes, with points 1-6 and without the delta step.
