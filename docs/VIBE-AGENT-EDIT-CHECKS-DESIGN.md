# Vibe agent: a cheap check after every edit, and a check-gated finish (P-045)

Date 2026-11-03. Owner's go for the 25 Oct audit plan: "go go build i trust ur judgment here" (3 Nov 2026). That go
covers the design-first gate for the syntax half. The finish half needs his own word (AGENTS.md section 10:
`allowed_commands`, "a way to run a command without the owner's click"), recorded in DECISIONS.md before it merges,
so it is designed here and NOT built in this PR. The two halves ship as two PRs and either reverts alone.

## Why

The agent accepts its own "done" with no evidence. A local model reading back its own edit is weak evidence; a parse
error with a line number is not. Today an edit that leaves `app.py` without a colon, `package.json` without a comma or
`index.html` with an unclosed `<div>` comes back as "Edited ... (+1 -1)" and nothing else.

## Part 1 (this PR): a syntax note at the end of every edit result

`codec_vibe_agent_check.py` (new, standard library only, no files, no programs, no network) has one entry point,
`check_file(path, text) -> Note | None`. `codec_vibe_agent._t_edit_file` and `_t_write_file` call it on the text they
just saved, and when it returns a note its text goes at the end of the result (inside `[RESULT ...]`, after the
changed region) and a few words go at the end of the step's one-line summary (so a shortened old step still says
it). `delete_file` and every other tool are unchanged. The edit itself is never refused or undone: the file is saved
and checkpointed exactly as today; the note only reports.

| File name ends in | Check | Kind of note |
|---|---|---|
| `.py` | `ast.parse` (SyntaxWarnings silenced; a leading BOM ignored) | firm: line, the parser's message, the line's text |
| `.json` | `json.loads` (a leading BOM ignored) | firm: line, column, message, the line's text |
| `.html`, `.htm` | tag balance with `html.parser.HTMLParser` | firm: up to 3 problems, each with its line |
| `.js`, `.mjs`, `.cjs`, `.css` | bracket count `() [] {}` that skips strings, comments and (JS) likely regex literals | advisory: "a rough count, it can be wrong" |

Rules:

- A file that checks clean gives no note. Any other extension gives no note.
- The note is at most 1 KB (UTF-8 bytes, cut with " ..." if longer). It names the file and the line.
- The note is about the file as the edit left it, whoever broke it first: the brief's "an edit that leaves invalid".
- JSONC: `tsconfig*.json`, `jsconfig*.json`, `.eslintrc.json`, `devcontainer.json` and any `.json` under a
  `.vscode/` folder may hold comments and trailing commas by convention. For those names comments and trailing
  commas are blanked out (line and column kept) before `json.loads`. Every other `.json` is strict, so a trailing
  comma in `package.json` is reported.
- HTML: void elements (`br`, `img`, `input`, `meta`, `link` ...) and elements whose end tag HTML lets you leave out
  (`p`, `li`, `td`, `tr`, `option`, `html`, `head`, `body` ...) are not counted; `<x/>` counts as closed; `script` and
  `style` content is not read as tags (the parser does this). Problems: a tag never closed, an end tag that closes a
  tag opened inside it first ("`<span>` opened at line 5 is not closed before `</div>` at line 9"), an end tag with
  nothing open.
- JS and CSS: the count is advice only and says so in its own words; it can false-alarm on JSX text, nested template
  literals and unusual regex literals. It never blocks anything (nothing in part 1 blocks).
- The Python grammar is the one of the Python that runs the dashboard; the note names that version.
- A check that raises (recursion, a parser bug) gives no note; a step is never broken by its check.

Cost: up to 1 KB per edit in the 10k context window, only when the file fails. A result longer than 6 KB reaches the
model as head and tail (P-040); the tail keeps the last 4 KB whole, so the note always survives the cut.

No new audit event, no new config key, no change to the run log format (the note is part of the result's `output`,
so a continued run (P-044) sees it again), no change to the page (it shows `output` with `textContent`).

## Eval

`tools/vibe_agent_eval.py` keeps `--bar` keyed to tasks 1 to 12 (`BAR_IDS`, `bar_status`, `compare`): checked, not
changed. New task 13, "an edit lands in a file with a syntax error": `app.py` has a missing colon on line 1 and the
goal asks for a new function there, so the agent's edit leaves the file invalid unless it notices first, and the note
names line 1. The check parses `app.py` and runs the project's own tests (outside the sandbox, the owner's side, as
tasks 4 and 8). Task 13 never counts for the bar. The second new task (a failing marked check) belongs to part 2.

Live: `python3 tools/vibe_agent_eval.py --bar --baseline <P-041 baseline> --out <scratch file>` on the local model,
only when no voice call, image job or other eval is running. Pass: no task 1 to 12 that passed in the baseline fails
both tries; task 13's result is recorded.

## Part 2 (next PR, waits for the owner's word): a check-gated finish

The smallest data model that fits the brief:

- `project.json:allowed_commands` entries become `{cmd, network, check}`; `check` is true on at most one entry. Today
  `allowed_commands()` rebuilds each entry as `{cmd, network}`, so it must carry `check` through, and `allow_command`
  must keep the marker of an entry it re-adds.
- `codec_vibe_projects.mark_check(pid, command, on)` sets the marker on that entry and clears it from every other one.
  It is called only by a new route `POST /api/vibe/projects/{id}/agent/commands/check {command, on}` that answers 403
  to anything not from this Mac (like approve, list and forget), and by nothing else: no skill, agent, model reply or
  timer. The page shows a "Check before finish" switch beside each stored command. `forget_command` removes the entry
  and its marker with it. Audit: `vibe_agent_allow` with `action` `check` | `uncheck` (no new event).
- At `finish`, when the project has a marked command, the run was started at this Mac (`can_run`) and the sandbox
  exists, the engine runs that command once through `_run_command` (the same `codec_vibe_agent_cmd.profile`, the
  stored network flag, the same timeout). Exit 0: the run ends `done` and the report gets one line saying the check
  passed. Otherwise the finish is answered with a failed result holding the exit code and output, and the run goes
  on. At most 2 retries: the third failing finish ends the run `limit` with the failure in the report. Stop, the step
  limit and the time limit work as today. A project with no marked command, or a run started away from this Mac,
  finishes as today (the report then says the check did not run, when one is marked).
- Eval task 14, "a failing marked check": a project whose tests fail, with `python3 -m unittest -q` stored and marked
  by the harness the way the owner's click would; pass when the tests pass and the gate ran.

Nothing runs without the marker, and the marker comes only from the owner's click. The sandbox profile is not
widened. Rollback: the owner's Forget removes the marker; reverting part 2 makes the marker an unknown key that
nothing reads.

## Tests (part 1)

- `tests/test_vibe_agent_check.py`: each checker on a broken and a clean sample; the note names file and line and
  stays within 1 KB; JSONC names tolerate comments and trailing commas while `package.json` does not; HTML void and
  optional tags, `<x/>` and `"</div>"` inside a script give no note; JS strings, comments and regex literals with
  brackets give no note; unknown extensions and a check that raises give no note.
- `tests/test_vibe_agent.py`: through a scripted run, an edit that breaks `app.py` reaches the model with the note
  inside its result and in the summary; a clean edit and a clean `write_file` add nothing.
- `tests/test_vibe_agent_eval.py`: task 13 is outside `BAR_IDS`, its check fails on the seeded file and passes on a
  fixed one.

## Rollback

Revert the PR. Nothing is stored: old run logs keep their notes inside `output`, which older code shows as text.
