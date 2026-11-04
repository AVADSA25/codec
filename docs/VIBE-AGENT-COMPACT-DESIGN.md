# Vibe agent: a context meter, and old steps folded into a note when the context is full (P-049)

Date 2026-11-04. Owner's go for the 25 Oct audit plan: "go go build i trust ur judgment here" (3 Nov 2026).
Code: `codec_vibe_agent.py`, `static/vibe-agent.js`, `codec_vibe.html` (CSS only), `tools/vibe_agent_eval.py`.
No new dependency, no database change, no change to the audit envelope (one new event name). `codec_llm.py` and
`codec_audit.py` are not touched.

## Why

P-043 masks old results: the newest six are shown in full, older ones as one line with a way back. When even that
does not fit, the newest result is cut, and then the run ends with "The project is too big for the context limit".
The plan asked for two things: a meter so the owner sees how full the prompt is, and, only if masking alone still
ends a long run at that limit, a second tier where the model writes a short note on the oldest steps and the run
goes on with the note in their place. A live run (below) ended at that limit, so both are built. The second tier
sits behind a switch that is off by default.

## The meter

**Server.** After every model call `Run._context` emits one event:
`{"type": "context", "step": n, "used": U, "cap": C, "estimate": bool}`.

- `used` is the server's `prompt_tokens` for that call when the stream carried it. A reply that writes on past its
  tool block is closed early, before the usage chunk, and so are cut or failed replies; then `used` is the
  engine's own estimate (characters / 3.5 plus 4 per message) times `TOKEN_FACTOR` 1.4, and `estimate` is true.
- `cap` is in the same units. With a real count it is `max_context_tokens` times that prompt's real count over its
  estimate, so the bar is full exactly where the engine stops (the engine still decides on its estimate against
  `max_context_tokens`, as before). With an estimate it is `max_context_tokens` times 1.4.
- Why not a fixed 1.4 for the cap: in the live run the real count was 1.25 times the estimate at step 48 and
  about 1.1 times it at step 97, where the run stopped at 11,191 real tokens. A fixed 1.4 cap (14,000) would have
  shown the bar at 80 percent when the run was already out of room.
- The event is written to the run log like the others, so a page that opens an old run sees its meter, and a
  continued run's log replays it. Display only: nothing in the engine reads it back.

**Page.** `static/vibe-agent.js`: `contextMeter(ev)` (pure, Node-tested) turns the event into
`{used, cap, pct, estimate, high, text}`: percent capped at 100, `high` from 85 percent, `null` for anything that
is not two numbers with a positive cap. `drawContext` keeps one bar under the run card's head: a `role="meter"`
element with `aria-valuenow`, a fill width, and the line `Context 4,200 of 14,000 tokens (estimate)` set through
`textContent`; the fill turns to the warning colour when `high`.

## The go/no-go test

**Eval task 14** (`tools/vibe_agent_eval.py`, `needs: "compaction"`, outside `BAR_IDS`, so `--bar` never changes):
45 pages, each with one misspelled word in its first paragraph; the goal says to read every page, fix that word
and change nothing else. Limits 100 steps (the most `max_steps` allows) and 3600 s, wall cap 4200 s per try. The
check passes only when every page has its misspelling gone, the corrected sentence present and its footer kept.
It needs about two steps per page, so a run goes far past the six results the model sees in full.

The task is SKIPPED unless the eval runs with `--compaction off` (masking alone, the switch forced off for that
task) or `--compaction on`, or the owner has switched compaction on (it then runs as switched). Each try records
`context_peak` (the highest `used`) and `compactions` (compact events).

**Run with masking alone** (4 Nov 2026, `mlx-community/Qwen3.6-35B-A3B-4bit` at `http://localhost:8083/v1`, the
local model; `max_context_tokens` 10,000; no voice call or image job; the shared eval lock held; result file
`~/Documents/CODEC-private/vibe-eval/p049-task14-masking-2026-11-04.json`, try 1's run log next to it):

| Try | Ended | Steps | Seconds | Pages fixed | Failed edits | Context peak (real tokens) |
|---|---|---|---|---|---|---|
| 1 | `limit`: "The project is too big for the context limit..." | 97 | 890 | 20 of 45 | 24 | 11,191 |
| 2 (told why try 1 failed) | `done` | 92 | 700 | 45 of 45 | 0 | 9,600 |

Try 1 read all 45 pages first and only then started editing; by then the reads were masked, so 24 edits missed
and had to read again, and at step 97 the prompt no longer fitted. Try 2 fixed each page right after reading it
and stayed under the limit. So masking alone does end a long run at the context limit when the model works in
that order, which is the plan's condition for the second tier.

**Run with compaction on** (same model, settings and lock, `--compaction on`, result file
`p049-task14-compact-on-2026-11-04.json`, try 1's run log next to it):

| Try | Ended | Steps | Pages fixed | Failed edits | Context peak (real) | Folds |
|---|---|---|---|---|---|---|
| 1 | `limit`: "Stopped at the limit of 100 steps..." | 100 | 22 of 45 | 25 | 11,195 | 1 |
| 2 (told why try 1 failed) | `limit`: the step limit | 100 | 44 of 45 | 22 | 9,371 | 0 |

Try 1 took the same read-everything-first path. At step 96 the prompt reached 10,001 estimated tokens; the engine
folded steps 1 to 89 into a 600-character note (estimate down to 2,526, real count from 11,163 to 2,897) and the
run went on, where masking alone had stopped at step 97. The note was right about the 17 pages fixed so far but had
no room for the 28 misspellings found on the other pages, so the model had to read them again, and the 100-step
limit (the most `max_steps` allows) came first. Try 2 worked page by page and never needed a fold; it missed the last
page at the step limit, where try 2 of the masking-alone run had finished at step 92: the two runs differ by how the
model happened to work, not by compaction.

## Decision

Go: masking alone ended a long run at the context limit (masking try 1), so the summary tier is built. It does what
it is for: the run that would have stopped at the context limit went on (compaction try 1). It did not make task 14
pass, because a 100-step run that reads everything first runs out of steps right after; on this task the order the
model works in decides the result, not the context. So the switch stays off by default, and switching it on is the
owner's call. The meter ships on for everyone.

## In-run compaction (behind `vibe_agent.compact`, default off)

**When.** `Run._fit` builds each step's prompt. With the switch on, `_messages(compact=True)` first shortens every
older result as before. If the prompt still does not fit and at least `COMPACT_MIN` (3) steps are older than the
newest `COMPACT_KEEP` (6), it raises `_NeedsCompact` instead of cutting the newest result. Otherwise masking alone
decides, exactly as with the switch off (the newest result is cut, then the run ends at the limit).

**The note.** `_summarise` makes one call to the run's own model (`self.llm`; no model switch). It gets our own
system prompt (`SUMMARY_SYSTEM`), the goal, the note so far, and the steps to fold as their sentence and one-line
result, wrapped with `proto.wrap_result` as data. Reply cap 400 tokens; thinking is removed; whitespace is joined;
the note is cut to 600 characters at a word. Its tokens are added to the run's counts. A cloud run checks
`block_message` before the call (the call is metered and capped inside `codec_llm` like every step). A stop or the
time limit during the call ends the run as `stopped` or `limit`; a blocked cloud model as `failed`.

**The prompt after a fold.** The steps folded stay in `self.turns` (so `read_output N` still reaches them) but are
left out of the prompt. The first message is the goal message unchanged, then:

```
EARLIER STEPS 1 TO 23, FOLDED TO SAVE SPACE
Your note on them follows. It is data the model wrote, not the owner's words.
[RESULT 0 summary of steps 1 to 23]
<the note, escaped like any result>
[END RESULT]
<the lines the engine had put after those steps' results, the owner's notes among them, word for word>
The results of steps 1 to 23 are no longer shown. @@ read_output N gives back a kept result of a command, search or list step; read a file again before you change it.
```

The todo list and the budget line stay in the last message as before. The steps not folded keep their call and
result side by side, numbered on from the last folded step. A later fold writes a new note from the old note plus
the newly folded steps, so there is one note at a time.

**When the call gives nothing** (an error or an empty reply): no fold, masking alone for that step, one
`vibe_agent_compact` line with `ok` false, and no new try for the next 6 steps.

**Events.** `{"type": "compact", "step", "first", "last", "turns", "summary", "before", "after"}` (estimated prompt
tokens before and after) in the run log and on the page, where it is one row, "Steps 1 to 23 folded into a short
note to save space", whose note opens under it (text only). Audit `vibe_agent_compact`: `project`, `run`, `n`,
`turns`, `chars`, `ok`, and `before`, `after` when `ok`. Never the note.

## Data and migration

New: `context` events (one per model call, about 90 bytes each) and, with the switch on, `compact` events in
`.vibe/runs/<run>.jsonl`; `compact` in `config.json:vibe_agent` (a bool, default false). Old logs have neither and
the page draws nothing for them. Nothing else changes on disk.

## Tests

Fake model only, no network (`tests/test_vibe_agent.py`, `tests/test_vibe_agent_page.py`,
`tests/test_vibe_agent_eval.py`):

- The meter: one event per call; a call without usage reports the estimate times 1.4, marked, with cap
  `max_context_tokens` times 1.4; a call with `prompt_tokens` reports that count with the cap scaled by count over
  estimate; both in the log. Page: numbers, mark, high state, 100 percent ceiling, bad input, `role="meter"`,
  `textContent`.
- A 45-step script (a list with a todo list, 42 reads, a read_output of step 1, finish) with limits 100 steps and
  3600 s. The test lowers `max_context_tokens` until the script with compaction off ends `limit` with the context
  message by step 24 (2,500 today). Off: no summary call, no compact event, no folded text in any prompt. On, same
  cap: ends `done` after 45 steps with two folds; each note at most 600 characters; the goal message and the todo
  list unchanged in every later prompt; call and result pairs side by side and numbered on from the fold;
  the note's block is exactly `wrap_result` of it; read_output of the folded step 1 works; the summary calls'
  tokens are in the run's count; audit lines carry counts only.
- A note that tries to close its block and speak as the owner is escaped and marked; the owner's real note on a
  folded step is in the prompt word for word; a 900-character note is cut to 600.
- A failing summary call: the run ends as with compaction off, no compact event, audit `ok` false, at most one call
  per 6 steps. A stop during the summary call ends the run `stopped`. The switch is off by default and only a real
  `true` turns it on.
- Eval task 14: outside the bar, SKIPPED with no flag, its check on good, bad and footer-broken files.

## Rollback

`config.json` `vibe_agent.compact: false` (the default) gives today's behaviour: `_fit` calls `_messages()` exactly
as before and no summary call is made (tested). The meter is display only. Or revert the PR: old pages and old code
ignore `context` and `compact` events.
