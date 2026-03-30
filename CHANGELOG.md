# Changelog

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
- Setup wizard: 9 steps, 49 skills, CODEC features configuration

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
- Agent name configurable (default: C, personal: Mike)
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
