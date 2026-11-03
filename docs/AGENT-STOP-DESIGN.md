# P-010: Project-mode Abort and Pause really stop a running agent

**Status:** design, then build (GAP-209 of the 25 Oct 2026 audit).
**Touches:** `codec_agent_runner.py` only (AGENTS.md section 10 zone, Step 9 runner: targeted fix, no refactor).
**Owner's word (2026-11-03):** "go go build i trust ur judgment here", about finishing the audit plan. That is the approval for this design gate.

## What is wrong

`POST /api/agents/{id}/abort` and `/pause` only write the new status to `manifest.json`. The runner thread reads the status once, at run start, and its step loop never looks again. So after a Stop or Pause the agent keeps asking the model and keeps calling skills (Gmail, files, whatever it was granted) until the plan ends or blocks. The page says "stops where it is"; it does not.

## The fix

The runner thread re-reads its manifest at step boundaries. When the status reads `paused` or `aborted`, it raises a new exception, `StopRequested(status)`.

Where it checks (all inside `_execute_checkpoint`, plus one line in `_qwen_next_action`):

| Site | Why |
|---|---|
| Before every model call (`_next_action`, which also covers the permission-correction retry) | no new model call after a stop |
| After every model call returns | a stop that arrived during a ~20 s model call wins; its answer is not acted on |
| Before the JSON-format retry inside `_qwen_next_action` | that retry is a second model call |
| After a destructive confirmation returns, before the at-most-once marker is written | the confirmation can stay open for 10 minutes; a stop that arrived meanwhile wins, nothing runs, and no marker is written, so a resume asks again |

The skill call that is running when the stop arrives finishes; the runner never kills a skill mid-way (it could leave work half done). Nothing new starts after it.

`_run_agent` catches `StopRequested` in the per-checkpoint `try`, next to the other exceptions:

- It saves the checkpoint progress once more (`_persist_checkpoint_progress`, same data the last step saved, plus any user replies drained at checkpoint start) and returns. The checkpoint-complete save (cursor `current_checkpoint` +1) and every completion message are skipped.
- **Aborted:** it posts one `agent_aborted` message titled `Interrupted` and emits `agent_aborted` (reason `user_aborted`). The route already set `aborted`, so the runner does not call `_atomic_set_status` (it would only log "superseded"); it posts the message itself. `agent_aborted` also sends the content-free phone push `agent_stopped`, which tells the owner the stop actually took effect.
- **Paused:** it emits `agent_paused` (reason `user_paused`) and the thread exits. No message: the page already shows Paused.

**Resume** needs no change: `/resume` sets `running`; the daemon's next tick sees `running` with no live thread and takes its crash-resume branch (`crashed_resumed` then `running`), and `_run_agent` reloads `cp_history` and `executed_destructive` for the in-progress checkpoint, so the run continues from the last saved step. The daemon logs this resume as `agent_resumed` with `recovery: true`, as it does for a restart; that wording is unchanged.

A quick Pause then Resume before the next boundary is seen as `running`, so the thread simply continues (no second thread: the daemon only respawns when no thread is alive).

## What does not change

- `_VALID_TRANSITIONS` (codec_agent_plan.py): untouched. `running -> paused` and `running -> aborted` already exist; `aborted` stays terminal.
- `VALID_MESSAGE_TYPES` (frozen): no new type; the message is `agent_aborted`.
- Audit: no new event and no envelope change. `agent_aborted` and `agent_paused` gain the reason values `user_aborted` and `user_paused` (the same words the routes already store in `status_reason`).
- The at-most-once ledger (`executed_destructive`): unchanged. A destructive step that ran before a pause is skipped on resume, as after a crash.
- Routes, pages, state.json keys: unchanged.

An unreadable manifest does not stop a run (same as today); only a manifest that reads `paused` or `aborted` does.

## Known limits (follow-ups, not in this change)

- A stop does not cancel an open destructive confirmation question; the thread notices the stop when the question is answered or times out (600 s). The answer is then ignored.
- The daemon spawns `_run_agent` even when its `crashed_resumed` (or `blocked_on_qwen -> running`) transition did not apply, so a Pause landing inside one daemon tick, between its scan and that transition, can be overridden when `_run_agent` moves `paused -> running`. Fix: skip the spawn when the transition returns False.

## Test plan

New `tests/test_agent_stop.py`. Every path is in `tmp_path`: `codec_agent_plan._CODEC_DIR`, `_AGENTS_DIR`, `_GLOBAL_GRANTS_PATH`, `codec_agent_messaging._CODEC_DIR`, `_AGENTS_DIR`, `_NOTIFICATIONS_PATH` and the audit log; `codec_push.notify` is replaced by a recorder, so the live inbox, `~/.codec/agents` and the phone are never touched. A fake slow skill and a fake model, both held on events, stand in for the real ones; the routes are called through FastAPI's TestClient.

1. Abort while step 1's skill runs: after it ends, no model or skill call starts; `current_checkpoint` stays 0; the manifest reads `aborted`; exactly one `agent_aborted` message, titled `Interrupted`; no checkpoint or `agent_done` message; one `agent_stopped` push.
2. Abort while the model call runs: its answer is not acted on (no skill call).
3. Pause halts at the next boundary and the thread exits; a daemon tick while paused starts nothing; Resume plus a daemon tick re-enters through the crash-resume branch (`agent_resumed`, recovery true) and continues from the saved step (step 1 is not replayed).
4. A destructive step that ran before a pause is not run again after resume (ledger); a pause that lands while its confirmation is open runs nothing and writes no marker, so the resume asks again and runs it once.
5. The JSON-format retry is not sent after a stop.

Existing runner tests must still pass.

## Rollback

`git revert` the change, then the owner restarts `codec-agent-runner`. An agent aborted by the new code stays aborted (`aborted` is terminal), which is the intended outcome. A paused agent whose thread exited resumes through the unchanged daemon branch with or without this change.
