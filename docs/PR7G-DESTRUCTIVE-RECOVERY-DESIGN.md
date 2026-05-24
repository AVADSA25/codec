# PR-7G — recover the blocked_on_destructive dead-end (Audit B / B-8)

> Wave 7. Closes **B-8**. Reference: `PHASE-1-PROJECTS-PILOT.md` B-8.

## The hole

When a destructive op's consent times out, `_run_agent` transitions to
`blocked_on_destructive` and audits it — but posts **no notification** (unlike
`blocked_on_permission`, which surfaces Grant/Abort actions). The recovery path
*exists* (`/api/agents/{id}/resume` → `running` is a legal transition; the daemon
re-runs a running-without-thread agent, and B-1 now re-issues the consent
prompt), but the user is never told they can resume and gets no button — a
silent dead-end.

## The fix

In the `blocked_on_destructive` branch, post an `agent_blocked` notification with
**Resume** (`/api/agents/{id}/resume`) + **Abort** actions and a body explaining
the consent will be re-issued. No new endpoint or state-machine change needed —
`/resume` already works and the daemon + B-1 do the rest.

```
post_message(type="agent_blocked",
             title="Paused: destructive op needs your confirmation",
             body="… Resume to be re-prompted for consent, or abort.",
             actions=[Resume → /resume, Abort → /abort])
```

(Auto-re-running `blocked_on_destructive` on the daemon tick is deliberately NOT
done — it would spam consent prompts; the human decides when to retry.)

## Test plan (`tests/test_destructive_recovery.py`)

- Set up an approved agent; mock a destructive action whose consent times out
  (`_enforce_destructive_gate` → `timed_out=True`); record `post_message`.
  Assert: status → `blocked_on_destructive` **and** a notification was posted
  carrying a **Resume** action pointing at `/api/agents/<id>/resume`.

## Rollback

One `post_message` block in `_run_agent` + one test. `git revert`.
