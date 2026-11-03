# Deep Chat notes on the earlier part of a long chat (P-038)

## What

When a Deep Chat is longer than `config.json:chat_prompt_token_budget` (P-034,
default 12,000 estimated tokens), the turns P-034 leaves out are no longer lost:
CODEC writes notes on them with the local model, in the background after a
reply, and later turns send those notes right after the system prompt, followed
by as many of the newest turns as fit. New module `codec_chat_summary.py`;
`codec_compaction.py` (the Terminal Session's summariser) is not touched.

**Chat only.** Voice keeps its 20-turn trim (decision in `~/.claude/DECISIONS.md`,
2026-11-04: a summary call on the one local model would delay a voice turn).

## Why

P-034 made long chats fast again by leaving the oldest turns out. A long chat
then forgets its start: a name, a price or a decision from turn 5 is gone by
turn 40. Notes keep those facts in a few hundred words.

## How a turn is sent (`codec_chat_summary.fit_with_notes`)

1. P-034's `_fit_prompt_budget` runs exactly as today. If it left no message
   out, the turn is sent as it fits; nothing else happens on this turn.
2. If it left messages out and notes exist for the start of this exact history,
   one notes message goes right after the system message, and the fit runs
   again on that list. The fit keeps the notes message with the system message
   (one 3-line addition in `_fit_prompt_budget`) and fills the rest of the
   budget with the newest turns, as before. Attached files keep P-034's excerpt.
3. The newest turns may repeat what the notes cover (they fill the budget, as
   P-034 does); the notes say that where they differ, the messages are right.
4. The reply's `trim` frame gains `summarised` (messages left out that the notes
   cover); `dropped` is now only the messages left out with no notes. The page
   says "Shortened to fit: 40 earlier messages summarised".

The route runs `fit_with_notes` in a worker thread: with planning it runs the
fit up to four times, which takes about 250 ms with a 1 MB file, too long for
the dashboard's event loop (P-016).

The prompt is never bigger than P-034's budget, so the time before the first
word does not grow with the chat.

## How notes are written (`NotesJob`)

- **When:** planned at the turn's fit, started by `_post_reply_checks` after the
  reply is finished (both paths, P-099), in one background thread. A job is
  planned when P-034's fit at 85% of the budget would leave out messages the
  notes do not cover yet, so notes exist before the chat goes over the budget.
  It covers the turns the fit would leave out at 60% of the budget, so the next
  job comes about every 8 turns of a typical chat.
- **How:** whole turns, at most about 3,000 estimated tokens a call; the notes
  so far are given for context and the model writes notes on the new part only
  (one line per fact, names, numbers, dates, decisions; at most about 300
  words). Attached files are reduced to "[file attached: name]" (P-034's excerpt
  handles their text). When the notes pass a quarter of the budget, one more call
  rewrites them shorter: it is told their length and a target of about 60% of
  that quarter, and how to get there (one line per subject, a changed value kept
  only in its later form, drafts reduced to what they were for). A merge reply
  cut by the token cap, or not shorter, is not used (a cut one would lose the
  newest notes). Notes still over twice that quarter after a merge are not
  kept and the job stops, so notes never grow past half the budget (P-034
  never leaves out the notes message). Thinking off, temperature 0.2, 240 s cap.
- **Where kept:** in the dashboard's memory only, keyed by a SHA-256 of the
  exact messages covered (role and text, in order). Newest 64 entries. A
  dashboard restart forgets them; the next long turn writes them again. Editing
  an earlier message changes the key, so stale notes are never used. Nothing is
  written to disk and a stored chat is never changed.
- **Which model:** only the active model when its URL is on this Mac
  (`codec_cloud_models.is_local_url`) and no persona model is active. On a
  cloud model or a persona, existing notes are used but none are written.
- One job at a time; a second one is skipped and the next turn plans again.

## The voice rule (done-when 3)

Whether the model server stops work when a client disconnects is not measured.
So the rule written down and enforced is:
**a notes call starts only when no voice session is open**
(`~/.codec/voice_session.json` absent), checked before every call. While a call is streaming, the marker is checked between tokens
and the call is dropped if it appears (best effort: during the prompt read no
token arrives). With a voice session open, the turn is sent as P-034 sends it.
A marker left behind by a crashed voice process stops notes until it is gone.

## API and data

- `config.json:chat_summary`: default on. `false` (or `0`, `"off"`, `"no"`)
  turns it off: the turn is sent exactly as P-034 sends it and no job runs.
  Read per request, no restart. Default on because it changes nothing until a
  chat is already over the budget, never runs during a voice call, runs only on
  the local model after the reply, and the recall eval passed (below).
- `trim` frame: new key `summarised` (count). No new audit event, no schema
  change, no stored state. Log lines carry counts only, never chat text.

## Test plan

- `tests/test_chat_summary.py` (fake model): notes are used after the system
  message and the sent prompt stays within the budget; switch off sends what
  P-034 sends and plans nothing; a job is planned only near the budget, on a
  local URL, without a persona; a job writes notes the next turn uses, with no
  attachment text in its input; an open voice session stops a job before any
  call and during a call; an edited earlier message gets no notes; notes over
  the cap are merged; the route's `trim` frame and job start; the page note.
- `tools/chat_summary_eval.py` (live local model, not CI): the made-up 60-turn
  fixture `tools/chat_summary_fixture.json` with 20 questions, replayed turn by
  turn through the real fit and notes code. Bar: at least 18 of 20 answered
  from notes plus recent turns, and prefill time (from the model-server log,
  counts and timings only) of every turn after 30 within 1.2x of turn 30. Runs
  only with no voice call and no image job, and never on a cloud URL.

## Results (4 Nov 2026, Qwen3.6-35B-A3B-4bit on the Mac Studio)

Three full runs of `tools/chat_summary_eval.py`:

| Run | Code | Recall with notes | From notes only | Without notes | Prefill turn 30 | Worst after 30 | Ratio |
|-----|------|-------------------|-----------------|---------------|-----------------|----------------|-------|
| 1 | before the fixes below | 20/20 | 15/15 | 10/20 | 10.107 s | 10.821 s (turn 50) | 1.071 |
| 2 | b8f2096 | 19/20 | 15/16 | 10/20 | 10.010 s | 10.852 s (turn 56) | 1.084 |
| 3 | ea5f52c (final) | 20/20 | 15/15 | 10/20 | 10.039 s | 10.722 s (turn 36) | 1.068 |

All 30 turns after turn 30 were measured in each run, with no other request on
the server. A notes job took 9 to 15 s in runs 1 and 3. In run 2 the miss was
the changed date: the model gave the old value from the notes. Two fixes came
from that run. The notes message now says a later note replaces an earlier one.
The merge prompt was rewritten: the old one took 1,518 words of notes only to
1,450 (target 1,050). The 60-turn replay seldom reaches the merge, so it was
tried on its own. The new prompt took the same 1,518 words to 1,181 in 28 s.
All 17 questions those notes cover were still answered from the merged notes.

## Rollback

Set `"chat_summary": false` in `~/.codec/config.json` (read per request), or
revert the PR. Nothing is stored, so nothing needs undoing.
