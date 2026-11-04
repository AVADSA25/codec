# Vibe agent: model and tokens on the run card, Settings > Vibe, a doctor check (P-065)

Date 2026-11-03. Owner's go for the 25 Oct audit plan: "go go build i trust ur judgment here" (3 Nov 2026). That go
is the approval of this note (the repo's design-first gate). Scope cut by the session lead: this PR builds done-when
(a), (b), (c), (d), (f) and (g) of P-065. (e) the headless runner and (h) the weekly summary and thumbs control are
deferred to a later item.

## Why

The owner cannot tell which model produced a run, how many tokens it used or what it cost; the model choice resets
to Local on every reload and only one cloud entry is ever offered; six limit keys have no screen; there is no health
check for the sandbox and the model server; agent replies do not reach Settings > Usage.

## (a) Model name and kind

- `GET /api/vibe/agent/models` keeps `local` and `cloud` (the default entry: the active one, else the first) and adds
  `clouds`: every registered cloud entry, `{id, label, host, ready, reason}`, in config order.
- A run request may name the entry: `cloud_id` (a string) next to `model: "cloud"` and `confirm_cloud: true`. An
  unknown id answers 400; without `cloud_id` the old choice stands (active, else first). Continue keeps the old run's
  entry id from its `start` event. Vibe chat (`codec_vibe_chat.resolve_llm`) takes the same `cloud_id`, so chat and
  agent always send to the entry the dialog named.
- The `start` and `end` events carry `model: {kind, id, label}`; `id` is the local model id or the cloud entry id.
  `start` also carries `commands` (whether `run_check` is offered to this run). The `/run` and `/continue` answers
  are unchanged.
- The page lists Local plus every cloud entry (`cloud:<id>`; not-ready ones disabled with the reason). The pick is
  kept per project in this browser (`localStorage` `vibeAgentModel:<project>`), read again when the project opens; a
  remembered entry that is gone or not ready falls back to Local. Every cloud send still asks first (unchanged).
- The run card shows "Local: <model>" or "<label> (cloud: <id>)".

## (b) Tokens and spend

- After each model reply the run emits `usage {prompt_tokens, completion_tokens, estimated}` (run totals so far; no
  `step` field, so step filters and the Continue rebuild never see it) and, for a cloud run, `spent_usd` (month to
  date, `ccm.spent_usd(entry id)`) and `cap_usd`. The `end` event carries the same fields. `estimated` is true once
  any step had no usage from the server (the totals then include the existing estimate of 3.5 characters a token).
  This is common: the agent stops reading the stream as soon as a tool call is complete, before the server's last
  chunk with the numbers.
- The card's meta line shows "1.2k in, 340 out tokens (estimated)" and, for a cloud run, "$0.04 of $10 this month".

## (c) Settings > Vibe and the limits

- `vibe_agent` becomes a nested block of `PUT /api/config` (`routes/config.py`, the path Settings already uses):
  `max_steps`, `max_seconds`, `max_context_tokens`, `max_reply_tokens`, `wait_seconds` as whole numbers inside
  `codec_vibe_agent.BOUNDS` (outside: 422, nothing saved), `enabled` and `allow_commands` (true or false),
  `append_prompt` (text, at most 1,200 characters; longer: 422). Other `vibe_agent` keys are kept as they are.
- `allow_commands` (default true): false takes `run_check` out of the run's tools, so out of the system prompt and of
  what the reply parser accepts.
- `append_prompt`: the owner's own text, added at the end of the system prompt under one heading. A value longer
  than 1,200 characters found in the file (hand edit) is ignored.
- `.vibe/project.json` may hold an `agent` block. It can only lower: a number in `BOUNDS` applies when it is lower
  than the global value, `allow_commands`, `notes`, `import_claude_md`, `budget_line` and `todo` apply only when
  false. Anything that would raise a limit or switch something on is ignored, as is any other key. Only the owner
  writes there (`.vibe` is closed to the agent and to commands).
- The Settings page gets a "Vibe" section with those eight controls (min and max taken from `BOUNDS`, pinned by a
  test) and one line from the doctor below.

## (d) Doctor

`GET /api/vibe/doctor` (behind the login; 403 for a tunnel request) answers five booleans and nothing else:
`sandbox_exec` (`/usr/bin/sandbox-exec` on macOS), `perl` (`/usr/bin/perl` executable, the command watchdog),
`model_server` (`GET <local base>/models` answers 200 within 2 seconds, only for a host on this Mac or network, no
proxy, and a redirect is not followed, so the check never reaches another host), `cloud_ready` (at least one cloud entry passes `block_message`), `vibe_db` (`~/.codec/vibe.db` and its folder
can be written, checked with `os.access`; the database is never opened). It works with the agent switched off.
Read-only: no write, no start, no prompt.

## (f) SECURITY.md

The scope list names `codec_vibe_agent_cmd.py` (the command sandbox) and `codec_vibe_projects.py` (the project
store and its path rules).

## (g) Usage

Each model reply of a run calls `codec_usage.record_reply(model, base_url, stats, source="vibe")` with the
completion tokens, the seconds and, when the server sent usage, the speed (the Vibe chat already records with
`source="vibe"`). No schema change. Settings > Usage shows the replies by source, with "Vibe" as one label.

## Data and audit

No audit event is added or changed. Run logs gain `usage` lines and the fields above; older logs read as before (no
id: the card shows the label only; no tokens: nothing shown). No migration.

## Tests

- `tests/test_vibe_runcard.py`: model id and kind on start and end; `clouds` lists every entry; `cloud_id` picks the
  entry and an unknown one is refused; continue keeps it; usage events and end totals, estimated or not; a fake cloud
  entry run shows tokens and month-to-date spend (spend file and reply log in `tmp_path`); `record_reply` gets
  `source="vibe"`; `allow_commands=false` drops `run_check` from tools and prompt; the project block lowers and never
  raises; `append_prompt` reaches the system prompt and a longer one does not; the doctor answers booleans only (no
  path, no key text), 403 through the tunnel; the config block refuses out-of-range values and a long prompt.
- Node tests of the page functions (model text, token text, the pick per project, the chat request's `cloud_id`).
- Headless Chrome on a temp profile against a dashboard started from the worktree on a free port: Settings > Vibe
  and the Vibe page render, console read last.

## Rollback

Revert the PR. Config keys left behind (`vibe_agent.allow_commands`, `append_prompt`) are ignored by the old code;
`usage` lines in run logs are ignored by the old page (unknown event).
