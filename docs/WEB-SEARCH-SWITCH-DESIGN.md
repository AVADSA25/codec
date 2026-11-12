# Chat: web search only with the "Search the web" switch (R-01, 2026-11-12)

Owner approval, decision board card R-01 (confirmed 12 Nov 2026): "Run web search
only when the switch is on (trigger words and the model's skill tag both), cap the
query length, and refuse it when web is off."

## What was wrong (known issue 2026-10-29)
D-37 made the chat's own search (`routes/chat._enrich_messages`) wait for the
"Search the web" switch. The `web_search` skill did not: in the Chat page it still
ran on everyday phrases ("who is", "who won", "look up") through the pre-LLM
trigger match (`_try_skill`), and on a model-written `[SKILL:web_search:...]` tag,
with the switch off. It sent the whole message (up to 2,000 characters) to
DuckDuckGo or Serper. The Vibe page's `web: false` did not stop it either.

## How the switch reaches the server
The Chat page (`codec_chat.html`, send and regenerate) sends
`force_search: webSearchEnabled` on every `/api/chat` request. The Vibe page's
rollback request (`static/vibe-chat.js`) sends `web: false`. Search is on for a
turn when `force_search` is true and `web` is not false:
`_search_on = bool(body.get("force_search")) and body.get("web", True) is not False`,
read once at the top of `chat_completion`.

## Change (routes/chat.py only, plus one refusal phrase in codec_claim_check)
`_try_skill`, `_try_explicit_skill` and `_try_skill_by_name` take `search`
(default True, so callers without a switch keep their behaviour). The chat route
passes `_search_on` on every path:

| Path | Switch off (or `web: false`) | Switch on |
|---|---|---|
| Pre-LLM trigger match (`_try_skill`) | `web_search` is left out of the matches, as if it had not matched; the next allowlisted match runs, or the model answers | `web_search` runs with the capped query; skills matched before or after it get the message as before |
| '@' pick (`_try_explicit_skill`) | Answers `WEB_SEARCH_OFF`, nothing runs | Runs with the capped query |
| `[SKILL:web_search:...]` tag, stream and stream:false (`_try_skill_by_name`, through the `SkillTagBuffer` callback and the stream:false loop) | The tag becomes `WEB_SEARCH_OFF` in bold, nothing runs | Runs with the capped query |

When `web_search` is one of several matches, each group (the skills before it, it,
the skills after it) is its own `run_skill` call, so its own `wake_dispatch` line and
correlation id; with one match nothing changes.

`WEB_SEARCH_OFF` = "Not searched: web search is off. Turn on Search the web to search."
It is added to `codec_claim_check._NOT_RUN`, so it never backs a claim.

**Query cap:** `WEB_SEARCH_MAX_CHARS = 300`. `_web_search_task(text)` collapses all
whitespace (so pasted lines become one line) and keeps at most 300 characters, cut
back to the last whole word. The skill then removes its own lead-in words as before.
300 characters holds any real search request ("search for ..." questions are well
under 100) and keeps a pasted paragraph from leaving the Mac whole.

## Not changed (they do not receive the switch)
- **Voice call** (think mode can call `web_search`), **wake word** (`codec._dispatch_inner`
  trigger match), **Telegram and iMessage bridges** (`codec_bridges.BRIDGE_SAFE_SKILLS`
  includes `web_search`), **MCP** (`SKILL_MCP_EXPOSE = True`): still run it on a
  trigger phrase or a call, with the old uncapped query.
- **Home's Flash box** (`POST /api/command`, `codec_dashboard`): calls `_try_skill(task)`
  with no switch, so the default `search=True` still runs `web_search` on a trigger
  phrase. Because it goes through `_try_skill`, it now sends the same capped query.
- **Crews** use their own `web_search` tool (`codec_agents._web_search`), and the
  **agent runner** runs the skill only when the plan grants it. Unchanged.
- `skills/web_search.py` itself is unchanged (no manifest change), so the cap applies
  to the chat only.
- `chrome_search` (also triggered by "look up" and "google search") opens a Google
  search in Chrome; it is not part of R-01 and is unchanged.

## Tests
`tests/test_web_search_switch.py`: the cap; the pre-LLM match with the switch off
and on (including fall-through order); the '@' pick; the tag helper; the route on
stream and stream:false with the switch off, on, and on with `web: false`. The
dispatcher and the search are fakes: no request leaves the test. Fakes of the three
helpers in older tests take the new `search` argument.

## Rollback
Revert the PR. No stored state, config key or schema changes.
