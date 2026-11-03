# Chat reply parity: one post-reply helper (P-099)

Approved by the owner's "go" of 3 Nov 2026 for the 25 Oct audit plan.

## What

`routes/chat.py:chat_completion` answers in two ways: an SSE stream (`stream: true`, what
the Chat page sends) and one JSON reply (`stream: false`, API callers). The checks that run
once the reply is written were copied into both, and the copies drifted:

| Rule after the reply | stream:true | stream:false before | stream:false after |
|---|---|---|---|
| Claim check (`codec_claim_check`) | yes | yes (second copy) | yes, same helper |
| Premise check (`codec_premise_check`) | yes | yes (second copy) | yes, same helper |
| Project suggestion (`escalate_project`, Step 10 Q11) | yes | **no** | yes |
| Repetition cut (`_degenerate_tail`) | yes | **no** | yes |
| Step budget on a `[SKILL:...]` tag | yes | yes | unchanged |

This change writes the rules once:

- `_post_reply_checks(reply, actions, last_user_text, messages, session_id, stream)` runs the
  Project suggestion, the claim check and the premise check, writes their audit lines and
  returns `(suggestion or None, [notes in order])`. It never raises. It blocks (the suggestion
  may ask the local model), so the JSON path calls it with `asyncio.to_thread`; the stream
  generator already runs in Starlette's thread pool.
- `_cut_repetition(text)` is the JSON path's repetition cut: it checks the text the way the
  stream checks its deltas (every 160 characters, about the stream's 40 deltas) and cuts at the
  first point `_degenerate_tail` fires. `_LOOP_NOTE` is the one wording of the note both paths
  append.

The two paths keep their own reply plumbing: the stream frames tokens through
`SkillTagBuffer`; the JSON path resolves its tag with a regex.

## Why

Every cross-cutting rule had to be added twice, and two were already missing from the JSON
path (BH-5 in the 25 Oct audit). One helper means the next rule (P-097 and later) is added
once.

## API and data changes

- The SSE frames do not change: same keys, same order (`escalate_project`, then the claim
  note, then the premise note as `token` frames, then `[DONE]`).
- The JSON reply gains `escalate_project` (`{"estimated_checkpoints", "reason"}`) when the
  Project suggestion fires, the same object the stream sends. A task-shaped message (24+
  characters and an action verb) now waits for the classifier (local model, 15 s timeout)
  before the JSON reply returns. No page in the repo uses `stream: false`.
- A JSON reply caught in a repetition loop is cut where the loop is found and ends with the
  same note the stream shows.
- Audit lines are unchanged: `unbacked_claim_corrected` and `premise_misattribution_flagged`
  keep `transport: "stream"` and the "(stream)" message on the stream path only, as before.
  No new event, no envelope change, no memory.db change.

## Migration

None. Takes effect when the owner restarts `codec-dashboard`.

## Test plan

`tests/test_chat_reply_parity.py`, in process (FastAPI TestClient), with the model, the
escalation gate, the skill run and every `~/.codec` read stubbed or in `tmp_path`:

1. One fake reply with a `[SKILL:time:...]` tag, an unbacked "saved to your Desktop" claim and
   a premise misattribution in the user messages, sent with `stream: true` and `stream: false`:
   the same full reply text (tag resolved, claim note, premise note), the same
   `escalate_project`, the same audit lines, the same step-budget consumption.
2. The same reply with the step budget spent: the tag is dropped and the skill does not run,
   on both paths.
3. A reply stuck in a loop: both paths cut it and end with `_LOOP_NOTE`; an ordinary long
   reply is not cut.

Plus the existing chat tests, and the stream wiring test in `tests/test_claim_check_stream.py`
updated to follow the check into the helper.

## Rollback

`git revert` of the PR, then restart `codec-dashboard`; the two copies come back and
`stream: false` loses the suggestion and the cut again.

## Left as they are (reported, not changed here)

- A disallowed `[SKILL:...]` tag uses one step on the stream path and none on the JSON path.
- On the JSON path a tag whose skill fails or returns nothing stays visible; the stream drops it.
- The JSON path resolves only the first tag; the stream resolves every tag.
