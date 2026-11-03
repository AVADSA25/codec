# LLM queue: a cancelled waiter no longer blocks background calls (P-086)

Status: written and built on 2026-11-03 under the owner's go-ahead for the 25 Oct audit
plan items ("go go build"). Not live until the services are restarted.

## What

`codec_llm_proxy.LLMQueue.acquire` counts a caller in `_waiting[priority]` and then
waits on the semaphore with nothing around the wait. When the waiting task is
cancelled (voice barge-in or hang-up cancels the reply task) or the wait raises, the
count is never taken back. MEDIUM and LOW callers poll until no CRITICAL or HIGH
caller is waiting, so one cancelled voice reply made every crew call wait until
`codec-dashboard` was restarted.

The fix, all in `acquire`:

1. The semaphore wait is wrapped in `try/except BaseException`: on any exit that is
   not a taken slot, `_waiting[priority]` goes back down and the exception is raised
   again unchanged. This also covers a wait that raises instead of being cancelled.
2. The bookkeeping after the slot is taken is plain code with no `await`, so a cancel
   cannot land between taking the slot and returning to the caller, whose
   `try/finally` releases it. (Before, it sat inside `async with self._lock`; within one
   event loop that lock never has to wait, so this changes no result, it only removes
   the window.)

The unused synchronous wrapper `_SyncLLMSlot` / `llm_queue_sync` is deleted with its
two tests. No production code calls it (searched the repo and the user skill and
plugin folders). The `codec_llm.py` module docstring still names it; that file is
outside this change and the line is left for a later edit of that file.

## Why

Audit 25 Oct 2026 (BL-7, BC-7, GAP-140): reproduced on a copy; the counter stays
above zero for good.

## API and data

- `LLMQueue.acquire/release/slot/stats` and the `llm_queue` singleton: unchanged.
- Removed: `codec_llm_proxy._SyncLLMSlot`, `codec_llm_proxy.llm_queue_sync`.
- No state file, no audit event, no config key.

## Not in this change

- The cross-process "interactive" marker (a lock file telling background callers a
  chat turn is waiting). Chat, the agent runner and the Vibe agent do not use this
  queue, so a background agent can still sit in front of a chat turn on the same model
  server. The owner measures chat first-token time with one background agent running,
  before and after this change; a marker item is raised only if that is bad.
- `codec_voice.py`, `codec_agents.py`, `codec_llm.py` and
  `scripts/start_model_server.sh` are not edited.

## Test plan

- `tests/test_concurrent.py::TestLLMPriorityQueue::test_cancelled_critical_waiter_does_not_block_medium`:
  one slot taken, a CRITICAL waiter blocked on it is cancelled, the slot is released,
  and a MEDIUM acquire succeeds within 1 s with `_waiting[CRITICAL] == 0`. It fails on
  the old code (MEDIUM times out in the polling loop).
- The other tests in `tests/test_concurrent.py` and the voice and crew test files that
  run the queue's callers still pass.

## Rollout and rollback

The owner restarts `codec-dashboard` and `open-codec` to load it. Rollback: `git revert`
of this PR; nothing is written to disk, so nothing needs cleaning up.
