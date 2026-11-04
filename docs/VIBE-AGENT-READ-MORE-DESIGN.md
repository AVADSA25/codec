# Vibe agent: read more and in parallel (P-072)

Status: approved by the owner's go-ahead on the audit plan, 3 Nov 2026. Depends on P-049 (in this branch's base)
and P-065 (run_settings). P-015 answered (option C, 29 Oct).

## Why

Large reads flood the 10k window, every read costs a 15 to 25 s step on the local model, and the agent cannot see
notebooks, PDFs or images: `read_file` refuses every file that is not UTF-8 text.

## What

Three parts, each on its own.

### 1. `@@ delegate`: a read-only helper with its own window

    @@ delegate
    Where is the cart total computed, and which files call it?

- The engine runs a nested loop on the run's own model (`Run._delegate`). Its tools are fixed in code,
  `proto.SUB_TOOLS = (list_files, read_file, search, finish)`. Any other block a helper writes (edit_file,
  write_file, delete_file, run_check, ask, read_output, delegate) is refused by that list before anything runs,
  whatever its prompt says. A helper cannot start a helper. A run whose request sent a tools list (P-047) cuts the
  helper's tools to it too (`Run._helper_tools`): a list only takes tools away, through a helper as well.
- At most `proto.SUB_MAX_STEPS` (8) steps, and never more than the run has left: every helper step counts toward
  `max_steps` (`Run._sub_steps`, added in the limit check, the budget line and the end event's `steps`).
- The answer reaches the parent as one `[RESULT n delegate ...]` of at most 1,500 characters
  (`proto.SUB_REPORT_CHARS`). A helper that runs out of steps returns what it looked at, marked failed.
- Its window is fresh: its own system prompt (`proto.delegate_prompt()`), the question, the owner's goal (500
  characters, context only), its own steps and the file map. The parent's turns are not in it. Older helper
  results are shortened first, like the parent's.
- No second project claim: the helper runs inside the parent's thread and holds nothing (`agent_claim` is called
  once per run). Same stop flag and deadline as the parent.
- No agent definitions are read from anywhere. The helper's prompt and tools are code; a project file named like
  one (`agents/explore.md`, `vibe-agents/*.md`) is only a file. Owner-folder definitions can come later.
- On the page each helper step is one `note` event (`sub`: the helper step); the run card's step line counts them,
  and nothing else is new to draw. The question is data: a forged owner line or notes block in it is marked. The run log
  keeps those notes and never a helper's reply. A private run (P-077) logs nothing and keeps no output: the
  helper writes through the parent's `_emit`, and has no command tool.
- Audit: one `vibe_agent_step` per helper step with `sub` (its step number); refusals are `vibe_agent_blocked`
  with reason `mode`.
- Not in plan mode: `PLAN_TOOLS` is unchanged.

### 2. Several read blocks in one reply

- `proto.READ_TOOLS = (list_files, read_file, read_output, search)`. When the first block of a reply is one of
  them, `scan_reply` reads on: further read blocks, one after the other with only blank or fence lines between,
  join it (at most `MAX_READS` = 4). `Scan.calls` holds them when there is more than one.
- The stream no longer closes at the first read block: it closes as soon as the line after the blocks can no longer
  start another block or a fence (a sentence, a made-up result), at a fifth read block, or at the end of the reply.
  So a reply that is only a read block is read to its end; one that writes on after it is still cut early. A reply whose first block is an
  edit, a write, a delete, a command, ask, delegate or finish is read exactly as before.
- A reply that mixes reads with another block runs that block alone (`Scan.dropped` counts the reads; the result
  says they did not run). Edits and commands stay single.
- The engine (`Run._run_reads`) runs each block as it would run alone (plan-mode refusal, plugin hooks), then fits
  all of them in the one 6 KB result window: a fair share each, unused room given to the longer ones, a file read
  cut at a whole line with its "more lines follow" hint, any other block cut at a line with a "run it alone" hint.
  The step is one tool event named `reads`, one result, one turn. A batch turn records the files it read
  (`_Turn.paths`) and its blocks (`_Turn.parts`): a later edit of one file hides only that file's block, the others
  stay to copy from (the first live eval hid them all, and task 3 took 10 steps with two failed edits). The whole step
  is stale once every file it read has changed, or when it came back from a log, where its blocks are not kept apart.

### 3. `read_file` on notebooks, PDFs and images

- `.ipynb`: the JSON is shown as cells: `[cell 3: code]`, its source, then its text outputs (stream text,
  `text/plain` results, errors with colour codes removed). Image and HTML outputs are left out and named
  (`[output 3: image/png, left out]`). JSON that does not parse is read as text. Edits still work on the JSON.
- `.pdf`: text through `codec_pdftext.extract_text` (P-033: pdftotext when installed, PDFKit otherwise), never a
  command in the agent's sandbox. Pages are marked when the text has page breaks.
- `.png .jpg .jpeg .gif .webp`: the bytes go to `codec_vision.describe_sync`, the one vision path: the local model
  unless the owner set `vision_provider: "gemini"` (P-020), the loaded model's name (P-013). The description comes
  back as the result, marked as the vision model's.
- All three are numbered lines read with the usual `FIRST-LAST` range, cut to the 6 KB window with the usual
  "more lines follow" hint. The converted text is kept for the run (`Run._docs`, 8 files, keyed by path and a SHA-256 of
  the bytes), so reading on in a long PDF or image description asks the helper or the vision model once.
- Checks before anything is read: the same `vp.read_file` checks as any read (path, secret names, links, 1 MB),
  then the bytes through `O_NOFOLLOW`.

## API and data

- New tool `delegate` in `proto.TOOLS` (so a request's tools list can leave it out); new `Scan` fields `calls`,
  `dropped`, `extra`; new `_Turn.paths`; new event field `sub` on helper notes; new audit fields `sub` and `calls`
  on `vibe_agent_step` (inside `extra`, no envelope change). No route, schema or config change. The system prompt
  gains one tool line and two rules, about 110 tokens; without them it is the old prompt byte for byte (pinned in
  `tests/test_vibe_agent_plan.py`).

## Migration

None. Old run logs replay as before (a `reads` or `delegate` step comes back as one turn).

## Tests

`tests/test_vibe_agent_readmore.py` (scripted model, temp projects, vision and PDF helper mocked; one Mac-only
check with a real PDF through PDFKit or pdftotext). Extended: `test_vibe_agent_proto.py` (several read blocks)
and `test_vibe_agent.py` (stream close). Live: the eval bar against the P-041 baseline, task 3's step count.

## Rollback

`git revert` of the PR. Nothing is stored that needs undoing; old logs with `reads` or `delegate` steps replay as
plain turns. Takes effect after the owner restarts `codec-dashboard`.
