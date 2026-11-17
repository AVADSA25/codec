# Chat and wake word: a trigger phrase inside a long statement goes to the model (S-08, 2026-11-17)

Owner approval, decision board card S-08 (confirmed 17 Nov 2026): the chat and the
wake word follow the voice-call rule (#598, docs/VOICE-SKILL-MATCH-DESIGN.md): a
trigger phrase counts only in a message of at most 15 words, or when it opens a
longer one. Questions like "what was I doing?" still work.

## What was wrong (known issue 2026-11-12)
The voice call stopped firing a skill on a phrase met in the middle of a long
statement (#598). The chat's pre-LLM match (`routes/chat._try_skill`) and the wake
word (`codec._dispatch_inner` -> `codec_dispatch.check_skill`) did not: a long chat
message or spoken sentence that only mentioned "search for", "remind me to" or
"timer for" ran that skill. In the chat an allowlisted match is honoured even when
`_is_conversational` says conversation (on purpose, for "what was I doing?"), and
the wake word had no statement check at all.

## Change
One rule, one helper: `codec_voice_filters.command_parts(text, LONG_MESSAGE_WORDS)`
(`codec_chat_pipeline.LONG_MESSAGE_WORDS` = 15), the function the voice call already
uses. It is imported, never copied.

- `codec_dispatch.check_skill(task, commands_only=False)`. With `commands_only=True`
  the matches come from the parts `command_parts` gives: every sentence of at most 15
  words, where a trigger may be anywhere, and every longer sentence with its lead-ins
  ("hey codec", "please", "could you", "I want you to" ...) dropped, where the trigger
  must open it. Each part goes through `registry.match_all_triggers(part,
  at_start=...)` (whole words, custom triggers, as before); the names are kept in
  part order without repeats, and switched-off skills are dropped as before. The
  default `False` is the old call, byte for byte, for every other caller.
- `routes/chat._try_skill` calls `check_skill(user_text, commands_only=True)`.
  Everything after the match is unchanged: the 2,000-character cap, the R-01 / S-07
  search switch, the allowlist, the `_is_conversational` fall-through, temporary
  chat, the consent gate and the time limit. Home's Flash box (`/api/command`) shares
  `_try_skill`, so it follows the same rule.
- `codec._dispatch_inner` calls `check_skill(task, commands_only=True)`. The
  500-character cap and `run_skill(..., consent=_wake_consent)` are unchanged, so the
  wake word's consent gate stays.

Differences from the voice call, kept on purpose: the chat and the wake word still
count one-word triggers ("weather", "calculate"); the voice call keeps `min_words=2`
and its skip list. Neither changes here.

## "What was I doing?"
A short question (15 words or fewer) is one part where the trigger may be anywhere,
so "what was I doing?", "what was I doing 20 minutes ago?" and "hey, what was I
working on this morning?" match observer_recall as before, and `_try_skill` still
honours an allowlisted match on a question. A longer question still works when it
opens with the phrase ("What was I doing an hour ago, before the call with the bank
about the mortgage papers?"). A longer one with the phrase at the end now goes to the
model.

## Not changed
- The '@' pick (`_try_explicit_skill`), slash commands and the model's `[SKILL:]`
  tags: no trigger matching, the pick or the tag decides.
- The voice call (already on this rule), the terminal session (`codec_session`), the
  Telegram and iMessage bridges, MCP, crews, the agent runner and the Today tiles.

## What it does not catch (both ways, as on the voice call)
A short statement (15 words or fewer) that holds a whole trigger phrase still fires
("I set a timer for the eggs this morning"); a long sentence that opens with a
one-word trigger still fires; a long run-on sentence whose command comes at the end,
with no full stop before it, now goes to the model. With several sentences, the first
sentence's matches come first; a trigger that runs across a full stop no longer
matches (no built-in trigger holds one). Telling these apart needs the model, not a
word list.

## Tests
`tests/test_chat_wake_skill_match.py`, on the real built-in skill triggers (a
registry over `skills/`, custom triggers in tmp_path, no skill code imported or run):

- `check_skill(commands_only=True)`: long statements with "search for", "remind me
  to" and "timer for" in the middle match nothing; the same phrases opening a long
  message (also after "hey codec, could you") or in a short one match their skill;
  "what was I doing?" and three other questions match observer_recall; a long
  question that ends with the phrase matches nothing; a short command after a long
  sentence still matches; a switched-off skill never matches; the default call still
  matches the long statements (control).
- the chat (`_try_skill`): a long statement runs no skill; a short command and a long
  message that opens with one run; "what was I doing?" runs observer_recall.
- the wake word (`codec._dispatch_inner`): a long statement runs no skill; a short
  command and a long one that opens with the phrase run, through the consent gate.

Fakes of `check_skill` in older tests take the new keyword.

## Rollback
Revert the PR. No stored state, config key, schema or skill file changes.
