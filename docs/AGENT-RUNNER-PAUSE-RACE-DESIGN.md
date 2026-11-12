# A Pause inside one daemon tick is kept (2026-11-11)

## What was wrong (known issue 2026-11-03)
`codec_agent_runner._daemon_one_tick` scans the manifests, then acts on what it
read. Two branches started `_run_agent` even when their own status change was
refused:
- crash-resume (scan: `running`, no thread): the owner presses Pause after the
  scan, `paused -> crashed_resumed` is refused, the thread starts anyway, and its
  run-start moves `paused -> running` (allowed for /resume), so the pause is lost.
- model check (scan: `blocked_on_qwen`): the daemon's `-> running` can be refused
  (the agent was aborted); the thread started anyway.
A third window: the daemon's `blocked_on_qwen -> running` applies, the owner
pauses, then the thread's run-start moves `paused -> running`.

## Fix
1. Both daemon branches `continue` (no thread, no `agent_resumed` line) when
   `_atomic_set_status` returns False. The external change wins.
2. `codec_agent_plan.set_status` takes `refuse_from` (statuses this caller never
   moves away from), checked inside the same manifest flock as the transition.
   `_run_agent`'s run-start passes `("paused",)`; `_atomic_set_status` passes it
   through. `paused -> running` stays in the state machine for the /resume route,
   which then goes back through the daemon's crash-resume branch as before.

## Not changed
`_VALID_TRANSITIONS`, the routes, the stop checks at step boundaries (P-010),
every other `set_status` caller (`refuse_from` defaults to empty).

## Tests
`tests/test_agent_stop.py`: the three windows above (each fails on the old code)
and /resume still moving a paused agent.

## Rollback
Revert the PR; no stored data changes.
