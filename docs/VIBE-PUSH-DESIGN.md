# Phone push when a Vibe run waits or ends (P-070)

Plan item P-070 (audit 25 Oct 2026). Builds on P3.13's Web Push (docs/P3.13-DESIGN.md). Method: reimplement the
"tell me when the agent waits" behaviour on CODEC's own push; no outside code.

## Why

A Vibe agent run that waits for a command approval or an answer shows only a card on the page, and it stops after
`wait_seconds` (600 s by default). An owner away from the screen never learns that it waited, or that it ended.

## What

- **Three fixed-text kinds in `codec_push.KINDS`** (the privacy contract: callers pass a kind, the push carries only
  the fixed line; no project name, goal, command, question or file text):

  | kind | title | body | switch | opens | TTL | urgency |
  |---|---|---|---|---|---|---|
  | `vibe_approval` | Vibe wants to run a command | Approve or decline it on your Mac. | approvals | `/vibe` | 600 | high |
  | `vibe_question` | Vibe has a question for you | Tap to answer it. | questions | `/vibe` | 600 | high |
  | `vibe_done` | A Vibe run has ended | Open its project in Vibe to see the report. | agents | `/vibe` | 86400 | normal |

  No new switch: the existing Approvals, Questions and Agent results switches cover them. The approval body says
  "on your Mac" because command approvals are refused away from the Mac (`routes/vibe_agent._mac_only`).
- **Where they are sent** (`codec_vibe_agent.py`, through one hook `_push(kind)` that calls `codec_push.notify` and
  swallows every error, so a run is the same with push off, not set up or broken):
  - `Run._approval`: after the approval event is out, `vibe_approval`.
  - `Run._ask`: after the question event is out, `vibe_question`.
  - `Run._end`: after the end event, `vibe_done`, except when the owner pressed Stop (the stop flag is set): the
    owner did that himself. A run that stops because nobody answered in time does push.
  `notify` sends from a daemon thread and throttles to one push per kind per 60 s, as for every kind.
  `tools/vibe_agent_eval.py` replaces `_push` with a no-op while it runs, as it does `_audit`: eval runs are not
  the owner's and never reach his phone.
- **The tap re-mounts the live run.** The service worker already opens any same-origin path, so `/vibe` needs no
  worker change. A fresh `/vibe` opens an empty session, though, so the page learns which run is live:
  - `codec_vibe_agent.live_run()` and `GET /api/vibe/agent/live` (behind the dashboard login like every route):
    `{"project", "run", "status"}` of the one run going on this Mac (running or waiting), or nulls. Ids only.
  - `codec_vibe.html` `_reopenLiveRun()`, called once the editor exists: when a run is live and the page still has
    the session it opened with, with no message sent in it yet, it opens that project's newest chat session (`ldSes`), and `VibeProj.open` hands the
    project to `VibeAgent.adopt`, which mounts the run card with its question or approval. No session for the
    project (a run started without the page, such as the eval tool): nothing is opened. An ended run is not reopened
    on load, so a plain visit to `/vibe` stays as it is.

## API, data, schema

New kinds in `codec_push.KINDS`, new read-only route `GET /api/vibe/agent/live`. No new file, no new audit event
(`push_sent` already carries the kind), no config key, no schema change, no new dependency. `static/sw.js` and
`static/vibe-agent.js` are unchanged.

## Tests

- `tests/test_vibe_agent_push.py`:
  - the three kinds: exactly the table above, title and body at most 60 characters, no placeholder characters,
    each switch one of `codec_push.SWITCHES`, and the whole key set pinned;
  - a scripted run (run_check, ask, finish) with a fake push endpoint (`codec_push.STATE_PATH` and `_post` patched as
    in `tests/test_push.py`, never `~/.codec/push.json`): one push each at the approval, the question and the end,
    each decrypted payload equal to the table, and no goal, project id or title, file name or text, command,
    question or answer in any payload or header;
  - each switch off in turn: that kind is not sent, the other two are;
  - push unavailable (`notify` raising, `codec_push` not importable): the run's events are the same;
  - Stop sends no `vibe_done`; a run that times out waiting does;
  - `live_run` and the route: ids while running or waiting, nulls after the end, never the goal;
  - Node: `static/sw.js` shows each Vibe kind's real payload and a click on it navigates an open window to `/vibe`
    or opens `/vibe`;
  - Node: `_reopenLiveRun` taken from the page opens the newest session of the live project, and does nothing with
    no live run, no session for it, or a session already chosen; it is called after the editor is created.
- `tests/vibe_agent_fakes.py` replaces `_push` with a no-op in its autouse fixture, so no other agent test reaches
  `codec_push`.
- One headless Chrome check on a temp profile against a scratch server from this worktree (temp Vibe root, temp
  `vibe.db`, a scripted model that asks a question, the dashboard's CSP): `/vibe` loads, the run card shows the
  question, and the console read has no script error and no CSP report. Not committed.

## Rollback

Revert the PR. Nothing to undo in data or config. Deploy and rollback need a `codec-dashboard` restart (the
owner's word); the agent runs inside the dashboard process.
