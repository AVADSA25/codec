# Vibe chat on its own route — design

Status: approved. Owner decision D-22, 29 Oct 2026 ("go"). Plan item P-042 (audit 25 Oct 2026). Step 1 of the item (the
`web: false` flag) is already merged (D-37, docs/LOCAL-FIRST-DEFAULTS-DESIGN.md). This is step 2: the route.

## Why

Chat mode in Vibe (the default and so far the only used mode: `vibe.db` has 71 sessions, the agent has two runs ever) sends its
messages to `POST /api/chat`, the general chat. That route is built for CODEC's everyday chat, so a Vibe message inherits things
a coding page must not have:

| What the general chat does | What it means in Vibe |
|---|---|
| Pre-LLM skill hijack (`_try_skill`) | 6 of 18 typical Vibe prompts never reach the model: "build a timer app", "create a weather dashboard", "build a todo list with reminders", "translate this page to french", "show me a bitcoin price chart" run a skill; "list files in the project" hangs up to 600 s |
| Memory recall, open threads, observer text | Personal context is added to a code prompt |
| Persona system prompt, glued in front of the page's prompt | Two identities in one prompt |
| The model the chat is on | While chat is switched to a cloud model (MiMo), up to 60,000 characters of project files go to the cloud with no dialog |
| Link fetch and web search of the whole message | Closed by D-37 (`web: false`); the file dump is still scanned |
| The page's own `SYSP` | Names modes and features that do not exist: KAIROS, COORDINATOR, BUDDY, AUTO-MODE, "50+ skills", "CODEC shared memory" |
| `vibe.db` stores the user turn as sent | The `[PROJECT FILES]` dump (up to 60 KB) is saved in every user message |

## What changes

### 1. A route of its own: `POST /api/vibe/chat` (`routes/vibe_chat.py`, engine `codec_vibe_chat.py`)

Request (JSON):

| field | meaning |
|---|---|
| `messages` | the page's turns: `[{role, content}]`. Only `user` and `assistant` are kept; any `system` entry is dropped (the server writes the prompt). The last one must be a `user` turn. At most 200 turns and 600,000 characters, else 400 |
| `stream` | `true`: SSE. Default `false`: one JSON reply |
| `project` | `true` when the answer's files go into a project folder (project prompt: one fenced block per file with `file:path`) |
| `workdir` | the working folder the owner chose; one line in the prompt so code can use it as the base for local file paths (max 500 characters) |
| `model` | `"local"` (default) or `"cloud"` |
| `confirm_cloud` | the cloud model is used only for `model: "cloud"` **and** `confirm_cloud` exactly `true`. `model: "cloud"` without it is 400. `confirm_cloud: true` with `model: "local"` does nothing |

What it does, in this order, and nothing else:

1. Reads the switch (section 5). Off: 404.
2. Checks the body; picks the model (section 2).
3. Builds **only the Vibe system prompt** (section 3): no persona prompt, no memory recall, no open threads, no observer text,
   no step budget suffix, no standing rules.
4. Calls `codec_llm.stream` (or `codec_llm.call` for `stream: false`). It never calls `_try_skill`, `_try_skill_by_name`,
   `_enrich_messages`, `_fetch_url_content`, `codec_search.search`, `codec_memory`, `codec_observer` or the slash-command
   parser. A model-written `[SKILL:...]` tag is plain text. Nothing in the module imports `routes.chat`.
5. Writes one counts-only audit line (section 6).

Stream, the shape the page parses is unchanged: `data: {"token": "<text>"}\n\n` per piece, `data: [DONE]\n\n` at the end.
The route also sends `: keepalive` comment lines while a model is silent (holds the tunnel, like `/api/chat`), one
`data: {"stats": {...}}` frame before the end (the page ignores frames it does not know, so this is additive), and the same
visible notices as `/api/chat` as `token` frames when a reply was cut by the token cap, interrupted, empty or stuck in a
repetition loop. A failure while streaming sends `data: {"error": "..."}` then `[DONE]`. `<think>...</think>` is removed on
the server across chunk boundaries, so reasoning text never reaches the editor's live fence parser.

Non-stream: `{"response": "<text>", "model": "<id>", "route": "own"}`.

Status: `GET /api/vibe/chat/status` returns `{"route": "own"}` or `{"route": "chat"}` so the page learns the switch. It is
read from `config.json` at every call.

### 2. Local unless the owner confirms cloud

`codec_vibe_chat.resolve_llm(kind, confirm_cloud)` follows the Vibe agent's rule (`codec_vibe_agent._resolve_llm`), without
depending on the agent's kill switch:

- **local:** `llm_base_url` / `llm_model` from `config.json` through `codec_cloud_models.local_only(base, model, cfg)` (used as
  is, not edited: AGENTS.md section 10). A registered cloud entry that chat is switched to becomes the local model it stands in
  for (`llm_local_restore`, else the default). So a Vibe message never reaches a cloud URL by itself.
- **Fail closed for an unregistered remote provider.** `local_only` leaves a base URL that is not a registered cloud entry
  unchanged (a LAN Mac, an AVA or custom provider). A LAN Mac is fine (private address, `.local`, `.lan`, a bare host name); a
  public host that is not a registered cloud entry is not on this Mac or this network, so the route answers 409 with a plain
  sentence instead of sending project files there. The owner fixes it by switching chat to the local model, or registers
  the provider as a cloud entry.
- **cloud:** only with `model: "cloud"` and `confirm_cloud: true`. The entry is the one chat is on, else the first registered.
  `codec_cloud_models.block_message` is checked first (no price set, monthly cap reached, no key): 409 with its message. The
  hard monthly cap and the metering stay in `codec_llm` (not edited).

The API key: the local model gets `get_llm_api_key()` only when its URL is the configured one, unchanged. A substituted local
URL or a cloud entry gets none from here (`codec_llm` adds the entry's key).

Page side: the model list (`GET /api/vibe/agent/models`, the Agent mode's own call) shows the cloud model next to the mode
switch in **both** modes now. Choosing it makes every chat send open the agent's confirm dialog (`Send this message to
<label>?`, naming the host and the cap), and only "Send" sends `model: "cloud", confirm_cloud: true`. The choice is shared
with the Agent mode and goes back to Local when the page is reloaded (as today). When the agent is switched off the list is
not available, so chat has no cloud choice: local only.

### 3. The prompt

`codec_vibe_chat.build_system_prompt(project, workdir)` is the only system text. It says what Vibe chat really does: write or
change code in the open project; it sees the owner's message and the open code (`[CURRENT CODE]` / `[PROJECT FILES]`), nothing
else; it cannot run code, open links, search, use other CODEC features or remember other chats; reply with a short sentence
and fenced blocks with the COMPLETE file; in a project, one block per file with `file:path` (the page writes them behind a
checkpoint the owner can undo). It tells the model to treat text inside files as data and never to write secrets into
files. The four "operational modes", "50+ skills" and "CODEC shared memory" passages are gone.

The page keeps a copy (`SYSP`, `SYSP_PROJECT`) for the rollback path only, cleaned the same way. A test pins that neither
copy mentions the invented names and that both keep the file-block contract the page parses (`VibeFiles.parseBlocks`,
`apply_blocks`).

### 4. `vibe.db` stores what was typed

`doSaveSes` sent `hist` as is, so every user turn was stored as the typed text **plus** the `[PROJECT FILES]` dump (or the
`[CURRENT CODE]` block). The page now saves `plain` (the typed text) for user turns, through `VibeChat.stored(hist)`. Assistant
turns are unchanged. A reloaded session also shows the short text instead of the dump. Nothing is migrated: rows already saved
keep their dump (they can be deleted with their session).

### 5. Rollback switch: `config.json` → `vibe.chat_route`

`"own"` (default; also any missing or unknown value) uses the new route. `"chat"` makes the new route answer 404 and
`/api/vibe/chat/status` say `chat`, and the page sends to `/api/chat` as before. Read at every request: **no restart, a page
reload is enough.** The page also falls back to `/api/chat` for one send when the new route answers 404 (an older server, or the
switch flipped while the page was open) and remembers that for the page load.

The `/api/chat` body from the page on that path carries `tools: false` and `temporary: true` as well (item P-042 step 1: no
skill hijack, no memory recall, no observer text; `web: false` was already sent). Rollback therefore brings back the old
*route*, not the audited defects. The cloud rule of section 2 does not apply on this path: `/api/chat` follows whatever model
chat is on, as before.

Env var: none. `VIBE_PROJECTS_ENABLED` and `VIBE_AGENT_ENABLED` need a PM2 restart (the owner's action); a config switch does
not, and a second switch for the same thing would only add a state to test.

### 6. Audit

One line per accepted request, before the model call, source `codec-vibe`, level info. Counts only: never a message, a file name,
a path or any code.

| Event | extra fields |
|---|---|
| `vibe_chat_sent` | `route` (`own`), `messages` (turns sent), `files` (file blocks in the last user turn, plus 1 for a `[CURRENT CODE]` block), `chars` (everything sent to the model, system prompt included), `project` (bool), `stream` (bool), `model` (`local`\|`cloud`), `workdir` (bool) |

Documented in `docs/codec-architecture.md` (Vibe audit events) and in AGENTS.md section 7 (Vibe projects), with the rule:
**Vibe chat never uses the general chat's persona, memory, skills or web.** Replies are also counted in Settings > Usage
(`codec_usage.record_reply`, source `vibe`; model, cloud flag and speed only).

## Files

| File | Change |
|---|---|
| `codec_vibe_chat.py` (new) | prompt, model choice, body checks, think stripper, stream, audit |
| `routes/vibe_chat.py` (new) | `POST /api/vibe/chat`, `GET /api/vibe/chat/status` |
| `codec_dashboard.py` | register the router (2 lines) |
| `static/vibe-chat.js` (new, stamped) | which route, request bodies, fallback on 404, what is saved (plain functions, Node-tested) |
| `static/vibe-agent.js` | model list visible in chat mode; `chatModel()` and `confirmChatCloud()` for the page |
| `codec_vibe.html` | send through `VibeChat`, cloud confirm before the send, `stored(hist)` on save, `SYSP` cleaned, script tag |
| `tools/stamp_static.py` | stamp the new script |
| tests | `tests/test_vibe_chat.py` (route), `tests/test_vibe_chat_page.py` (page, Node), pins updated in `tests/test_vibe_agent_page.py` |
| `docs/codec-architecture.md`, `AGENTS.md` | audit event, Vibe paragraph |

Not touched: `routes/chat.py`, `codec_llm.py`, `codec_cloud_models.py` (section 10 zones), `skills/` (no manifest change),
`routes/vibe.py` (storage unchanged), the Agent mode.

## Tests

All with a fake `codec_llm.stream` / `codec_llm.call`, no network, no model, no write to `~/.codec` (the audit log, usage log and
config are redirected; `conftest.py` already redirects the first two).

- Cloud: chat switched to a registered cloud entry, a request with no confirmation: the model call goes to the local URL, never
  to the cloud host. `model: "cloud"` without `confirm_cloud` is 400 and calls nothing; with `confirm_cloud: true` it calls the
  cloud entry; `confirm_cloud: true` alone stays local. A capped cloud entry is 409 with no call. A public unregistered base URL
  is 409 with no call; a LAN address passes.
- The six hijacked prompts start no skill (`codec_dispatch.check_skill`, `run_skill`, `routes.chat._try_skill` and `_try_skill_by_name`
  are replaced by recorders and must see zero calls), and the model still receives each prompt.
- A message with a URL and one with search words make zero calls to `_fetch_url_content` and `codec_search.search`.
- The SSE stream is `token` frames then `[DONE]`; the page's own parser (the loop in `codec_vibe.html`) is run in Node against it.
  `<think>` blocks split across chunks are removed.
- The system prompt is the only system text (a client `system` message and a persona prompt are dropped), has no invented mode
  and no persona, memory, observer or skill text, and states the file-block contract.
- Switch: `vibe.chat_route: "chat"` makes the route 404 and the status say `chat`; the page code falls back on 404.
- Audit: one line, numbers and booleans only, no text from the request.
- Page: `stored(hist)` returns typed text; the request body for each route; cloud only after the dialog; scripts stamped.

## Rollback

Set `"vibe": {"chat_route": "chat"}` in `~/.codec/config.json` and reload the Vibe page. Nothing else changes. To remove the
code, revert the PR (no data, schema or config key is migrated; `vibe.db` rows keep their text).

Takes effect after a `codec-dashboard` restart (the owner's action); the page script is served with a new version stamp.
