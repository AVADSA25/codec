# Untrusted text fence and memory-write origin (P-021)

Status: built in `elev/p021-untrusted-fence` (Nov 2026). Owner approval for the
design gate came with the elevation build request.

## What

1. **One fence for text from outside.** `codec_fence.fence(text, what)` wraps text
   in a fresh random marker and one line that says the text is data, not
   instructions. Chat-template tokens (`<|im_start|>`, `[INST]`, `<<SYS>>`,
   `<start_of_turn>`, `<think>`, `<tool_call>` and similar) are removed from the
   text first, and anything that looks like our own marker is defused. Used at
   three places:
   - every crew tool result (`codec_agents.Agent.run`, the "Tool result from"
     message);
   - the `web_fetch` skill's page text (chat, MCP, crews, clipboard_url_fetch);
   - the chat's URL fetch (`routes/chat.py`, inside the `[URL CONTENT]` block).
2. **An origin for every agent-reachable memory write.** The existing
   `facts.source` column (no schema change) now gets one of five values:

   | Value | Meaning |
   |---|---|
   | `owner` | the owner's own words: chat, voice, the Memory page |
   | `derived` | extracted by the model from the owner's own local chat or voice text (`fact_extract`, nightly `auto_memorize`, a model-written skill tag) |
   | `system` | written by CODEC itself (reserved; no writer in this item) |
   | `agent` | a crew tool call, a scheduled job (`CODEC Schedule`, autopilot), a Project-mode agent (`agent:<id>`) |
   | `untrusted` | any write made while an MCP call runs, and any write the scan flags |

   `codec_fence.memory_origin(skill, text, default, app)` decides it. `memory_save`,
   `fact_extract`, `thread_note` (through `codec_daybreak.save_thread`) and the
   "set fact" path of `memory_history` use it. The matching `conversations` row
   (memory_save, fact_extract) gets role `untrusted` instead of `fact`, because that
   table has no source column.
3. **A bounded scan.** `codec_fence.scan(text)` returns the names of the patterns
   that matched. Input is cut to 65,536 characters before and after Unicode
   normalisation (NFKC), invisible format characters are dropped, and Unicode "tag"
   characters (invisible ASCII) are turned back into ASCII so hidden text is read.
   The 15 patterns are precompiled at import and use only bounded gaps, so a
   65,536-character hostile input is scanned in well under a second. The patterns
   and the marker wording are our own.

This item writes the origin. Filtering on it (keeping `untrusted` out of prompts
until the owner promotes it, mapping the older values `mcp`, `fact_extract`,
`daybreak`, `skill:memory_history`, `memory page`) is the memory-origin item
(P-037). No approval queue is built here.

## Why

Text fetched from the web or sent by a remote caller could steer a crew (a tool
result was pasted raw into a user turn) and could persist in memory with no mark
(memory_save over MCP, fact_extract every night). Markers reduce injection, they do
not stop it; the origin lets later code keep such text out of prompts.

## API and data changes

- New module `codec_fence.py` (stdlib only): `fence`, `strip_template_tokens`,
  `scan`, `memory_origin`, `write_origin`, `caller(kind)` (a context manager that
  marks the running call as `"mcp"` or `"agent"`; `"mcp"` is never replaced by an
  inner `"agent"`), `from_mcp()`, constants `OWNER DERIVED SYSTEM AGENT UNTRUSTED`,
  `ORIGINS`, `SCAN_MAX_CHARS`.
- `codec_mcp.py`: the skill call runs inside `codec_fence.caller("mcp")`. In the
  `codec-mcp-http` process `CODEC_MCP_TRANSPORT=http` also counts as MCP, so a
  skill that starts its own thread is still covered there.
- `codec_agents.py`: crew tool calls run inside `codec_fence.caller("agent")`; the
  tool result is fenced after the `tool_result` audit and before the stuck banner,
  so stuck warnings and the owner's own escalation answer stay outside the fence.
  CODEC's own timeout and veto messages are not fenced (a private `str` subclass
  tells them apart; page text cannot forge it).
- `codec_autopilot.py`: the rule's skill call runs inside `caller("agent")`.
- `codec_daybreak.save_thread(kind, text, user_id="default", source="daybreak")`:
  new keyword, default unchanged.
- Same return strings everywhere: an MCP `memory_save` still answers "Saved to
  memory", so claude.ai sees no change. The web_fetch result now carries the fence
  lines, over MCP too.
- New audit event `memory_write_untrusted` (source `codec-fence`; `extra`: skill,
  reason `mcp` / `scan` / `mcp+scan`, pattern names, character count; never the
  text). Level `warning` on a scan hit, `info` for a plain MCP write. The audit
  envelope is unchanged.
- Kill switches: `FENCE_ENABLED=false` returns text unfenced (old prompts);
  `MEMORY_SCAN_ENABLED=false` makes `scan` return no hits (MCP writes stay
  `untrusted`).

Not changed: `codec_agent_runner.py` (its `agent:<id>` app value already gives
`agent`), `_HTTP_CONSENT_REQUIRED` (adding memory_save and thread_note there needs
the owner's word), the memory.db schema, the Memory page writer, prompt readers.

## Migration

None. Old rows keep their old source values. New writes use the five values.

## Test plan

`tests/test_untrusted_fence.py`, no network, no live state (temp memory.db through
patched paths, audit captured in memory):
- ten planted injection strings are caught, ten benign owner sentences are not;
- 65,536-character hostile input is capped and fast; zero-width, full-width and
  tag-character evasions are caught;
- fence: fresh marker per call, template tokens removed, fake markers defused,
  own fence not re-wrapped (and closed again when a length cap cut it), kill
  switch, the fence line itself does not trip the scan;
- a crew run with a stubbed model: the tool result reaches the model fenced, TOOL
  and FINAL still parse, the Google Docs guard still works, the stuck banner sits
  outside the fence, timeouts are not fenced;
- origins: memory_save, fact_extract, thread_note and memory_history for owner,
  derived, agent (crew caller, `agent:x`, `CODEC Schedule`), MCP (untrusted, same
  success text) and a scan hit; the MCP tool wrapper sets the caller.
- Deep Research against the local model with the fence on (recorded run with fixed
  search and page data, one page carrying an injection; result in the PR).

## Rollback

`git revert` of the commits, then `python3 tools/generate_skill_manifest.py --write`.
Facts already stored as `untrusted` stay and can be re-marked by a separate update.
Reverting the fence restores the old prompts. Faster, without a deploy:
`FENCE_ENABLED=false` / `MEMORY_SCAN_ENABLED=false` in the PM2 env.
