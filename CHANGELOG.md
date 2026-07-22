# Changelog

## v3.5.0 (2026-07-22)
### Changed
- **CODEC Pilot parked.** The browser-automation pillar is withdrawn from the product: the Pilot tab is removed from the dashboard and its product card from the Cortex map. Pilot needs a far deeper build than the rest of CODEC to be trustworthy — Google blocks account sign-in from any CDP-controlled browser (not a setting we can change), and cookie walls plus bot challenges make a large share of real sites unusable. The code is parked, not deleted: it lives in its own `codec-pilot` repo and the runner is untouched, so it can return when it earns its place.
- **CODEC Project folded into CODEC Overview.** Project mode is a capability of the dashboard rather than a product in its own right. No functionality changes — plan → approve → autonomous run all work exactly as before.
- **9 products → 7.** Core · Chat · Dashboard · Vibe · Agents · Dictate · Instant.

### Added
- **Claim-to-artifact matching (`codec_claim_check`).** CODEC may not claim what it did not do. Every real action is already recorded, so a claim of action with no corresponding action is false by construction — not by opinion. Catches both impossible capabilities ("I'll remember this for future sessions") and unbacked actions ("saved to your Desktop" with no file skill run). Covers the streaming and non-streaming chat paths. Tuned for false negatives over false positives: half the tests are false-positive guards.
- **Standing rules that persist (`codec_standing_rules` + `standing_rules` skill).** "add a standing rule: …" writes `~/.codec/standing_rules.json` and appends to the chat system prompt every turn, surviving restarts. Additive by design — deliberately not `prompt_overrides.json`, whose `chat` key replaces the entire system prompt.
- **Generated skills are checked against the world.** `create_skill` now refuses code that calls a hostname which does not resolve, after it shipped a moon-phase skill calling an invented `api.moon.ph` that failed every run.

### Fixed
- **Chat could hang with no reply.** A long paste whose text matched a destructive skill trigger fired the consent gate, which blocked the request thread for its full 600s timeout. Skills no longer auto-fire above 2000 characters — a paste is a document, not a command.
- **"no" now declines a consent prompt.** Refusals were treated as ambiguous and burned an in-memory attempt counter, so from a fresh process a prompt could never be closed; one had been stuck for 13 days.
- **Copy button did nothing on any message containing an apostrophe** — the text was embedded in an inline `onclick`, so `don't` ended the JS string. Also closed the injection vector that came with it.

## v3.2.0 (2026-05-25)
### Added
- **In-app auto-update (Sparkle-compatible).** CODEC now checks for new releases and can update itself. A pure-Python client (`codec_update.py`) reads a signed Sparkle appcast and verifies every download's **Ed25519 signature** against the embedded `SUPublicEDKey` before installing — a tampered build is refused. New dashboard endpoints `GET /api/update/check` + `POST /api/update/download`, plus an in-app update banner ("Download & open") that polls on load and every 6h.
- **GitHub-hosted update feed.** Each release (signed `.dmg` + appcast) is published to `AVADSA25/codec-updates`; the app polls a permanent `releases/latest/download/appcast.xml` URL that always resolves to the newest version. Host is a one-line switch (`sparkle_feed_url` config / `CODEC_APPCAST_PREFIX`) for a later move to a custom domain or Cloudflare R2.
- **Appcast generation in the release pipeline.** `release_macos.sh` regenerates the Ed25519-signed feed automatically after building the DMG.

### Fixed
- **Gatekeeper signing.** Bundled Python now lives under `Contents/Resources/python` instead of `Frameworks/` — a bare Python tree under `Frameworks/` is treated as a nested bundle and broke the code-signing seal (Gatekeeper rejected the app). Launcher, signing, and bundler scripts updated to match.
- **Release pipeline.** Corrected a false "(dry run)" status line; the DMG is now notarized + stapled so it opens cleanly on a never-online Mac.
- **CI green + clean.** Resolved stacked CI gate failures (ruff lint, trusted-skill manifest regen after the UI-TARS port fix, stale packaging-test assertions) and removed a phantom `codec` submodule gitlink that tripped `actions/checkout` on every run.

### Changed
- **Unified model port.** All Qwen MLX endpoints reconciled to `:8083` (was split `:8081`/`:8082`); the UI-TARS vision model is served from the same unified server.

## v3.1.0 (2026-05-25)
### Security
- **Pilot security-hardening wave (PP-1…PP-12)** — full adversarial audit remediation of the browser-automation pillar. AST safety gate at skill-approval time (`skill_review.py` refuses to activate dangerous compiled skills), untrusted-input fencing on the LLM selector-rescue prompt (`replay.py` `build_rescue_prompt` / `wrap_untrusted` so page text can't redirect element selection), irreversible-click blocking on replay unless `PILOT_ALLOW_DESTRUCTIVE=1`, path/glob-traversal neutralization in `slugify()` lookups, and a forensic audit trail (`audit()`) on every skill write/approve/reject/block.
- **`pilot.lucyvpa.com` tunnel removed** from the Cloudflare ingress after an RCE finding — Pilot is local-only again pending re-hardening.

### Added
- **Conversational continuity in Project mode** — a Project-mode chat thread now binds to the agent it drafts (`_activeAgentId`). Follow-up messages route to that running agent (status queries, mid-run instructions) instead of spawning a duplicate project. Pulsing "Talking to …" chip with one-click exit; auto-clears on terminal status.
- **Auto-grant of user-typed paths** — `codec_agent_plan.merge_user_paths_into_manifest()` extracts `~/…`, `$HOME/…`, `/Users/<n>/…` paths from the project description and injects them into the plan's permission manifest (write-verb heuristic for read vs write), so a path the user typed themselves doesn't trigger a mid-run `blocked_on_permission`. Sensitive paths (`~/.ssh`, `~/.aws`, `/etc`, …) stay blocklisted.
- **Voice in / voice out** — Kokoro TTS now actually speaks assistant replies when "Voice Replies" is on (the toggle was previously inert); per-message Speak button; Speak toggle promoted into the composer mode-bar. Pilot tab gets its own 🎤 dictate + 🔊 speak controls and announces run/replay status aloud.
- **Live preview panel** — `GET /api/agents/{id}/files` plus a slide-out that shows the most-recently-modified files in a running agent's project folder (5s poll), so you can watch output appear without leaving the chat.
- **Cortex neural map → 9 products** — added Pilot, Pilot Chrome, Project, Plan Draft, Marketplace, Observer, Scheduler, Audit, Watchdog, MCP Server nodes (28 → 38) with 31 new edges; product cards for CODEC Pilot (#8) and CODEC Project (#9).

### Changed
- **2026 palette refresh** — calmer brand accent (`#d97757` dark / `#b85a3a` light, was pure `#E8711A`) and a muted Tailwind-500 node palette across the Cortex map. Same identity, far less "Windows-XP-bright."
- **F-wave engineering hygiene** — ruff baseline + CI lint gate (F-4), versioning discipline with `VERSION` single source of truth + CHANGELOG-driven tag helper (F-5), `pyproject.toml` packaging metadata (F-15), pricing docs (F-11), GitHub Discussions (F-12), `lucy`→`delegate` skill rename (F-6), orphan-screenshot cleanup (F-8).
- **Docs** — README/FEATURES/Cortex updated to the 9-product offering; refreshed `cortex.png` (9-product map, calmer palette).

### Fixed
- **Tasks page completely dead in the browser** — a stray `});` left after the UX overhaul was a hard SyntaxError that halted *all* inline JS, so the theme preference was ignored (Tasks flipped to dark) and Scheduled Tasks froze on "Scheduled Tasks…". Removed it and consolidated a duplicate `showTab()` that had dropped the reports-load behavior.

## v2.3.0 (2026-05-13)
### Added
- **CODEC Project — promoted to 9th product.** Drop-a-project autonomy was already shipped as the Phase-3 substrate (Steps 1-10 + 5 architectural reviews, Apr-May 2026), wired into Chat → Project mode. This release elevates it to its own product slot in README + Cortex grid since it runs autonomously on its own PM2 daemon (`codec-agent-runner`) for hours rather than turn-by-turn. Implementation lives in `codec_agent_plan.py` (planner), `codec_agent_runner.py` (execution daemon), `codec_agent_messaging.py` (reply queue), with per-agent state at `~/.codec/agents/<id>/` and a two-tier permission system (per-agent manifest + global allowlist at `~/.codec/agent_global_grants.json`). Plan-hash tamper detection, resume-after-restart, multi-agent concurrency capped at 3, AskUserQuestion + strict-consent gates, step budgets, 17 new audit event types. See `docs/PHASE3-COMPLETE.md`.
- **CODEC Pilot — 8th product, browser-automation pillar.** Dedicated headless Chromium on CDP port 9223 (separate from user's daily Chrome on 9222), driven live by Qwen and visible in the dashboard. Three modes: agent (natural-language tasks), teach (record-by-doing), replay (deterministic re-execution).
- **`pilot/` module suite** (11 files) under `~/codec/pilot/`:
  - `pilot_chrome.py` — Playwright lifecycle wrapper, persistent profile at `~/.codec/pilot_chrome_profile/`
  - `snapshot.py` — Indexed-DOM accessibility-tree snapshot (`<500ms` typical, max 150 elements per page, ARIA-role filtered)
  - `pilot_agent.py` — ReAct loop with 8-action vocabulary (`navigate/click/type/scroll/wait/extract/select_option/done`), 30-step budget, hallucinated-index validation, StubLLM offline fallback
  - `pilot_runner.py` — FastAPI on PM2 port 8094, 30 endpoints, CORS-enabled, MJPEG live stream at `/screenshot/stream` (~3 fps, 350 KB/s)
  - `trace.py` — Per-action JSON traces persisted to `~/.codec/pilot_traces/{run_id}/trace.json` with target_xpath/css/name/role per step
  - `compiler.py` — Trace → Replayer-based Python skill template
  - `replay.py` — **3-tier reliability ladder**: stored XPath (3× retries × 500ms backoff) → CSS selector (1× × 2s) → LLM rescue (Qwen re-snapshots and finds element by accessible name). Worst-case 12s per stuck step.
  - `skill_review.py` — **Approval gate**: compiled skills land in `~/.codec/skills/.pending/`, require human approval via dashboard before activating in `~/.codec/skills/pilot_*.py`. Blocks prompt-injection-spawned auto-registration.
  - `hitl.py` — Human-in-the-loop: `pause / resume / inject / takeover / handback` over HTTP
  - `screencast.py` — Background JPEG frame capture for per-run trace replay
  - `config.py` — Centralized constants (ports, paths, budgets, action vocabulary)
- **Tasks → Pilot dashboard tab** in `codec_tasks.html`:
  - Live MJPEG screenshot panel with smart polling fallback
  - Navigate / Snapshot / Click [N] / Type [N] quick-action controls
  - "Teach mode" record bar (● Record → drive → ■ Stop & Save)
  - Recent Runs with inline ▶ Replay and 💾 Compile buttons per row
  - Pending Skills panel with source preview, ✓ Approve / ✕ Reject gate
  - HITL pause/resume controls on selected run detail
- **Cloudflare tunnel exposure** — `pilot.lucyvpa.com` mapped to `localhost:8094` for off-LAN dashboard access
- **PM2 service `pilot-runner`** added to `ecosystem.config.js` (autorestart, 2GB memory cap, isolated log files)
- **Approved skills auto-expose as MCP tools** — generated `pilot_{slug}.py` files carry `SKILL_NAME`/`SKILL_DESCRIPTION`/`SKILL_TAGS` metadata, registered by CODEC's SkillRegistry, callable from voice / Chat / Scheduler / Claude Code / Cursor / VS Code

### Fixed
- **Pilot tab failed-to-fetch** — added CORS middleware to pilot-runner so the dashboard's HTTPS origin can call HTTP localhost:8094 across the preflight boundary
- **Pilot tab failed-to-create-run** — CODEC's global `window.fetch` wrapper was mutating POST bodies, causing FastAPI 422; replaced `_pilotFetch` with XMLHttpRequest to bypass the wrapper entirely
- **Duplicate Pilot JS block** — git merge of `680f33d` inserted a second `_pilotFetch` definition; removed lines 1409-1659 from `codec_tasks.html`

## v2.1.1 (2026-04-14)
### Added
- **F5 hands-free live typing** — tap F5 (no modifier) and speak; words stream to the cursor in real-time in any text field. Tap F5 again to stop. Visible red `LIVE · press F5 to stop` pill top-center with pulsing dot
- **Pipelined live recording** — producer thread records 2s sox chunks back-to-back into a queue; consumer thread transcribes in parallel. Zero audio dropped between chunks
- **Agent job notifications** — Deep Research, Daily Briefing and other agent completions now surface in the Reports section with Google Docs links. Running/success/error states tracked live
- **TTS echo filter** — voice transcriptions now strip CODEC's own TTS output when the mic re-captures it, preventing skill mis-triggers

### Fixed
- **Dictate typing into Chrome URL bar** — live mode was triggered by ⌘+L, which Chrome/Safari/Firefox intercept as "focus address bar". Trigger moved to F5. Paste now via `pyautogui.hotkey` (CGEventPost) instead of `osascript "System Events" keystroke` — no app activation, focus stays in the user's clicked field
- **Whisper auto-translation** — live dictation occasionally translated English speech to other languages. Now pinned to `language=en, task=transcribe`
- **Live pill wouldn't close on stop** — tkinter mainloop ignored SIGTERM; added SIGKILL fallback so F5-to-stop reliably dismisses the overlay
- **Mouse control UI-TARS integration** — was returning `(-1,-1)` due to mismatched prompt format. Switched to native `<|box_start|>(x,y)<|box_end|>` parsing, bumped screenshot downscale to 1920px for retina accuracy
- **F18 voice recording sounds** — Glass.aiff (press) + Pop.aiff (release) for clear audio feedback

### Changed
- Dictate startup banner and classic-mode hint updated to reference F5 (was L)
- Removed large text-preview overlay during live dictation — Gemini-style direct paste at cursor is cleaner and faster

## v2.1.0 (2026-04-11)
### Added
- **CODEC External** — new 8th product. First time CODEC data flows beyond the local Mac
- **iMessage integration** (`codec_imessage.py`) — trigger-based activation ("Hey CODEC" or "Good morning"), processes text, photos (vision), and voice notes (Whisper transcription). Pure Python, reads macOS Messages DB, replies via AppleScript. Inspired by [Photon's imessage-kit](https://github.com/photon-hq/imessage-kit)
- **Telegram bot** (`codec_telegram.py`) — @Codec_mf_bot, full DM support with conversation memory, Markdown responses. Supports text, photos, voice messages, and 80-second voice note briefings via Kokoro TTS
- **Daily Briefing** — executive-grade morning report: real Google Calendar events, live weather, crypto markets (BTC/ETH/SOL via CoinGecko), Top 10 ranked news from 9 RSS feeds (FT, Reuters, BBC, Ars Technica, The Verge, TechCrunch, Al Jazeera, Nature, NPR), Gmail inbox count, pending tasks, motivational quote + joke. Delivered on both iMessage and Telegram
- **Deep Report via messaging** — say "full report" on iMessage or Telegram to trigger the multi-agent Deep Research crew, outputs a 10,000-word illustrated report to Google Docs
- **Smart agents** — Restaurant Decider (location-aware dining suggestions), Accountability Coach (goal tracking + check-ins with SQLite persistence)
- **Voice briefing** — Telegram sends an 80-second audio note with the full briefing read aloud (Kokoro TTS, sentence-level chunking, female voice)
- **PM2 managed services** — `codec-imessage` and `codec-telegram` with auto-restart and log rotation
- **Photon Residency demo** — `PHOTON_DEMO.md` standalone pitch document with architecture breakdown

### Fixed
- **Google Calendar in briefing** — installed `google-api-python-client` for Python 3.14, calendar events now show in Daily Briefing
- **News readability** — blank lines between each ranked news item for cleaner formatting on mobile

## v2.0.0 (2026-04-09)
### Added
- **7-product architecture** — Core, Dictate, Instant, Chat, Vibe, Voice, Overview (Cortex + Audit folded into Overview)
- **PIN brute-force escalating lockout** — 5 attempts then progressive lockout: 30s → 60s → 2min → 5min → 15min → 30min cap (OWASP standard)
- **VAD configurable via config.json** — silence threshold, duration, min speech, echo cooldown all tunable without code changes
- **Voice interrupt after vision** — if user speaks during screen analysis, stale results are discarded immediately
- **TTS audio dedup guard** — prevents duplicate audio chunks from playing back-to-back
- **Dictate hallucination filter** — shared filter blocks Whisper noise artifacts across standard and live modes
- **atexit + SIGTERM cleanup** — dictate properly cleans up sox, overlays, and temp files on shutdown
- **MCP tool error containment** — try/except wrapper prevents stack traces from leaking to clients
- **Session eviction** — hourly background cleanup of expired auth sessions
- **Process watchdog** — kills stuck/zombie processes (>500MB RAM + <0.5% CPU for 10+ min)

### Fixed
- **Audit page blank** — field name mismatch (ev.timestamp→ev.ts, etc.) caused zero events to display
- **Whisper heartbeat** — health check was hitting `/` (404) instead of `/health`
- **Duplicate /api/health route** — removed duplicate that caused FastAPI warning on every startup
- **Skill name mismatch** — ask_mike_to_build SKILL_NAME now matches filename
- **Settings fetch error** — cortex loadSettings() now handles non-OK HTTP responses
- **Processing overlay** — dynamic timing replaces hardcoded 4s timer, overlay stays until transcription completes
- **Default audit range** — changed from 6h to 24h for better initial view

### Changed
- All version strings bumped to v2.0.0 across all services and terminal banners
- CORS explicit header whitelist (no more wildcard)
- Dangerous command patterns extended (+8 patterns)
- Overlay text sanitization (newlines, null bytes stripped)

## v1.5.1 (2026-04-05)
### Fixed
- **Security (SE-2):** Dangerous commands (rm, delete, shred) now trigger Allow/Deny dialog in voice sessions — injected full safety system into `build_session_script()` generated temp scripts
- **Mouse control:** Two-pass vision approach — rough find at 1920px then crop 1000×1000 at full 4K res for precise coordinates. Uses pixel interpretation (not normalized). cliclick cascade for reliable clicks from PM2
- **App switch routing:** Removed overly generic triggers ("show me"), word-level alias matching prevents "codec" matching "code", returns None for unknown apps so Q-Agent handles them
- **Dialog close:** All tkinter dialogs (danger preview, command preview) now use withdraw→quit→destroy pattern for instant close instead of orphaned windows

### Changed
- Vision timeout increased from 30s to 60s for 1920px images
- Overlay unified to 520×90
- Expanded dangerous command patterns in codec_config.py

## v1.5.0 (2026-03-29)
### Added
- MCP Server — 43 tools exposed to Claude Desktop, Cursor, and any MCP client
- 3 new Agent Crews: Social Media Manager, Code Reviewer, Data Analyst (8 total)
- Heartbeat system — auto-monitors all services every 30 minutes
- Wake word audio smoothing — energy threshold + confidence gate reduces false triggers
- Voice energy ring animation during CODEC Voice calls
- Web search toggle in Deep Chat
- Transcription post-processing — strips Whisper hallucinations and stutters
- PTT Lock Mode — double-tap F18 for hands-free dictation
- Voice session warmup — pre-loads context when speech detected (500ms faster)
- CODEC Read Aloud — 7th right-click service, speaks text via Kokoro TTS
- CODEC Save — 8th right-click service, saves text to Google Keep or local notes
- DuckDuckGo search fallback (zero API key needed)
- One-line installer script (install.sh)

### Changed
- Modular codebase: codec.py split into 6 focused modules (1314 → 280 lines)
- Safe eval: session scripts run as subprocess with resource limits (512MB RAM, 120s CPU)
- Context compaction: LLM summarizes old conversations, keeps last 5 raw
- Error handling: 30+ bare except:pass replaced with logged exceptions
- Logging framework: structured logging replaces print statements
- Safety blocklist expanded to 30+ dangerous command patterns
- Setup wizard: 9 steps, 50+ skills, CODEC features configuration

### Fixed
- Command Preview window freezing (now runs as subprocess)
- Whisper hallucination false triggers ("thank you for watching", etc.)
- TTS engine "none" config not working
- Skill Forge title line syntax errors
- Memory search targeting old entries instead of recent
- Agent crews timing out through Cloudflare proxy (background job + polling pattern)
- Browser caching stale frontend after backend deploys (no-cache headers on all HTML routes)

## v1.4.0 (2026-03-28)
### Added
- CODEC Voice — own WebSocket voice pipeline (replaced Pipecat)
- CODEC Agents — own multi-agent framework with 5 crews (replaced CrewAI)
- FTS5 full-text memory search across all conversations
- Memory search skill — voice-triggered memory retrieval
- Skill Forge with URL import — paste GitHub URL, auto-converts to CODEC skill
- 5 Chrome AppleScript skills (open, close, search, read, tabs)
- 6 new system skills (file search, process manager, network info, screenshot OCR, brightness, terminal)
- CODEC Reply — 6th right-click service with :direction syntax
- CODEC Prompt — 5th right-click service, optimizes text as LLM prompt
- CODEC Translate — translates any language to English
- Command Preview UI with Allow/Deny before execution
- Deep Research — CrewAI agents + Serper web search → Google Docs report
- Vibe Code IDE — Monaco editor + AI chat + live preview + Skill Forge
- Deep Chat — 250K token context window with file upload
- Phone Dashboard PWA with Cloudflare Tunnel
- Google OAuth with 7 scopes (read+write)
- Agent name configurable (default: C)
- requirements.txt

### Changed
- PM2 consolidated to ~/codec-repo/
- README rewritten with 7 product frames and power examples
- Setup wizard expanded to 8 steps

## v1.3.0 (2026-03-27)
### Added
- Right-click Text Assistant (Proofread, Elevate, Explain)
- Vibe Code initial version
- Deep Research initial version
- Chrome skills (AppleScript-based)
- Command Preview initial version
- Wake word noise filter
- System prompts upgraded

## v1.2.0 (2026-03-26)
### Added
- Phone Dashboard PWA
- Google Workspace skills (Calendar, Gmail, Drive, Docs, Sheets, Slides, Tasks, Keep)
- Webhook delegation to external agents
- Multi-machine LAN support
- 15 built-in skills

## v1.0.0 (2026-03-24)
### Added
- Initial release
- Voice control with F-key shortcuts
- Whisper STT + Kokoro TTS
- 13 built-in skills
- Always-on wake word
- Draft and paste functionality
