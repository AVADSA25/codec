# Vibe agent: long command output kept in a file, older results shortened to a way back (P-043)

Date 2026-11-04. Owner's go for the 25 Oct audit plan: "go go build i trust ur judgment here" (3 Nov 2026). The
engine-only deletion of `.vibe/runs/<run>/` folders at KEEP_RUNS is recorded in `~/.claude/DECISIONS.md` (4 Nov).
Code: `codec_vibe_agent.py`, `codec_vibe_agent_cmd.py` (the collector only, never `profile()`),
`codec_vibe_agent_proto.py`. No page change, no audit change, no new dependency.

## Why

A result the model saw only in part, or saw whole and then lost when it aged out of the prompt, cannot be read
again. Older results shrink to one summary line, and the collector keeps only the first 4 KB and the last 12 KB of
a command's output, so the middle of a long test run is gone. The model then runs the command again or guesses.

## What changes

**The command's whole output goes to a file while it runs.** `codec_vibe_agent_cmd.run` takes `spill=PATH`. The
collector (CODEC's own reader thread, outside the sandbox) writes every byte it reads to that file as it comes, up
to a hard cap of 1 MB (`SPILL_BYTES`, the end line included). Past the cap it only counts, and the file ends with
one line saying how many bytes the command printed in all and that its end is in the result. The file is opened
by the engine with `O_NOFOLLOW`, mode 0600, in `.vibe/runs/<run>/out/<step>.txt` (folders 0700). The sandboxed
command can neither read nor write `.vibe` (`profile()` is unchanged). A write error stops the copy, never the
reading of the output. The head and tail kept for the result are as before.

**When the file is kept.** Only when the model sees part of the result (it is longer than the 6 KB window of
P-040). A short output's file is deleted at once. Once a run keeps 8 MB of such files (`SPILL_RUN_BYTES`) it starts
no new copy, so a run keeps at most 8 MB plus one file; past that a long result is not copied to a file and
read_output falls back to the text the run holds.

**What the model sees.** The P-040 window, unchanged (first 1.5 KB and last 4 KB, so the failing test at the end
stays visible and still ends the result), plus one pointer line put right after the `... (N characters left out)
...` marker, where the text is missing:
`(This result is shown in part. Its text is kept, N lines: @@ read_output 3 FIRST-LAST shows any of them.)`
A long `search` or `list_files` result gets the same line; the run holds its whole text in memory.

**`@@ read_output N [FIRST-LAST]`**, a new tool. It returns numbered lines of result N, at most 300 and at most
what fits the 6 KB window, on whole lines, with the next range named when more follow (the read_file rules). It
reads the kept file when there is one (ANSI codes removed, as in the result), else the result text the run holds.
It gives back `run_check`, `search`, `list_files` and `ask` results only. A `read_file` result is never kept or
given back: the model is told to read the file itself, because an edit needs the file's exact text as it is now.
For `edit_file`, `write_file` and `delete_file` results the same. It takes a number, never a path, so it cannot
reach any other file; the file tools still refuse `.vibe` (`safe_rel`, unchanged).

**Older results.** A result older than the newest six (or shortened to fit the context) was
`[RESULT n tool arg] summary (shortened to save space)`. It now names its way back:
`... (shortened to save space; @@ read_output 3 shows it again)` for the four kinds above,
`... (shortened to save space; @@ read_file style.css 1-40 reads it again)` for a read, and the same call for an
older read_output. Edits, deletes and unreadable replies keep the old line. When the newest result itself must be
cut to fit the context, its cut marker also names read_output when it can give that result back.

**Continue (P-044).** The result event of a kept step carries `out: {"lines": N}`. A continued run rebuilt from the
log reads that step's file from the earlier run's folder, so read_output works across Continue. A rebuilt result
that is longer than the window gets its pointer line again.

**Removal.** `_trim_logs` (at the end of every run) deletes the logs past the newest 20, as before, and now also
every `.vibe/runs/<run>/` folder whose log is gone, unless that run is still going. It never follows a link. Only
the engine writes or deletes there.

## Data and migration

New: `.vibe/runs/<run>/out/<step>.txt` files and the `out` field on some result events. Old logs have neither and
read as before; the page ignores the field. No database, audit or config change. Disk: at most 8 MB plus one file
per run, 20 runs per project.

## Tests

`tests/test_vibe_agent_spill.py` (scripted model, no network; the real sandbox only on this Mac, marked
`vibe-run-test`): the collector streams 300 KB into a 0600 file and caps a 2 MB output at 1 MB with its end line,
and a failing file never stops the reading; the engine keeps the file of a 300 KB output, gives the model the
window plus the pointer, and `@@ read_output 1 100-150` returns exactly those lines; a short output leaves no
file; a long read_file is not kept and read_output refuses it; older results show their way back, also from memory;
a continued run reads the earlier run's file; trimming at KEEP_RUNS removes the out folders of trimmed runs and
keeps the live one; the 8 MB budget; the parser and the prompt size. Existing tests for `safe_rel`, the collector,
the P-040 window and Continue run unchanged.

Live: `python3 tools/vibe_agent_eval.py --bar --baseline <P-041 baseline>` on the local model, tasks 1 to 12 must
not regress, run only when no voice call, image job or other eval is active.

## Rollback

Revert the PR. Kept files live in `.vibe/runs/<run>/out/` and are removed with their run; older code leaves them
until the project is deleted, and ignores the `out` field.
