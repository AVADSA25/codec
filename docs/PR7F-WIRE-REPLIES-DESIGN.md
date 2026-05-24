# PR-7F — wire user replies into the agent loop (Audit B / B-6)

> Wave 7. Closes **B-6** (`get_unread_user_replies` is defined but never called —
> replying to a running agent does nothing). Reference: `PHASE-1-PROJECTS-PILOT.md` B-6.

## The hole

`codec_agent_messaging.get_unread_user_replies(agent_id, since_ts)` exists, and
`POST /api/agents/{id}/messages` writes `user_reply` lines, but `_run_agent`
**never calls it** — so a user answering a running agent is silently ignored
(both a UX dead-end and a safety gap: you can't course-correct mid-run).

## The fix

- `_drain_user_replies(agent_id, since_ts) -> (entries, new_cursor)`:
  pulls unread `user_reply` records, turns each into a history entry shaped like
  the existing ones (`{step, skill:"user_reply", task:"", result:"[USER REPLY] …"}`
  — so `_qwen_next_action`'s generic history renderer shows it to the model),
  and returns a fresh cursor (`time.time()`). Lazy-imports messaging; returns
  unchanged on any error.
- In `_run_agent`'s checkpoint loop, **at each checkpoint start**, drain replies
  into `history` and advance/persist `state["last_reply_ts"]` so the next Qwen
  call sees them and they're not re-read after a restart.

Cursor = `time.time()` after each drain (the reply writer shares the machine
clock; `get_unread` already filters `ts > since_ts`). The same-millisecond
double-read/drop edge is the separate LOW finding **B-20** (offset-based cursor)
— noted, not fixed here.

## Scope

`_run_agent` checkpoint-loop injection + one helper. **B-8** (recover the
`blocked_on_destructive` dead-end) is the next PR — kept separate so this stays
small. Does not touch `_execute_checkpoint`'s signature (tests depend on it).

## Test plan (`tests/test_user_replies.py`)

- `_drain_user_replies`: a `user_reply` in `messages.jsonl` → returned as a
  history entry with the body; cursor advances; a reply older than the cursor is
  not returned.
- **Integration:** an approved agent + a pending `user_reply`; run `_run_agent`
  with a recording `_qwen_next_action` → the reply body appears in the `history`
  the model receives (proving it's wired). Fixture redirects both
  `codec_agent_plan._AGENTS_DIR` and `codec_agent_messaging._AGENTS_DIR`.

## Rollback

One helper + a few lines in `_run_agent` + one test. `git revert`.
