# P-026 — Chat and voice know the buyer: one identity prompt

## Why

Deep Chat told every buyer it runs on a Mac Studio M1 Ultra with 56 skills: the page
(`codec_chat.html`) sent its own typed system message, which the server glued under its own
prompt, so each turn paid for two identities (1,978 tokens, 795 of them the stale page block).
Deep Chat never read About me or the saved facts, which voice and the wake word read. And a
brace in the Settings chat prompt (`{` in a JSON example) broke every chat, because the server
ran that prompt through `str.format`.

## What

- **One runtime function.** New module `codec_runtime_context.py`. `gather()` returns this Mac's
  hardware (model from `ioreg`, chip and memory from `sysctl`; no serial numbers; read once per
  process; "" on Linux or a failed probe), the number of skills the registry lists that are
  switched on, the About me text (`codec_memory_upgrade.load_identity`) and the active facts
  (`codec_memory_upgrade.query_valid_facts`, newest first, 20 like today). `chat_block()` renders
  Deep Chat's block from it. Nothing in the module writes anything.
- **Deep Chat** (`routes/chat.py:_build_chat_system_prompt`): the base prompt (Settings override or
  `CHAT_SYSTEM_PROMPT`) gets its `{date}` with `.replace`, as the persona branch already did, then
  the runtime block is appended right after it: hardware, skill count, About me (capped at the
  Settings limit, 1,500 characters) and the facts (one line at most 300 characters, the block at
  most 1,600 characters, about 400 tokens). Persona models are left as they are: they own the turn
  and speak only as themselves. A temporary chat (P2.11) gets About me (the owner's instructions,
  like standing rules) but no facts (memory recall). The reply's "Memory used" lists the About me
  note and the number of facts.
- **Source filter.** `query_valid_facts` gets one new parameter, `exclude_sources` (default none,
  so every other caller is unchanged). Deep Chat passes `mcp` and `untrusted` (facts a remote
  caller can write: `memory_save` is MCP-exposed and tags every save `mcp`) and `daybreak` (open
  threads, which Deep Chat already gets in their own Daybreak block). Voice and the wake word read
  About me and facts through the same `gather()` with no filter, so their prompts are byte for byte
  what they were until the memory-origin item (P-037) maps origins.
- **One identity block.** The page's typed `SYS` block is deleted. The page sends a system message
  only when Think is off, and then only the concise-answer directive; the server prompt is the one
  identity. The page's "Never use emoji" rule moves into the single prompt: the chat-mode format
  line of `CODEC_CHAT_PROMPT` now says "No emoji" instead of allowing one (outside the
  `CODEC_IDENTITY` operating principles, which are not edited). The dashboard addon's
  "User-specific context" section now says where About me, facts and standing rules arrive.
- Settings > Memory > About me says that chat reads it too.

## API and data

`codec_memory_upgrade.query_valid_facts(..., exclude_sources=())`: SQL `COALESCE(source,'') NOT IN
(?, ...)`, values bound. No schema change, no stored value changes, no new audit event. The Settings
prompt override and About me are only read.

## Measured (Studio, the model's own tokenizer)

Before, from the base text: server prompt 1,182 + page block 795 = 1,978 tokens stacked per turn.
After, the identity part of the system message (server prompt, dashboard addon, this Mac, About me,
facts block) taken from a stubbed `codec_llm.call` on a real `/api/chat` run with the Studio's own
About me and facts: 1,560 tokens (1,589 with the page's concise-answer line). On the Studio 8 of
the 9 active facts carry source `mcp`, so Deep Chat shows 1 fact until P-037 offers Promote; voice
and the wake word still inject all 9. The skill count in the prompt is 91, the skill files on disk.

## Test plan

`tests/test_buyer_identity.py`: with `gather` stubbed, the Deep Chat prompt and the voice prompt
both carry the stub's About me and facts; a Settings prompt with `{` and `}` returns a normal reply
through `/api/chat` (500 before); a temporary memory.db with one fact per source value: the `mcp`,
`untrusted` and `daybreak` facts are absent from Deep Chat and present in the voice and wake
prompts, which keep their exact blocks; the hardware string comes from the probes; the skill count
from the registry; the page has no typed identity, hardware or skill count. `tests/conftest.py`
points memory.db (`codec_config`, `codec_memory_upgrade`, `routes._shared`), About me and standing
rules at temp files so no test reads or writes the owner's: before, every unmocked chat prompt
build opened the owner's memory.db through Daybreak and counted a standing-rule injection.
Negative control: the new tests fail on the base commit.

## Rollback

Revert the PR. Nothing stored is changed; a page opened before the deploy keeps sending its old
block until reloaded (stacked as before, for that tab only).
