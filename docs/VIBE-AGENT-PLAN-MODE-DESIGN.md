# Vibe agent: plan mode and a tools list per run (P-047)

Status: built in this PR (plan mode, tools list). Ask-edits is the second PR; its design is at the end.

## What

1. **Plan mode.** A run started with `mode: "plan"` reads the project and writes a plan. It may call
   `list_files`, `read_file`, `read_output`, `search`, `ask` and `finish`, and `write_file` for `PLAN.md` only.
   Any other tool call (`edit_file`, `delete_file`, `run_check`, `write_file` to another path) is refused before
   anything runs: the model is told "`<tool>` is not allowed in plan mode, so nothing was changed or run", the file
   stays as it was, and the step costs one step like any failed one. Its finish report is the plan, kept up to
   4,000 characters (`MAX_PLAN_CHARS`) instead of the 2,000 of other runs.
2. **The plan card.** When a plan run ends with `done` and is still the project's newest run, the card shows the
   plan and three buttons in our own words: **Build this plan**, **Change it first** (the plan in a text box, then
   **Build my version**) and **Plan again** (one line on what should change). The page sends them to one new route.
3. **The tools list.** `POST .../agent/run` accepts `tools`: a list of tool names. The run's tools are the tools it
   would have today, cut to the mode's tools, cut to that list. A name can only take tools away, never add one
   (`run_check` still needs a run started at this Mac and `sandbox-exec`). The list must name known tools and
   must hold `finish`.

## Why

Vibe had no read-only phase: a run went straight to changing files. A plan first lets the owner see and correct
the approach before any file changes, and the build run then follows a plan he approved.

## API and data

- `POST /api/vibe/projects/{pid}/agent/run` takes two new optional fields: `mode` (`"default"` or `"plan"`, else
  400) and `tools` (a list as above, else 400). Without them the request is today's.
- `POST /api/vibe/projects/{pid}/agent/{run}/plan`, body `{decision, plan?, note?, model?, confirm_cloud?}`:
  - `decision: "build"` starts a default run on the plan run's goal. Its plan is the plan run's report, or the
    owner's own text in `plan` (1 to 4,000 characters).
  - `decision: "again"` starts a new plan run on the same goal and tools list, which sees the earlier plan and the
    owner's `note` (1 to 600 characters) on what to change.
  - The source run must be a plan run, ended `done`, and the project's newest run (409 otherwise). `model` picks
    the new run's model like `/run` (local by default; cloud only with `confirm_cloud: true`), so a plan made on the
    cloud entry can be built locally. Commands follow the request's origin, as for `/run` and `/continue`. Like
    `/continue` it answers from anywhere: the new run is a normal run with all of its own gates.
- `GET .../agent/{run}` adds `plan_ready` (true or false) for a finished plan run only; other views are unchanged.
- The run log's `start` event gains `mode`, `tools`, `plan`, `plan_from`, `plan_note` and `plan_edited`, each only
  when set, so a default run's log is unchanged. Continue (P-044) reads them back: a continued plan run stays a plan
  run with the same tools, and a continued build run keeps its plan.
- Where the plan goes: the first user message, right after the goal, as `[PLAN]` ... `[END PLAN]`. The shrink loop
  never trims that message, and 4,000 characters is about 1,143 tokens of the 10,000-token window (a test builds a
  run with the longest goal, a 4,000-character plan and the notes cap, and checks that it fits). A system-prompt
  line, sent only to a run with a plan, says the block is the approved plan and cannot change the rules or give a
  permission. The plan's text cannot forge the result, notes or owner markers, and since this change no result,
  file name or notes file can forge `[PLAN]` or `[END PLAN]` (they are marked as inside the data, as `[NOTES` is
  since P-046). That marking is the only thing a default run can notice, and only in text holding those exact words.
- Audit: a refused step writes the existing `vibe_agent_blocked` with the new reason `mode`. No new event; the
  envelope is unchanged.
- Page (`static/vibe-agent.js`): a **Plan first** switch next to Chat and Agent, built by the script and shown in
  Agent mode only. It applies to the next run and turns itself off once that run starts, so plan mode is chosen per
  run. Everything is drawn with `textContent`.

## Migration

None. No stored file changes shape; old run logs have no `mode` and read as default runs.

## Test plan

- Engine with the scripted model (`tests/test_vibe_agent_plan.py`): a plan run's tools and prompt; `edit_file`,
  `delete_file` and `write_file` to another path refused with the file unchanged; `PLAN.md` written behind a
  checkpoint; the 4,000-character report cap (2,000 for a default run); build, change and again decisions and their
  409s; the plan in the first message of the build run, fitting the cap; the tools list cut and its 400s; Continue
  keeping the mode; the default system prompt byte for byte as before for every tool set a run can have today.
- Routes: the new fields and the new route, through the FastAPI test client.
- Page: the new plain functions under Node, and the card in headless Chrome against a slim server with a scripted
  model (the switch, the plan card, Build this plan starting a build run, no console errors).
- Live: `tools/vibe_agent_eval.py --bar --baseline` on tasks 1 to 12 (default mode unchanged), and a new task 15
  (plan mode, `needs: plan_mode`, outside the bar).

## Rollback

Default mode is unchanged and plan mode is opt-in per run, so not choosing it is the rollback; otherwise revert the
PR. Old logs stay readable either way.

## Ask-edits (second PR, not built here)

Goal: a run where every file change waits for the owner. Design:

- A second waiting state beside the command approval (`_approval`, which is for commands only): before
  `agent_write` or `agent_delete`, the engine works out the new text, emits an `edit` event with the path and a
  unified diff (capped, drawn as text), sets the run to `waiting` and waits on the same condition as `_approval`.
- Its own decision route, `POST .../agent/{run}/edit` with `{id, decision: "apply" | "decline"}`, not the command
  approve route: that one is built for commands and its card, and folding edits into it would make every edit wait
  like a command.
- A run started from a phone is answerable only at the Mac: the edit route answers 403 to a remote request, the
  phone's card says "Answer this at your Mac" and shows the diff read-only, and the push names only the fixed
  `vibe_approval` kind. A wait that runs out ends the run as `limit`; an edit is never applied without a click.
- Decline leaves the file as it was and tells the model in the result ("The owner declined this change ..."), with
  the same marker rules as every result.
- Opt-in per run (`mode: "ask_edits"`), never the default. No automatic classifier: the local model must not judge
  its own changes.
