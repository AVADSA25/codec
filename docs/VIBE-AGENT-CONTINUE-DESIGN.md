# Vibe agent: Continue a run, and steer a running one with a queued message (P-044)

Date 2026-11-03. Owner's go for the 25 Oct audit plan: "go go build i trust ur judgment here" (3 Nov 2026).
Code: `codec_vibe_agent.py`, `codec_vibe_agent_proto.py`, `routes/vibe_agent.py`, `static/vibe-agent.js`, one CSS
rule and the script stamp in `codec_vibe.html`, plus tests. No sandbox, command, audit or database change.

## Why

1. A run is a thread inside `codec-dashboard`. Every restart (a deploy included) ends it; its log has no end event
   and it reads `interrupted`. The next run starts from scratch with four chat turns of 1,500 characters, so the
   model does not know what it already read, changed or was told.
2. To redirect a run the owner must Stop it and start again: the composer refuses text while a run is going.

P-040 now writes each step's model reply to the run log (`reply` events, secret shapes redacted). With the `tool`,
`result`, `checkpoint` and `message` events next to them, the log holds everything the model saw, so a run can be
rebuilt.

## What changes

**Continue.** `POST /api/vibe/projects/{pid}/agent/{run}/continue` `{confirm_cloud}` starts a new run on the same
goal that begins with the old run's turns rebuilt from its log:

- Allowed for the project's newest run when it ended `interrupted`, `stopped`, `limit` or `failed` (not `done`, not
  one still going): 409 otherwise. A finished run's view (`GET .../agent/{run}`) says `continuable: true|false`, and
  the page shows a **Continue** button on the end of the run card when it is true.
- Turns: for each step the assistant text is the `reply` event's text (what the model wrote, cut to 8,000
  characters as a live turn is), and the user text is the result as the model saw it, `wrap_result` over the
  logged output. Bad replies become the same error turns as live; reads of a file changed later are marked stale;
  an owner's message is put back after the result it followed. A log without reply text (an old log, or one written
  when the redaction patterns could not load) falls back to the tool line (`@@ read_file a.css`).
- A step that was cut off (a `tool` event with no `result`) becomes a failed turn that says so. For `run_check` it
  says the command was cut off, its result is unknown and it is not run again by itself. **No command is ever
  replayed:** Continue runs nothing; if the model asks for the command again, it goes through the normal gate (a
  new approval card, or the project's own "Always allow here" list, which is the owner's standing rule). An
  approval given once in the old run does not carry over.
- A continued run of a continued run follows the chain (`continues` in the start event, at most 3 runs back) and
  keeps the newest 40 turns.
- Fresh budget: the new run has its own `max_steps`, time limit, question count and repeat count. Its steps are
  numbered on from the old ones (so `[RESULT n]` stays unique), the start event says `continues` and
  `earlier_steps`, and the page counts "Step 1 of 30" from there. The end event's `steps` is this run's steps.
- Model: the old run's kind (local or cloud, from its start event). A cloud run needs `confirm_cloud: true` again,
  so the page shows the cloud dialog again; the cap check runs as for a new run. `can_run` follows the request's
  origin, as `POST /run` does: Continue from the phone gets no `run_check` even if the old run was started at the
  Mac.
- The first user message says this run continues an earlier one, and a line after the last old result says how it
  stopped; the old chat turns are not sent again (the turns carry the work).

**Steering.** `POST /api/vibe/projects/{pid}/agent/{run}/message` `{text}` stores one owner's note on a live run
(not Mac-only, like `/answer`; 1 to 300 characters, else 400; 409 when the run is over; a second note before the
first is read replaces it). At the top of the next step the note is put after the newest result, **outside** the
`[RESULT ...] ... [END RESULT]` wrapper, as `The owner says: <text>`, and a `message` event `{step, id, text}`
goes to the page and the log. `wrap_result` now also marks the phrase `The owner says:` (any case and spacing)
inside data as inside the data, and so does the file map that follows the note, so file text, command output or a
file name cannot forge the owner's line. A note sent during a long prefill waits for the
next step (15 to 25 s locally). If the run ends before it reads the note, nothing reaches the model.

**Page.** While a run is going, Enter in the composer queues the text: the page keeps one queued message, sends it
as a note, and shows it in the card until the `message` event says the agent read it. If the run ends without
reading it, the page starts it as the next goal; if the owner stopped the run himself, the text goes back into the
composer instead. A message over 300 characters is not taken while a run is going (toast, the text stays). **Esc**
in the composer stops the run. Continue adds "Continue: <goal>" to the chat, so a reload after a second restart
still finds the run.

**No claim file.** The plan named a write-ahead claim file under `.vibe`. The run log already holds what it would
say, in order: the `tool` event is written before a tool runs and `decision` before a command starts, so a cut step
is visible in the log; the project hold stays in memory, so nothing blocks Continue after a restart. One stored
item fewer.

## API and data

Two new routes (above). New fields: `continues`, `earlier_steps` on a start event; `continuable` on a finished run's
view; a new log and page event `message`. No new file, no new audit event, no database or envelope change. Old logs
read as before (they just have no `message` events).

## Tests

Scripted model, no network (`tests/test_vibe_agent_continue.py`, plus the proto, routes and page test files):
- a run cut mid-way (log without its end, as after a restart) reads `interrupted` and `continuable`; Continue's first
  prompt holds the old replies as assistant turns and their wrapped results; the step budget is fresh;
- a cut `run_check` is not run by Continue, its turn says so, and the command asked again waits for a new approval;
- Continue of a cloud run needs `confirm_cloud: true`; `can_run` follows the request's origin;
- a note reaches the next prompt after `[END RESULT]` as `The owner says: ...`; file text cannot forge it (parser
  test); caps, 404, 409, replacement;
- Node: start event with `earlier_steps`, `message` event, `canContinue`;
- headless Chrome on a temp profile: an interrupted run shows Continue and Continue runs; a queued note is read; Esc
  stops; a note the run did not read starts the next goal; console read empty.

Live (owner): after the `codec-dashboard` restart, start a run, restart, press Continue.

## Rollback

Revert the PR. Continue and steering are new routes and buttons; logs keep `message` events and start events keep two
extra fields, which older code ignores.
