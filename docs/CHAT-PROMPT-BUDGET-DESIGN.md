# Deep Chat prompt budget (P-034)

## What

`/api/chat` (routes/chat.py) keeps what it sends to the model each turn under
`config.json:chat_prompt_token_budget` (default 12,000 estimated tokens). The
page (codec_chat.html) says under a reply when that reply was given a shortened
chat. Nothing else changes: the page still sends its whole history, the server
trims one copy per turn and stores nothing.

## Why

Deep Chat resends the whole history every turn, and every attached file up to
80,000 characters (about 23k tokens) inside the user message that carried it.
The local server does not reuse a prompt start between turns, so each turn reads
everything again. Long chats and chats with a PDF got slower each turn and then
hit the 300 s timeout.

Prefill measured on the running model on 3 Nov 2026 (mlx_vlm.server,
Qwen3.6-35B-A3B 4-bit, 127.0.0.1:8083, thinking off, max_tokens 1, nothing
else running): 5,692 tokens in 6.9 s, 8,516 in 9.6 s, 12,029 in 14.7 s, so
820-890 tokens/s once warm (830 tokens took 1.9 s). The 134 tok/s in the old log
was not the steady rate; the ~850 tok/s in the speed note is. At that rate a
12,000-token prompt costs about 15 s before the first word, which is why the
default stays at 12,000.

## Decision: how an attached document is kept after the first reply

Agent's call under Mickael's "go go build i trust ur judgment here" (3 Nov 2026),
reversible: **full text once, then a bounded excerpt chosen by relevance to the
latest question.** The other two options were a fixed head (loses anything past
the first pages, such as a fact on page 30) and asking the owner to attach the
file again (honest but makes every follow-up question cost a re-attach). The
excerpt keeps a fact anywhere in the file reachable for about a third of the
budget. To be appended to `~/.claude/DECISIONS.md` by the parent session.

## How a turn is fitted (`_fit_prompt_budget`)

One estimator, `estimate_prompt_tokens(messages)`: 3.5 characters a token plus
4 a message (the same rule as the Vibe agent; 80,000 characters = 22,862). It is
used for the cap and by the tests. It overestimates English prose a little
(measured 4.9 characters a token on the live tokenizer), so the real prompt is
under the budget too.

1. Whole prompt (system prompt, this turn's memory/link/search context, history,
   latest message) at or under the budget: sent unchanged.
2. Otherwise the latest user message, the system message and this turn's
   `(INTERNAL CONTEXT ...)` message are kept as they are.
3. Every `[DOCUMENT: name] ... [END DOCUMENT]` or `[IMAGE ANALYSIS of name] ...
   [END IMAGE]` block in an older message is replaced in that message by a
   one-line marker, and one excerpt message is placed just before the latest
   message. The excerpt: the file is cut into 1,500-character windows (1,200
   apart, so a sentence on a boundary is whole in one of them), each window is
   scored against the words of the latest message (stop words dropped, simple
   plural/-ed/-ing endings removed, rarer words weigh more), the best windows are
   taken until the excerpt is full, then the start of each file if room is left,
   and they are written in file order with `[...]` between gaps. The excerpt gets
   at most a third of the budget and never more than the room left after step 2.
   It tells the model these are only parts of the file and to say so rather than
   guess when the answer is not in them. With no room at all it is one line per
   file saying the file was not sent this turn. A file name may contain `]`
   ("Invoice [2026].pdf"): the name runs to the first `]` at the end of its line.
4. Older turns (a user message and the replies after it) are added back newest
   first while the total stays under the budget; the oldest are left out first,
   and a gap is never skipped over.
5. **First attachment turn:** the attachment blocks in the latest message are
   not counted, so that turn may go over the budget once, by the size of the new
   file, and the history around it is still trimmed to the budget.
6. If the kept parts alone are over the budget (a very long pasted message, a
   huge system prompt), everything that can go has gone and the prompt is sent
   as it is; the log says so.

Small documents and short chats are untouched: nothing changes until the whole
prompt is over the budget.

## API and data

- `config.json:chat_prompt_token_budget`: integer, default 12,000; 0 or below
  turns the cap off; values under 2,000 count as 2,000; anything else invalid
  means the default. Read per request, no restart.
- Streamed replies: one `data: {"trim": {...}}` frame before the tokens when the
  turn was shortened (`budget`, `tokens`, `dropped`, `excerpts`: files with a
  part in the excerpt, `not_sent`: earlier files with no room left, `over`); the
  non-streamed reply gets the same dict under `trim`. File names go only to the
  page that sent them, never into a log line (the log has counts only).
- The page shows "Shortened to fit: N earlier messages left out, excerpt of
  name" (and "name not sent" for a file with no room) in the reply's meta line (textContent), with a tooltip saying how to get
  the whole file read again.
- No stored state, no new audit event, no schema change. The premise check and
  the attachment detection keep reading the untrimmed messages.

## Scope

routes/chat.py and codec_chat.html only. The launcher's prompt-cache option in
`scripts/start_model_server.sh` is a section-10 zone and is not touched. The
Vibe rollback path that posts to `/api/chat` gets the same cap.

## Test plan

`tests/test_chat_prompt_budget.py`: an 80,000-character attachment with a
planted fact after character 70,000 and 30 turns, through `POST /api/chat` with
the model replaced by a fake that records what it was sent: at turn 30 the sent
prompt is at or under the budget by `estimate_prompt_tokens`, the system prompt
is first, the question last, the planted fact is in the excerpt and the whole
file is not; the attachment turn sends the whole file and may go over; a short
chat is sent unchanged; the config knob (default, off, floor, invalid); the page
reads the frame on both stream paths and draws it with textContent.
`tests/test_memory_page.py` gains one pin: the fit runs after the system prompt
and the memory context are built. Live check (scratch, not committed): the same
fixture through the real route to the local model on 127.0.0.1, answer must name
the planted fact.

## Rollback

Revert the PR, or set `chat_prompt_token_budget` to 0 in `~/.codec/config.json`
(read per request). Nothing is stored, so there is nothing to migrate back.
