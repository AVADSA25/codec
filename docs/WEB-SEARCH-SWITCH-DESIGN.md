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
- `chrome_search` was left out of R-01; S-07 (below) adds it.

## S-07: chrome_search follows the same switch (2026-11-17)

Owner approval, decision board card S-07 (confirmed 17 Nov 2026): chrome_search runs
from the chat only with the "Search the web" switch on, as R-01 did for web_search;
voice, wake word and MCP unchanged.

Why: `chrome_search` opens a Google search in Chrome with the query in the address,
so the text leaves the Mac like web_search's. Its triggers ("search google",
"google search", "look up", "google for", "find online", "search online") fired it
from the chat with the switch off, and "look up" matched both skills.

Change (routes/chat.py only): `SEARCH_SKILLS = {web_search, chrome_search}`, and
every place R-01 tested for `web_search` now tests for either name:

| Path | Switch off (or `web: false`) | Switch on |
|---|---|---|
| Pre-LLM trigger match (`_try_skill`) | both are left out of the matches (`_without_web_search`); the next allowlisted match runs, or the model answers | each runs on its own `run_skill` call with the capped query, in match order; the skills before, between and after get the message as before (`_run_matched`) |
| '@' pick (`_try_explicit_skill`) | answers `WEB_SEARCH_OFF`, nothing runs | runs with the capped query |
| `[SKILL:chrome_search:...]` tag, stream and stream:false (`_try_skill_by_name`) | the tag becomes `WEB_SEARCH_OFF` in bold, nothing runs | runs with the capped query |

The off line is the same `WEB_SEARCH_OFF` text (already in
`codec_claim_check._NOT_RUN`). The query cap is the same `_web_search_task`
(one line, at most 300 characters, cut at a whole word); the skill then strips its
own lead-in words as before. `skills/chrome_search.py` is unchanged (no manifest
change).

Not changed: the voice call, the wake word, the bridges, MCP (`SKILL_MCP_EXPOSE = True`;
over HTTP it still needs the owner's Allow, `_HTTP_CONSENT_REQUIRED`), crews and the
agent runner. Home's Flash box (`/api/command`) and the Today tiles keep the default
`search=True`: the Flash box still opens a Google search on a trigger phrase, now
with the capped query; no Today tile uses chrome_search.

## Tests
`tests/test_web_search_switch.py`: the cap; the pre-LLM match with the switch off
and on (including fall-through order); the '@' pick; the tag helper; the route on
stream and stream:false with the switch off, on, and on with `web: false`. S-07
adds the same cases for chrome_search (pre-LLM match, both search skills matched,
'@' pick, tag, Flash default, the route on both paths) and checks on the real
built-in triggers that "search google for ...", "google for ..." and "look up ..."
leave nothing to run with the switch off. The dispatcher and the search are fakes:
no request leaves the test and Chrome is never opened. Fakes of the three
helpers in older tests take the new `search` argument.

## Rollback
Revert the PR. No stored state, config key or schema changes.
