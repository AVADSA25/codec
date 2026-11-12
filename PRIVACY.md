# Privacy

CODEC is **local-first**: the models, the speech, the screen reading and your memory run on your Mac. This page
says what CODEC stores, every kind of request that leaves the Mac and when, what the audit log does and does not
record, and how to delete everything. `tests/test_privacy_doc.py` checks the hosts, intervals, Keychain names and
paths on this page against the code, so a change to one fails the build until the other is changed too.

If you find a gap between this page and the code, open an issue: we treat it as a bug.

> **Not legal advice.** This is a good-faith engineering disclosure. Before the paid tier launches it should be
> reviewed by a privacy lawyer.

## 1. What CODEC stores on your Mac

Everything below lives on your Mac's disk. Nothing is synced anywhere.

| Path | What | Notes |
|---|---|---|
| `~/.codec/memory.db` | Conversations and remembered facts | SQLite with full-text search. Chats are deleted after 90 days by default (Settings > Memory). |
| `~/.codec/qchat.db`, `~/.codec/vibe.db` | Quick-chat history, Vibe chat history | SQLite |
| `~/.codec/clipboard.db` | Clipboard history (the `clipboard` skill) | Deleted after 30 days by default |
| `~/.codec/photos/` | Webcam photos | Deleted after 30 days by default |
| `~/.codec/notes/` | Your CODEC Notes | Deleted notes go to `notes/.trash/` |
| `~/.codec/agents/` | Project-mode agents: plans, grants, messages | Project files go to `~/codec-projects/`; Vibe projects to `~/codec-workspace/` |
| `~/.codec/google_token.json`, `~/.codec/google_credentials.json` | Google sign-in token and OAuth client | On disk (0600), not in the Keychain |
| `~/.codec/config.json` | Settings | Provider API keys and the paid app's licence key are in the Keychain (below); the installer writes the licence key here and CODEC moves it to the Keychain on first read. This file still holds the e-mail alert password if you set one. |
| `~/.codec/mcp_servers.json` | Connectors you added | Includes any API-key header you typed for one |
| `~/.codec/backups/` | Checked copies of `memory.db` and a daily state archive | Made daily, and before a clean-up |
| `~/.codec/pre_update/` | A copy of the databases and the state archive taken before each update | 0600; CODEC never deletes these: remove old ones by hand |
| `~/.codec/audit.log` | Audit log (section 3) | 0600, one new file per day, 30 days kept |
| `~/.codec/observer_buffer.json` | What the observer saw in the last 10 minutes: active window, screen text, recent files, idle time | 0600; the clipboard only as its type and length; deleted when the observer pauses or stops and after a long idle |
| `~/.codec/images/`, `~/.codec/models/`, `~/.codec/skills/`, `~/.codec/plugins/` | Pictures you made, downloaded models, your own skills and plugins | |
| `~/Library/Logs/CODEC/`, `~/.pm2/logs/codec-*` | Service logs (Mac app, source install) | A source install also writes `~/.pm2/logs/qwen3.6-*`, `whisper-stt-*` and `kokoro-82m-*` (the model servers) |
| `~/Library/Caches/ai.avadigital.codec`, `~/Library/WebKit/ai.avadigital.codec` | The app window's cache and page storage (theme and layout) | |

### Secrets in the macOS Keychain

Each secret is a generic password whose service is `ai.avadigital.codec.<name>`:

| Keychain service | What it holds |
|---|---|
| `ai.avadigital.codec.audit_hmac_secret` | The key that signs each audit log line |
| `ai.avadigital.codec.internal_token` | The token CODEC's own processes use to call the dashboard |
| `ai.avadigital.codec.dashboard_token` | The dashboard login token, if you set one |
| `ai.avadigital.codec.oauth_state` | Sign-ins of apps connected to CODEC over MCP HTTP (claude.ai) |
| `ai.avadigital.codec.license_key` | The paid app's licence key |
| `ai.avadigital.codec.llm_api_key` | The key for the model provider you chose (the licence key when you use the AVA proxy) |
| `ai.avadigital.codec.gemini_api_key` | Your Google Gemini key, if you added one |
| `ai.avadigital.codec.pexels_api_key` | Your Pexels key, if you added one |
| `ai.avadigital.codec.serper_api_key` | Your Serper search key, if you added one |
| `ai.avadigital.codec.telegram_bot_token` | Your Telegram bot token, if you added one |
| `ai.avadigital.codec.vapid_private_key` | The key that signs phone notifications |
| `ai.avadigital.codec.mcp_tokens` | Sign-ins to the connectors CODEC uses (one item per connector) |

A `key_slot` you name in `config.json` (a cloud model, the delegate webhook) is stored as
`ai.avadigital.codec.<that name>`. Without a Keychain (Linux, CI) secrets go to `~/.codec/secrets.enc.json`,
encrypted with `~/.codec/secret.key`: weaker than the Keychain.

## 2. What leaves your Mac

CODEC has no analytics SDK, no crash reporter and no usage reporting. The app's privacy manifest
(`packaging/macos/PrivacyInfo.xcprivacy`) declares no tracking.

### 2.1 Requests CODEC makes on its own

These two are the only requests that go out without you asking for something or turning a feature on:

| Request | When | To | What it carries |
|---|---|---|---|
| **Update check** | When the dashboard page opens, then every 6 hours while it stays open | `https://github.com/AVADSA25/codec-updates/releases/latest/download/appcast.xml` (GitHub). `sparkle_feed_url` in `config.json` changes the address. | A plain GET: GitHub sees your IP address and that a copy of CODEC asked, nothing else. The update file is downloaded only when you click Download, checked against the release signing key (Ed25519) and saved to `~/Downloads`. There is no off switch yet. |
| **Licence public key** (paid app only, once a licence key is entered) | When a skill, an agent or a cloud call checks the licence: at most once an hour per CODEC process once the key has been fetched, and at most once a minute while the fetch keeps failing | `https://codec-license.avadigital.ai/public-key` (AVA Digital) | A plain GET for the public key. Your licence key is checked on the Mac against it and is not sent. |

### 2.2 Requests you ask for, or features you turn on

| Feature | What is sent | To | When |
|---|---|---|---|
| **Web search in chat** | The first line of your message, links and a lead-in such as "search the web for" removed, at most 12 words. Pasted text below the first line stays on the Mac. | DuckDuckGo (`api.duckduckgo.com`, `html.duckduckgo.com`), or Serper (`google.serper.dev`) when a Serper key is set; if Serper fails or answers with an error (429 = out of credit), the same query goes to DuckDuckGo instead | Only when the "Search the web" toggle is on; the reply then shows "Searched the web". Temporary chats follow the same rule; Vibe never searches. |
| **The `web_search` skill** | In Chat and the Flash box on Home: your request on one line, at most 300 characters. By voice, the wake word, Telegram, iMessage or claude.ai: the words of your request | The same providers | In Chat only while the "Search the web" toggle is on: when you say a phrase such as "search for", "look up" or "who is", pick it with @, or the model calls the skill. With the toggle off it answers "Not searched" and sends nothing; Vibe never runs it. Voice, the wake word, the Flash box on Home, Telegram, iMessage and claude.ai still run it on such a phrase or call. |
| **A link in a chat message** | A request for that page | The site in the link (public addresses only) | When your message contains a link (not in Vibe) |
| **Screen and image questions** | The screenshot or image | The local vision model by default. **Google Gemini** (`generativelanguage.googleapis.com`) only when `config.json` has `"vision_provider": "gemini"` and a Gemini key. | Per question |
| **A cloud model** for chat, voice and agents | Your prompt, the conversation context and tool results | The local model (`http://localhost:8083/v1`) by default. A cloud provider only if you chose one at setup, picked a cloud model in the model list, or set `llm_auto_fallback` (used while the local model is down). Mail triage, text assist, the morning briefing and Project-mode agents always stay local; Vibe chat asks before each message it sends to a cloud model. | Per turn while a cloud model is selected |
| **AVA cloud proxy** (paid app, if you pick it at setup) | The same, with your licence key as the sign-in | AVA Digital's proxy (`ava-proxy.lucyvpa.com`), which passes it to Google Gemini models (`gemini-2.5-flash-lite` by default) | Per turn |
| **claude.ai connector** (MCP over HTTP) | The tool requests claude.ai makes and their results | Anthropic (claude.ai), through your own Cloudflare tunnel | While claude.ai is connected; tools with side effects wait for your Allow |
| **Google skills** (Gmail, Calendar, Drive, Docs, Sheets, Slides, Tasks, Keep) | What the request needs | Google APIs (`*.googleapis.com`), as your own account | Per request. A Google Doc CODEC creates is private; it is shared only when a caller asks for it, and none does today. |
| **Pictures in Google Docs** | Words from the doc's headings | Pexels (`api.pexels.com`) | When a Pexels key is set and CODEC writes a Google Doc |
| **Connectors** (Settings > Connectors) | The tool calls you make through them | The servers you add (Notion, Linear, ...) | Per call, after you sign in |
| **Telegram bridge and owner alerts** | The messages you exchange with your bot; alerts | Telegram (`api.telegram.org`). The bridge keeps asking Telegram for new messages while it runs. | After you add a bot token |
| **Alerts by e-mail or Slack** | The alert text (which CODEC service failed or recovered) | The mail server or Slack webhook you set under `alerts` in `config.json` | After you turn `alerts.email` or `alerts.slack` on |
| **iMessage bridge** | The messages you send and receive | Apple, through the Messages app | After you set it up |
| **Phone notifications** (Web Push) | An encrypted fixed line per kind ("CODEC needs your approval"), never the question, the agent or any chat text | Your browser's push service: Google (`fcm.googleapis.com`), Apple (`*.push.apple.com`), Mozilla (`*.push.services.mozilla.com`) or Microsoft (`*.notify.windows.com`) | When an approval, a question or an agent result waits, on the devices you turned it on for |
| **Skills that look something up** | The place, coin, query or page | weather: `wttr.in`; bitcoin price: `api.coingecko.com`, then `api.coindesk.com` if that fails; network info: `api.ipify.org` (shows your public IP); Philips Hue: `discovery.meethue.com`; AI news digest: public news feeds; `web_fetch` and the `chrome_*` skills: the page you name | When you ask |
| **Morning briefing** (off by default) | Calendar and mail requests, the weather place | Google, `wttr.in` | On its schedule, after you switch it on in Settings |
| **Heartbeat alerts** (`heartbeat_alerts` in `config.json`) | A price alert: the coin name. A mail check: a Gmail request | `api.coingecko.com`; Google | On every heartbeat, after you add one |
| **The dashboard from your phone** | The dashboard traffic | Your own Cloudflare tunnel (or Tailscale) to your Mac | While the tunnel runs |
| **Model downloads** | The model name | Hugging Face | When you install, pick or look up a model |
| **Skill marketplace** (command line, `codec_marketplace.py`) | A request for the skill list or a skill file | GitHub (`raw.githubusercontent.com`) | When you run it |

Speech recognition (Whisper), speech (Kokoro, voice 02), image making, the observer, memory, Notes, the morning
briefing's script and Project-mode agents run on the Mac. Dictation's tidy-up step and fact extraction use the
model that is selected, so they go to a cloud model while one is selected.

## 3. What the audit log records

`~/.codec/audit.log` holds one JSON line per event. The file is 0600, every line is signed (HMAC-SHA256 with the
`audit_hmac_secret` Keychain key), and known secret patterns are replaced before a line is written. A new file
starts each day (UTC) and dated files older than 30 days are deleted. The Audit view sorts the lines into 16 filter
groups (command, skill, llm, auth, ...) that it works out when it reads the log.

It records what CODEC's code chooses to log: skill runs through CODEC's dispatcher (chat, wake word, bridges),
tool calls by crews and over MCP, consent questions and answers, security refusals (blocked skills, plugins,
paths and commands), Keychain reads that find nothing, scheduled jobs and triggers, Project-mode agent steps, and
each web search sent from chat (`web_search_sent`, with lengths only, never the query).

It is **not a network log**. Model calls, the update check and the licence key fetch have no line of their own.

## 4. Skills, plugins and MCP

- Built-in skills are pinned by SHA-256 in `skills/.manifest.json`, which CI checks. Your own skills in
  `~/.codec/skills/` pass an AST safety check every time they load.
- Plugins load only when their SHA-256 is in `~/.codec/plugins.allowlist`.
- Over MCP HTTP, shell and code tools are blocked, and tools with side effects wait for your Allow in the app.
  `mcp_default_allow` and `mcp_allowed_tools` in `config.json` limit what MCP clients can call.
- A skill runs with your own permissions: it can read what you can read. macOS asks before CODEC first uses the
  microphone, camera, screen or another app.

See `SECURITY.md` and `AGENTS.md` for the full threat model.

## 5. Deleting your data

**Uninstall.** `packaging/macos/uninstall_codec.sh` lists everything below and deletes nothing unless you pass
`--yes`. `--yes` removes the app, its launchd agents, `~/Library/Logs/CODEC/`, CODEC's PM2 logs, the app's
`Caches`, `WebKit` and `HTTPStorages` folders and every `ai.avadigital.codec.*` Keychain item. `--yes --purge-data`
also removes `~/.codec`.

**By hand.**

- `rm -rf ~/.codec` removes everything in the first table that is under `~/.codec`: `memory.db`, `qchat.db`,
  `vibe.db`, `clipboard.db`, `notes/`, `agents/`, `google_token.json`, `config.json`, `backups/`, `pre_update/`, `audit.log` and
  its dated files, `photos/`, `images/`, `models/`, `skills/` and `plugins/`.
- `security delete-generic-password -s ai.avadigital.codec.<name>` removes one Keychain item. Run it for each name
  in the Keychain table and each `key_slot` you named; repeat it for `mcp_tokens` until it says the item could not
  be found.
- Remove CODEC's access to your Google account at `https://myaccount.google.com/permissions`.
- Your project folders `~/codec-projects/` and `~/codec-workspace/` are yours; delete them if you want.

**Automatic clean-up.** Settings > Memory sets how long CODEC keeps chats (90 days by default), clipboard history
(30 days) and webcam photos (30 days). CODEC deletes older ones only after a checked copy of `memory.db` for that
day.

**Staying offline.** Keep the local model, leave the "Search the web" toggle off, leave `vision_provider` unset,
and do not connect Google, Telegram, connectors or claude.ai. In Chat the `web_search` skill then sends nothing either.
By voice, the wake word or the Flash box on Home, a phrase such as "who is" or "look up" still runs the `web_search`
skill (section 2.2); switch that skill off on the Skills page if you want none. The
update check then remains the only request CODEC makes on its own (plus the licence key on the paid app).

## 6. GDPR (paid tier) — Art. 13 disclosures

- **Controller:** AVA Digital LLC (contact: `privacy@avadigital.ai`).
- **Categories of personal data:** account and licence identifiers; any personal data *you* choose to include in
  prompts, messages or files you ask CODEC to act on. CODEC does not independently collect special-category data.
- **Legal basis:** performance of the service contract (Art. 6(1)(b)) for the licence and paid features; legitimate
  interest (Art. 6(1)(f)) for local operation; consent (Art. 6(1)(a)) for any optional cloud feature you enable.
- **Recipients and processors**, only for the features you use (section 2): GitHub (update check), AVA Digital LLC
  (licence key, cloud proxy), Google (Gemini through the proxy or your own key, Workspace, push), Anthropic
  (claude.ai connector), Cloudflare (tunnel), DuckDuckGo or Serper (search), Telegram, Apple (iMessage, push),
  Mozilla or Microsoft (push), Pexels, and any cloud model provider or connector you add yourself.
- **International transfers:** the cloud proxy's model provider and other cloud services may process data outside
  the EU (mostly the US), under their own SCCs or DPA where applicable.
- **Retention:** local data per sections 1 and 5 (you control deletion); licence records per the AVA backend's
  policy.
- **Your rights:** access, rectification, erasure, restriction, portability and objection, through
  `privacy@avadigital.ai`. You can delete local data yourself (section 5).

## 7. EU AI Act — Art. 50 transparency

CODEC is an AI system that interacts with you directly, and you always know you are talking to an AI (it is the
purpose of the product). CODEC does not generate deep-fakes or manipulate users. Agent actions that touch your
files, apps or outside services are gated behind consent (strict consent for destructive operations) and recorded
in the local audit log.

## 8. Children

CODEC is not directed at children under 16 and should not be used to process children's personal data. It is
consumer and prosumer software, not a compliance-grade system for HIPAA, PCI-DSS or FERPA work.

## 9. Changes and questions

This page is versioned with the code; changes go through the same review. Material changes are noted in
`CHANGELOG.md`.

- Security disclosure: see `SECURITY.md`.
- Anything else: `github.com/AVADSA25/codec/issues` or `privacy@avadigital.ai`.

*Last updated: 2026-11-12.*
