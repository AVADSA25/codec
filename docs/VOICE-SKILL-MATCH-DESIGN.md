# Voice call: a sentence that only contains a trigger phrase goes to the model

Owner's request, 12 Nov 2026: "add to list to fix". Fixed in one PR.

## What happened

In a live voice call on 11 Nov 2026 the owner said one long opinion sentence (more than
20 words) that had the words "to do" in the middle of it. The voice pipeline logged
`→ skill: google_tasks`, ran it and read his whole Google task list aloud instead of
answering. The model's next turn kept talking about the tasks, because the skill
exchange was in the history.

When he hung up, the log also showed
`Pipeline error: RuntimeError: Cannot call "send" once a close message has been sent.`
and the same line as `Session error`.

## Cause

1. `VoicePipeline._match_skill` (codec_voice.py) matched every trigger of two words or
   more as a raw substring of the whole sentence, with no word boundaries and no check
   that the sentence was a request. google_tasks has the bare triggers `"to do"` and
   `"todo"`, so "nothing to do" or "how to do it" anywhere in anything the owner said
   fired it.
2. The chat path is not safe from this either. `routes/chat.py:_try_skill` uses the
   registry's word-boundary matching (`match_all_triggers`, which also reads
   `~/.codec/custom_triggers.json`) and `_is_conversational`, but an allowlisted match
   is honoured even when the message is conversational, and google_tasks is
   allowlisted. Word boundaries do not help: "to do" is two whole words. The wake word
   (`codec._dispatch_inner` → `codec_dispatch.check_skill`) has no conversational
   check at all. The same sentence fires google_tasks on all three paths.
3. Hang-up: the page sends `end_call`, stops its audio and closes the socket. The
   receiver task returned, but the turn still running in `_pipeline` kept generating
   and sending with raw `self.ws.send_json`. The first failed send came from the
   voice 02 worker (already guarded, it marked the socket dead); the next raw send in
   the pipeline raised, and the raw send in its `finally` raised again, out of the
   task, which ended the `gather` in `run()` with an error.

## Fix

- **One matcher for the three paths.** The voice call now matches through its
  registry's `match_all_triggers` (whole words, custom triggers), like the chat and the
  wake word. Two keyword arguments, defaults unchanged for the other callers:
  `min_words=2` keeps the voice rule that only phrases fire, `at_start=True` counts a
  trigger only when the text opens with it. Triggers are compared in lower case, as
  voice always did (before, a custom trigger written with a capital never matched the
  chat or the wake word).
- **Where a phrase counts (voice).** `codec_voice_filters.command_parts` splits the
  turn into sentences (Whisper ends each with `.`, `?` or `!`). In a sentence of at
  most 15 words (`codec_chat_pipeline.LONG_MESSAGE_WORDS`, the chat's own "long
  messages are almost always conversational" number) a trigger counts anywhere, as
  before. In a longer sentence it counts only when it opens the sentence, after
  lead-ins such as "hey codec", "please", "could you", "I want you to". So a long
  command still works ("add a task to call the plumber about the sink before the
  weekend ..."), a short command followed by context still works ("what's on my to do
  list? I want to plan ..."), and a phrase in the middle of a long statement goes to
  the model.
- **Why not the chat's whole `_is_conversational` guard.** Whisper ends every spoken
  question with "?", which alone makes it say "conversational" ("what's on my to do
  list?"), and its phrase list flags common spoken commands ("can you check my
  calendar", "read the latest news", "please check my reminders"). Applied to voice it
  would send those to the model, which cannot run skills in the default mode. Only its
  length rule is reused.
- **google_tasks triggers.** The bare `"to do"` and `"todo"` are replaced by the list
  phrases `"to do list"`, `"to-do list"`, `"todo list"`, `"my to do"`, `"my to-do"`,
  `"my todo"`. This fixes the incident's trigger on the chat and wake word paths as
  well. A bare "todo" message in the chat no longer opens the task list.
- **weather triggers.** Voice never fired the weather skill, because all its triggers
  but "how hot", "how cold" and "is it raining" are single words. Added
  `"what's the weather"`, `"what is the weather"`, `"how's the weather"`,
  `"how is the weather"`, `"weather today"`, `"weather tomorrow"`,
  `"weather forecast"`. The chat and the wake word already matched these through
  "weather", so only voice changes. Manifest regenerated.
- **Kept:** the voice-only skip list (`_VOICE_SKIP_SKILLS`) and the skill switches
  (`codec_skill_switches.is_off`).
- **Hang-up.** Every send in `_pipeline` (and the transcript chunk in `_say_sentence`)
  goes through `_safe_send_json`; the loop stops when the socket is marked dead; on
  `end_call` the receiver marks the socket dead and sets `interrupted`, so the running
  turn stops and nothing more is sent.

What it does not catch, both ways: a short statement (15 words or fewer) that contains
a whole trigger phrase still fires, on voice as in the chat ("the egg timer for the
kitchen broke"); and a long run-on sentence whose command comes at the end, with no
full stop before it, now goes to the model instead of the skill. Telling those apart
needs the model, not a word list (in Think mode the model already picks the tools).

## The lost first utterance

At 21:13:10 the call's first utterance was transcribed (whisper answered 200) but no
`User:` line followed and it was not saved. `transcribe()` returned an empty string:
it drops a transcript that is a noise word ("okay", "yeah", "hello" ...), a known
Whisper hallucination ("thank you", "you", "so" ...), a repeated fragment, or fewer
than two words. Each of these logs at debug level only, and the whisper server does not
log text, so the log cannot say which one. It came 9 s after the greeting, which fits
a short "okay" / "hello" or a Whisper artefact from room noise. This is the filters
working as designed, not a bug; not changed here.

## Tests

`tests/test_voice_skill_match.py`, on the real built-in skill triggers (skill code is
never imported: `load` is faked):

- made-up opinion sentences with "to do", "remind", "timer", "weather" ("how hot") and
  "search" / "look up" fire no skill by voice;
- "what's on my to do list", "what's on my to-do list", "add a task buy milk", "set a
  timer for five minutes", "what's the weather", "remind me to ...", "search for ...",
  a long command, a long command after "hey codec, could you", and a short command
  followed by a long sentence still fire;
- the chat and wake word matcher no longer maps an opinion with "to do" to google_tasks;
- the skip list, a switched-off skill and a custom trigger (written with capitals) are
  honoured;
- `end_call` during a reply: nothing is sent after it, no `Pipeline error`, the pipeline
  returns; a socket that dies mid-turn: one failed send, then nothing, and queued turns
  are not run.

`tests/test_skills_page.py`: the voice fake registry gains `match_all_triggers`.

## Rollback

Revert the PR (code, triggers and `skills/.manifest.json` together; the manifest must
match the skill files or the skills load through the AST gate instead). Nothing is
stored or migrated.
