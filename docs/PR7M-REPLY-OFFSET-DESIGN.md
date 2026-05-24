# PR-7M — Reply dedup by consumed-offset, not millisecond timestamp (Audit B / B-20)

**Status:** design → TDD → ship
**Closes:** Audit-B **B-20** (reply dedup by strict-`>` millisecond timestamp). Pairs with
B-6 (PR-7F).
**Branch:** `fix/pr7m-reply-offset`
**Touches:** `codec_agent_messaging.py` + `codec_agent_runner.py`.

## What

`get_unread_user_replies(agent_id, since_ts)` dedups user replies by `rec_ts > since_ts`
on millisecond ISO timestamps. Two replies in the same millisecond — or a cursor taken
from the last-read reply's `ts` — can **drop or double-read** a reply. Replace the float-ts
cursor with a **monotonic consumed-offset** (count of user-replies already fed in): the
i-th `user_reply` line has index `i`; records with `index >= since_index` are returned.

## Why it matters

The reply path is how a user course-corrects a running agent (B-6). A dropped reply means
the steer is silently lost; a double-read re-injects a stale instruction. A timestamp
compare is inherently fragile (clock granularity, equal-ms collisions, clock skew across a
restart). A consumed-offset is clock-independent and exactly-once by construction.

## Design

### messaging.py

- `get_unread_user_replies(agent_id, since_index: int = 0)` — iterate `messages.jsonl` in
  file order, count `type == "user_reply"` records; return those whose 0-based index is
  `>= since_index`. (No timestamp parsing at all.)
- New `count_user_replies(agent_id) -> int` — total user-reply count (= `len(get_unread(..., 0))`).
  Used only for the legacy-cursor heal-forward.

### runner.py

- `_drain_user_replies(agent_id, since_index: int)` → `(entries, new_index)` where
  `new_index = since_index + len(replies)`. **Advance by the reply count, not the entry
  count** — an empty-body reply produces no history entry but must still advance the cursor
  so it isn't re-read forever.
- `_run_agent` cursor resolution (replaces the `last_reply_ts` read):
  ```python
  if "replies_consumed" in state:
      cursor = int(state["replies_consumed"])
  elif state.get("last_reply_ts"):          # legacy agent mid-run across the upgrade
      cursor = count_user_replies(agent_id)  # heal forward: treat all current as consumed
  else:
      cursor = 0
  replies, state["replies_consumed"] = _drain_user_replies(agent_id, cursor)
  ```

### Migration (legacy `last_reply_ts`)

An agent that was running at upgrade time has `last_reply_ts` (a float) but no
`replies_consumed`. The old cursor was set to `time.time()` at each drain, i.e. "everything
posted before now is consumed." We mirror that exactly: initialize `replies_consumed` to the
**current** total user-reply count (heal-forward), so no pre-upgrade reply is re-injected.
A fresh agent (`last_reply_ts` absent) starts at 0. The stale `last_reply_ts` key is left in
state harmlessly (PR-7I's completion save preserves unknown keys).

## Schema / API changes

- `get_unread_user_replies` param renamed `since_ts: float` → `since_index: int` (default 0).
- `_drain_user_replies` param `since_ts: float` → `since_index: int`; return cursor is now an
  int offset.
- New state key `replies_consumed: int` (replaces `last_reply_ts` going forward). No manifest
  / on-disk-schema change.
- New helper `count_user_replies`.

## Rollback

Revert the single commit. The state key flips back to `last_reply_ts`; a lingering
`replies_consumed` is ignored by the reverted code. No data-shape migration to undo.

## Test plan (TDD)

New `tests/test_reply_offset.py`:
1. `test_offset_cursor_skips_consumed_replies` — 3 replies; `get_unread(..., since_index=2)`
   returns only the 3rd.
2. `test_same_millisecond_replies_each_consumed_once` — 2 replies with an **identical** ts:
   drain from 0 → both, cursor=2; a 3rd reply (same ts) → drain from 2 → only the 3rd (a
   ts-cursor at that ms would drop it).
3. `test_drain_advances_offset_by_reply_count` — `_drain_user_replies(agent_id, 0)` with 2
   replies → `(entries, 2)`.
4. `test_empty_body_reply_advances_cursor` — an empty-body reply yields no history entry but
   the cursor still advances past it (no infinite re-read).
5. `test_legacy_last_reply_ts_heals_forward` — state has `last_reply_ts` (no
   `replies_consumed`) + an existing reply; `_run_agent` must NOT re-inject that pre-upgrade
   reply.

Update `tests/test_user_replies.py`'s two drain tests to the offset contract (the cursor is
now an int count, not a float ts).

Full suite: zero new failures vs the 41-failed baseline. Ruff: zero delta vs origin/main.
