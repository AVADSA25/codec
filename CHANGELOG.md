# Changelog

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
- **Daily Briefing** — Lucy-quality executive morning report: real Google Calendar events, live weather, crypto markets (BTC/ETH/SOL via CoinGecko), Top 10 ranked news from 9 RSS feeds (FT, Reuters, BBC, Ars Technica, The Verge, TechCrunch, Al Jazeera, Nature, NPR), Gmail inbox count, pending tasks, motivational quote + joke. Delivered on both iMessage and Telegram
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
