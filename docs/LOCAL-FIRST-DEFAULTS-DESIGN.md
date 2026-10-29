# Local-first defaults — design

Status: approved. Owner decision D-37, 29 Oct 2026 ("Yes to all four"): web search only with the web toggle on;
vision local unless `vision_provider` is set; mail triage and text assist always local; Google Docs private unless
`share=true`. Plan item P-020 (audit 25 Oct 2026). D-36 (same day): CODEC runs on the local Qwen3.6 35B for chat,
voice and vision.

## Why

"Nothing leaves your Mac" is the promise. Before this change, five paths sent data out without a visible setting:

| Path | What left the Mac | Where |
|---|---|---|
| Chat (`routes/chat.py`, `_enrich_messages`) | the whole last message, when it contained one of 14 substrings ("recent", "google", "right now", ...), also in a temporary chat | Serper or DuckDuckGo |
| Vibe page (`codec_vibe.html`) | the same, with up to 60,000 characters of project files in the message; links in it were fetched | search provider, any public host |
| Vision (`codec_vision.py`, `codec.py`, `codec_voice.py`) | a screenshot or an image, whenever a Gemini key existed | Google Gemini |
| Mail triage (`codec_email_triage.classify`) and text assist (`codec_textassist.call_qwen`) | senders, subjects, snippets; the selected text | the cloud model, while chat was switched to one |
| Google Docs (`codec_gdocs.create_google_doc`) | every new doc readable by anyone with the link; a failed run left that doc behind | Google Drive sharing |

## The changes

### 1. Web search only with the toggle (`routes/chat.py`)

- The 14 trigger words are gone. `_enrich_messages` searches only when the page sends `force_search: true`, which
  the existing "Search the web" toggle already does (`codec_chat.html`, both send and regenerate). No new control.
- A search sends `_derive_search_query(message)`: the first non-empty line, links removed, a lead-in such as
  "search the web for" or "can you look up" removed, at most 12 words (`SEARCH_QUERY_MAX_WORDS`). Pasted text
  below the first line stays on the Mac.
- The reply shows "Searched the web" (a `web` event before the first token on the stream, `"web"` in the
  non-stream reply; the page draws a small label next to "Memory used").
- One audit line per search, `web_search_sent`, lengths only (see below).
- A temporary chat follows the same rule: no search on trigger words, the toggle and links still work
  (docs/P2.11-DESIGN.md is updated).
- Unchanged: a link in a chat message is still read (`_fetch_url_content`, SSRF-guarded), and the URL check still
  skips the search when the message has a link.

### 2. `web: false` for Vibe (`routes/chat.py`, `codec_vibe.html`)

- New request field `web` (default true). `web: false` skips the link fetch and the search, even with
  `force_search`. The Vibe page sends it on every `/api/chat` call (`codec_vibe.html`; the project files it sends
  are built in `static/vibe-projects.js`).

### 3. Vision local unless `vision_provider` is set

- `codec_vision._vision_config`, `codec.py` and `codec_voice.py` default `vision_provider` to `"local"`. A Gemini key
  alone no longer sends a screen or an image to Google. `vision_provider: "gemini"` in `~/.codec/config.json` brings
  the old behaviour back. The chat's image questions already went to the local vision model and are unchanged.

### 4. Mail triage and text assist always local

- Both call `codec_cloud_models.local_only(base_url, model, cfg)` before the model call, as `codec_briefing.py` and
  `codec_voice.py` already do. A registered cloud entry becomes the local model it stands in for; anything else
  (the local server, a LAN Mac, a custom provider) is unchanged. `codec_cloud_models.py` is not edited
  (AGENTS.md section 10). If the lookup fails, triage uses the local server address (fails closed); text assist
  shows its error overlay.

### 5. Google Docs private unless `share=True` (`codec_gdocs.py`)

- `create_google_doc(title, content, share=False)`. The "anyone with the link can view" permission is added only
  with `share=True`, and only after the doc is complete. No caller passes `share=True` today: scheduled reports
  (`codec_scheduler.py`) and the crew tool `google_docs_create` (`codec_agents.py`) make private docs. The crew
  tool does not read a share request from the model on purpose: text the model reads (a web page in Deep Research)
  could ask for it. A later owner-facing switch can pass `share=True`.
- A run that fails after the doc was created moves that doc to the Drive bin (`files().update(trashed=True)`):
  it leaves the doc list, Google empties the bin after 30 days, and nothing is erased by CODEC itself, like the
  Notes and Vibe trash.

## Audit

| Event | Source | extra fields |
|---|---|---|
| `web_search_sent` | `codec-dashboard` | `query_words`, `query_chars`, `message_chars`, `results`, `temporary` |

Level info (warning, with `outcome=error` and `error_type`, when the search raised). Never the query or the
message. No change to the audit envelope.

## Cost

- Search quality: answers that relied on an automatic search ("what's the weather in Lisbon right now") now come
  from the model alone unless the toggle is on, and a toggle search sends at most 12 words instead of the whole
  message, so some searches will find less.
- Vision: with a Gemini key and no `vision_provider`, screen and image questions run on the local vision model
  (slower, and its reading of small text can be weaker). To keep Gemini, set `"vision_provider": "gemini"`.
- Docs: a doc link opens only for the Google account CODEC writes with. Opening it signed in to another account
  asks for access.
- Unchanged and out of scope: the `web_search` skill still runs on its own trigger phrases ("search for",
  "who is", ...) and through a model-written `[SKILL:web_search:...]` tag; the MiMo auto-fallback (GP06-4);
  `fact_extract`, dictation and sessions.

## Tests

`tests/test_local_first_defaults.py` (all fail on the code before this change):

- each of the 14 old trigger words sends no search, marks nothing and writes no audit line;
- the toggle sends one query of at most 12 words without the lead-in or the pasted lines, marks the reply and
  writes one `web_search_sent` line whose fields are numbers;
- a Vibe-shaped `/api/chat` body with `web: false` makes zero calls to `_fetch_url_content` and
  `codec_search.search` (the same body without it reads the link), and the Vibe page sends `web: false`;
- with a Gemini key and no `vision_provider`, `describe_sync` posts only to the local model; with
  `vision_provider: "gemini"` it uses Gemini; `codec.py` and `codec_voice.py` default to local;
- with a registered cloud entry active, `classify` and `call_qwen` send to `http://localhost:8083/v1`;
- `create_google_doc` adds no permission unless `share=True`, and a failed run trashes its doc and never shares it.

`tests/test_temporary_chat.py` pins the temporary-chat rule: no search on a trigger word, the toggle searches,
links are read.

## Rollback

`git revert` of the PR restores each old default. Nothing is migrated: no config key is written, and a doc created
without sharing stays private (share it from Google Docs if needed). The owner can keep Gemini vision without a
revert by setting `vision_provider: "gemini"`. Takes effect after restarts of `codec-dashboard` and `open-codec`
(the owner's action); `codec-mcp-http` too for mail triage asked from claude.ai; text assist picks it up on its
next run.
