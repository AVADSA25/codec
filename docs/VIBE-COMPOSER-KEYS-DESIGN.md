# Vibe composer: commands, @file, keys, a project link, Changes, and the run card's views (P-069)

Date 2026-11-03. Owner's go for the 25 Oct audit plan: "go go build i trust ur judgment here" (3 Nov 2026).
Page work on the live Vibe page, plus one opt-in read of the run log (below).

## Why

The composer has one key handler: Enter always sends (a phone cannot type a new line, an IME's confirm key sends),
and there are no commands, no file mentions and no way to link to a project. Hand edits made since the last version
are not shown anywhere. A run card cannot be made shorter, does not say when a step ran, and the model's own replies,
kept in the run log since P-040, cannot be looked at. Two small defects sit in the same code: the "Keep" row after a
run never hides (`.agc-ea{display:flex}` beats `[hidden]`), and a cloud run starts without its dialog when the shell's
dialog is missing (`ask()` answers yes).

## What changes

**Commands** (our own list). A message that is exactly `/word` or `/word text` and names a command below runs it on the
page and is not sent; any other `/word` is sent as text. `/help` lists only the commands whose feature exists here.

| Command | Call | Listed when |
|---|---|---|
| `/help` | a line in the chat with this list | always |
| `/stop` | stops the agent, else the answer being written (`doStop`) | always |
| `/undo` | `VibeProj.undo()` (Undo AI change); refused while the agent works | projects are on |
| `/clear` | `nS()`, the New Project call; the current chat stays in Projects | always |
| `/agent`, `/chat` | the mode switch; text after the word stays in the box | the agent exists |
| `/model local`, `/model cloud` | selects the model list's entry; nothing is sent | a cloud model is set up |

`/clear` is New Project, not "empty this chat": emptying in place would delete the stored chat at the next save, and a
second chat on one project would let deleting one chat move the other's folder to the trash. `/model` only selects:
the run and chat dialogs still ask before anything goes to the cloud. While a chat answer is being written only
`/stop` runs. `/plan`, `/compact` and `/init` are not listed and are sent as text until their features exist.

**@file.** Typing `@` at the start of the box or after a space, in a project, opens a list of that project's text
files (`VibeProj.paths()`), filtered by what follows the `@`. Arrows move, Enter or Tab picks, Esc closes (and does
not stop a run). Picking replaces `@query` with the path; only a path from the open project's list is inserted.

**Keys.** One decision function, `composerKey(e, {touch, mention, running})`: nothing while an IME composes
(`isComposing` or key code 229); Cmd/Ctrl+Enter sends; Enter sends on a desktop and inserts a new line on a touch
device (`(hover: none) and (pointer: coarse)`), where the send button sends; Shift+Enter is a new line; Esc stops a
working agent (P-044's listener, unchanged).

**Project link.** `/vibe?project=<id>` opens that project: its newest chat on this Mac, else the project alone. The
id must match the server's project id pattern (`^[a-z0-9][a-z0-9-]{2,60}$`), otherwise the link is ignored and no
request names it. A link wins over P-070's live-run reopen (it takes a fresh chat id, which makes that reopen stand
down).

**After a run**, on this Mac, two rule-built chips: "Run the checks" (shown when `package.json` has a real test script
or the project has `test_*.py` / `*_test.py` files; `npm test` or `python3 -m pytest -q`) and "Show it on localhost"
(shown when the project has a page). Each opens the existing Run dialog (`VibeProj.runDialog`) on its option with the
command filled in; the owner reads it and presses Start, as with the play button.

**Versions drawer: Changes.** A first entry diffs the files as they are now against the newest version
(`GET /diff?from=<newest>&to=working`, existing route), says how many files differ, opens a file from its name, and is
read again after each autosave while the drawer is open.

**Selection.** When the editor has a selection in a project file, an "Add selection" button above the box appends
`[SELECTION path:12-18]`, the whole selected lines (at most 3,000 characters; the header names the lines kept) and
`[END SELECTION]` to the message.

**Run card.** Each step row's tooltip gives its start time (and end time once its result is in). A "Compact" switch
(remembered in this browser) collapses every step to one line. A "Raw replies" switch, off at every load, shows the
model's reply under each step. The live line gets `aria-live="polite"`. `.agc-ea[hidden]{display:none}` fixes Keep.
`confirmCloud()` now refuses when the shell's dialog is missing, and the run request is built by
`runRequest(goal, model, confirmed, history)`, which sets `confirm_cloud` only for the cloud model and an explicit yes.

## API and data

One opt-in parameter: `GET /api/vibe/projects/{pid}/agent/{run}?replies=N` adds `"replies": [...]`, the run log's reply
events for steps after N (`step`, `t`, `chars`, `cut`, `redacted`, `text` as P-040 stores them: redacted, at most
8,000 characters). Without the parameter the view is exactly as before and never carries a reply. The engine gets
one read-only function, `codec_vibe_agent.run_replies(pid, run_id, after)`. No database, no file format, no audit
event changes. Every text from a run or a file goes on the page with `textContent`.

## Tests

`tests/test_vibe_composer.py`, Node on the plain functions of `static/vibe-agent.js`: each command calls the right
function (spies), `/help` lists only commands whose feature exists, unknown and malformed `/words` are text, `/model`
selects without sending and a request built after `/model cloud` without the dialog's yes has no `confirm_cloud`;
mention find, filter and insert (a path outside the project is refused); the key table (IME, Cmd+Enter, touch, Esc,
list keys); the link id check; the chip rules; the selection block; the step time. `tests/test_vibe_agent_routes.py`:
the default view has no replies, `replies=N` returns the redacted replies after step N. Headless Chrome on a temp
profile against a scratch server with the real routes and the dashboard's CSP: the link opens the project and a bad
id is ignored; the two chips open the Run dialog; Changes shows a hand edit after the autosave; a selection is added
as a block; a step's tooltip has its time; Compact collapses the rows; raw replies appear only when switched on; the
live line is polite; the console read is empty.

## Method

Behaviour taken from Claude Code's public docs on interactive mode and commands (no code, no licence granted); the
command names are our own list and the code is our own. code-muse MUSE-02, named in the gap row, is not a source.

## Rollback

Revert the PR. Nothing is stored except the Compact choice (`vibeAgentCompact` in the browser's localStorage).
