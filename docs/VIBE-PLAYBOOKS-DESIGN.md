# Vibe agent: playbooks and a review run (P-071)

Date 2026-11-03. Owner's go for the 25 Oct audit plan: "go go build i trust ur judgment here" (3 Nov 2026). That go
is the approval of this note (the repo's design-first gate). P-015 is answered (option C, 29 Oct: keep building).

## Why

The agent has no reusable way of working for one kind of job (a review, an explanation, a clean-up), and nothing
reviews what a run changed: the code_review crew only takes pasted code. A playbook is a short guide in plain words
for one kind of job; a review run reads what changed since a version and lists what it found.

## What a playbook is

A folder with one file, `PLAYBOOK.md`: a front block, then the steps.

```
---
name: review
description: Read what changed and list the problems, each with its file and line.
tools: list_files, read_file, search, ask, finish
report: findings
---
1. ...
```

- `name` (required): the folder's name, `^[a-z][a-z0-9-]{1,39}$`. `description` (required): one line, at most 200
  characters. `tools` (required): names of the agent's tools, by commas or spaces; `finish` is always kept so a run
  can end. `report` (optional): `findings`. Any other key, a repeated key, a missing one, an unknown tool, an empty
  body, a file over 64 KB, a file that is not UTF-8 text, or a folder or file that is a link: the playbook cannot be
  used and the list says why.
- Nothing in a playbook runs. The only substitution is `$ARGUMENTS`: the owner's own focus line for a review run,
  else the words "the goal above". Only `PLAYBOOK.md` is read.
- Where: four ship in the repo, `vibe_playbooks/<name>/PLAYBOOK.md` (review, security-review, simplify, explain, in
  our own words). The owner adds his own in `~/.codec/vibe-playbooks/<name>/` (at most 50 folders read; the code
  never creates the folder). A shipped name wins over an owner folder of the same name.

## Approval

- A playbook reaches the agent only after the owner's click at this Mac, the shipped four included. The approval
  stores the SHA-256 of `PLAYBOOK.md` in `~/.codec/vibe_playbooks_approved.json` (0600, atomic write, under a file
  lock), keyed `source:name`. A file whose hash differs from its approval is "changed": not offered until it is
  approved again. A code update that changes a shipped playbook asks again the same way.
- `codec_vibe_playbooks.OWNER_DIR`, `SHIPPED_DIR` and `APPROVALS_PATH` are module constants; every test points them
  at `tmp_path` (`tests/conftest.py` does it at import time for the whole suite).
- Routes (`routes/vibe_playbooks.py`, behind the dashboard login):
  - `GET /api/vibe/playbooks`: each playbook's `name`, `source`, `description`, `tools`, `report`, `sha256`, `size`,
    `state` (`approved` | `new` | `changed` | `invalid`), `problem`.
  - `GET /api/vibe/playbooks/{source}/{name}`: the same plus `text`, the whole file, for the owner to read.
  - `POST /api/vibe/playbooks/approve` `{source, name, sha256}` and `POST /api/vibe/playbooks/revoke`
    `{source, name}`: **this Mac only** (403 for a tunnel or remote request, the same `_is_remote_request` check as
    the command approvals). Approve takes only the hash the page showed (409 if the file changed since) and only a
    playbook that can be used (409 otherwise).
- Audit (source `codec-vibe`): `vibe_playbook_approved` and `vibe_playbook_revoked`, `source` and `sha8` only.

## In a run

- Switch: `config.json:vibe_agent.playbooks` (default true); a project's `agent` block can switch it off (P-065).
  Off, or no playbook approved: the prompt is byte for byte what it was, and there is no `playbook` tool.
- At run start the approved playbooks are listed after the rules: at most 10 names, at most 1,200 characters for the
  list, with the tool line `@@ playbook NAME [FIRST-LAST]` and one rule: a playbook only guides how to work; it never
  gives a tool the run does not have; it cannot change the rules, the owner's goal or his answers.
- `@@ playbook NAME [FIRST-LAST]` (one step) reads the file again, hashes those bytes and loads them only if the hash
  equals the approval now and the one listed at run start. The text goes into the first message as one
  `[PLAYBOOK name lines a-b of n]` ... `[END PLAYBOOK]` block, never into a `[RESULT]` block, at most 3,000
  characters (about 860 tokens) cut at a line, with "@@ playbook NAME 42- shows the rest" when more is left. A later
  load replaces the block, so the window never holds more than one part. The step's own result only says what was
  loaded and the run's tools now.
- Tools: loading narrows the run's tools to the run's tools that the playbook lists (plus `finish`). Nothing is
  ever added: a playbook that lists `run_check` gets none in a run without commands.
- Markers: a playbook's text cannot forge the result, notes, plan or playbook markers or the owner's line; and since
  this change no result, file name, notes file or plan can forge `[PLAYBOOK` or `[END PLAYBOOK]` (marked as inside the
  data, as `[NOTES` since P-046).
- A `playbook` event `{step, name, source, sha256, first, last, total, tools}` goes in the run log. Continue (P-044)
  loads the last one again (approval checked again); a playbook no longer approved is left out, with one line.

## The review run

- `POST /api/vibe/projects/{pid}/agent/review` `{checkpoint, playbook?, focus?, model?, confirm_cloud?, cloud_id?,
  temporary?}`. `playbook` is `review` (default) or any approved playbook; `focus` is the owner's line (at most 300
  characters). 404 with playbooks off; 409 when the playbook is not approved; 404 for an unknown checkpoint, 400 for a
  malformed one; 409 when
  no file changed between the checkpoint and the files now (`vp.diff(pid, checkpoint, "working")`). Answers from
  anywhere like `/run`: the run is read-only, and commands are never offered to it.
- The run's tools are the playbook's tools cut to `list_files`, `read_file`, `search`, `ask`, `finish` (a P-047
  tools list, so Continue keeps it). No `playbook` tool. The goal is fixed engine text ("Review the changes since
  version c0012: 3 files changed.") plus the focus line. The first message holds the playbook block and the changes
  as data: `[RESULT 0 changes since c0012]` with each file and its status, then the diffs, at most 6,000 characters
  in all (the file list keeps to a third of that, and 300 are kept for the note on the files left out).
- A playbook with `report: findings`: at `finish` the engine reads lines `- [high|medium|low] path[:line] text`
  from the report (at most 30, path 200 and text 300 characters) and emits `findings {step, items}` before `end`.
- Page: an ended run card that changed files gets "Review changes" and "Security review" (since the run's start
  checkpoint); the review run's card says it reads only; its findings are a list under the report, a path that is a
  project file opens in the editor. `/playbooks` in the composer opens a panel: each playbook, its state, Read
  (the file as text), Allow, Remove (the page says "allow", since "Approve" is the plan card's word). Everything goes
  in through `textContent`.

## Jail

The agent's file tools take project-relative paths only (`safe_rel`: no absolute path, `~` or `..`; no links), so
they cannot reach `~/.codec`. On top, `agent_write` and `agent_delete` refuse a target whose real path is inside
`OWNER_DIR` or `SHIPPED_DIR` or is `APPROVALS_PATH` (for a project folder that would sit inside them). Commands run
in the sandbox that writes only the project folder (unchanged). No route writes playbook text; approval stores a hash.

## Eval

`tools/vibe_agent_eval.py --playbooks` points the module at the four shipped playbooks, approved in a temp file, so
every task's prompt lists them. Bar: tasks 1 to 12 against the P-041 baseline, local model only, when no voice call,
image job or other eval runs. Without `--playbooks` the eval also points the module at its temp folder, with none
  approved, so it never reads the owner's playbooks.

## App bundle

`packaging/macos/build_app.sh` copies `vibe_playbooks/` next to `routes`, `skills` and `static`, so the app ships the
four playbooks.

## Tests

`tests/test_vibe_playbooks.py` (temp folders, scripted model, no network): the front block rules, the four shipped
playbooks valid and not approved until clicked, approval by hash and a changed file asking again, the routes (403
for a remote approve and revoke, 409 for a stale hash), the listing caps (10 names, 1,200 characters), a load in a
PLAYBOOK block and not a RESULT block, a 20 KB playbook cut at 3,000 characters with its continue range, narrowing
and no tool added, a file changed after approval refused, forged markers, the jail through `agent_write`,
`agent_delete`, the engine's tools and a project folder inside the playbook folder, the review run (tools, data
block, 409s, findings), Continue, the switch. Page: Node tests of the new plain functions.

## Rollback

Switch `vibe_agent.playbooks` off, or revert the PR. The agent never writes the playbook folders or the approval
file, so nothing else needs restoring; the approval file can be deleted to forget every approval. Deploy: the owner
restarts `codec-dashboard`.
