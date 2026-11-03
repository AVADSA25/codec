# P-016: blocking calls leave the dashboard's event loop (DESIGN)

**Status:** implemented on `elev/p016-event-loop` (audit 25 Oct 2026, item P-016; findings BC-1, BH-1, BL-6, BC-4). Approved by the owner on 3 Nov 2026 ("go go build").

## What and why

`codec-dashboard` runs one event loop. It serves every page, every poll, every chat stream and the live voice call (`/ws/voice`). When an `async def` handler makes a blocking call, all of them wait until it returns. The worst cases found:

| Where | Blocking call | How long it can hold the loop |
|---|---|---|
| `routes/chat.py` `chat_completion` | `_enrich_messages`: link fetch (3 links x 15 s), web search | up to about 60 s |
| same | `_chat_vision_response`: `requests.post` to the vision model | 120 s |
| same, `stream: false` | `codec_llm.call` | 300 s (`chat.llm_timeout_s`) |
| `routes/update.py` | `codec_update.check_for_update`, `download_and_verify`, `open -R` | 8 s; minutes for the download |
| `routes/upload.py` | `codec_pdftext.extract_text` (`pdftotext`, 90 s timeout) | 90 s |
| `routes/cortex.py` (3 routes) | `pm2 jlist`, `pm2 logs`, `pm2 restart` | 5 to 10 s |
| `routes/health.py` `/api/status` | `pgrep`, polled every 10 s by each open page | 3 s |
| `routes/media.py` | `screencapture`, `pbpaste`, `pbcopy` | 3 to 5 s |
| `routes/cdp.py` | `httpx.get` to Chrome's debug port (not in the brief; the guard found it) | 2 s |

Each call now runs in `asyncio.to_thread(...)`: the same function with the same arguments, the same return value and the same exceptions. Only the thread changes.

## The one split: `_enrich_messages`

The memory part of `_enrich_messages` reads the qchat and vibe SQLite connections. Both are process-wide singletons opened with `check_same_thread=False` and used without a lock, always from the loop thread. In a worker thread a chat turn could read one while `/api/qchat/save` writes to it on the loop. So the memory part moves, unchanged, into `_recall_memory(messages, meta, temporary)`:

- `chat_completion` calls `_recall_memory` on the loop thread, then runs `_enrich_messages(..., memory_parts=<that list>)` in a worker thread. There it does only the link fetch, the web search and the assembly of the context message.
- With `memory_parts=None` (every other caller and the existing tests) `_enrich_messages` reads memory itself, exactly as before.

The order (memory, then links, then search), the messages sent to the model, `meta` ("Memory used", "Searched the web") and the `web_search_sent` audit line are unchanged.

## The guard (`tests/test_no_blocking_requests_in_async.py`)

For every `async def` in `codec_dashboard.py` and `routes/*.py` it reports:

- `requests.*` (as before), `urlopen`, any `subprocess.*` function, sync httpx (`get`, `post`, `put`, `delete`, `patch`, `head`, `request`, `stream`, `Client`), `socket.getaddrinfo`, `codec_llm.call`;
- the cross-module calls that do one of those: `codec_update.check_for_update`, `codec_update.download_and_verify`, `codec_search.search`, and `codec_pdftext.extract_text` (added: the PDF site moved behind this call on 30 Oct, #501).

Import aliases are resolved (`import requests as rq2`, `from subprocess import run`, `import urllib.request as ur`). A call to a sync function defined in the same file is followed one hop: that function's own body is checked too. A nested function or a lambda counts where it is called, so passing one to `asyncio.to_thread` is the fix and calling it inline is reported. A sync generator is not followed: calling it runs nothing, and `StreamingResponse` iterates it in a worker thread. A helper of a helper (two hops) is not followed.

## API and data changes

None visible. No route, request, response, config key, stored data or audit event changes. Internal: two new helpers in `routes.chat`, `_recall_memory` and `_last_user_message` (the "find the last user message" lines both need), and a new keyword argument `_enrich_messages(memory_parts=None)`. No new dependency.

## Migration

None. The change takes effect when the owner restarts `codec-dashboard`.

## Test plan

- The guard: 0 hits on the repo; one positive control per pattern; the one-hop, two-hop, nested-function, lambda, generator and "argument of `to_thread`" cases.
- `tests/test_chat_event_loop.py`, in process (`httpx.AsyncClient` with `ASGITransport`, one event loop, config and memory paths in `tmp_path`): the link fetch, the web search, the non-stream reply and the vision call each block their thread for up to 5 s. `GET /api/status`, sent on the same loop while the chat waits, must answer in under 250 ms, the slow call must run on a worker thread, and the reply must be what it was. The qchat and vibe connections must be used on the loop thread only. A control puts a slow call on the loop thread on purpose, and the harness must report it.
- The existing tests of every changed module.
- Manual, after the owner's restart: `/ws/voice` audio frames show no gap over 1 s while a chat with a pasted link is answered (needs the live TTS server).

## Rollback

`git revert` of the commit. Nothing stored, no config and no schema is touched. Deploying and rolling back are both a `codec-dashboard` restart, which is the owner's action.
