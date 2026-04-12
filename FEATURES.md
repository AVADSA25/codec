# CODEC — Full Product Breakdown

> 234 features · 56 skills · 378 tests · 33,900 lines of code

---

## 1. CODEC Core — The Command Layer (26 features)

| # | Feature |
|:-:|---|
| 1 | Push-to-talk via configurable hotkeys (F13/F18/F16) |
| 2 | Wake word detection ("Hey CODEC" + 6 configurable phrases) |
| 3 | Wake energy auto-clamping (50-1500 range with auto-warn on misconfiguration) |
| 4 | WebSocket real-time voice pipeline (PCM16 streaming) |
| 5 | WebSocket auto-reconnect (exponential backoff 1s-30s, max 5 retries) |
| 6 | WebSocket ping/heartbeat (15s keepalive) |
| 7 | Whisper STT integration (whisper-large-v3-turbo, multilingual auto-detect) |
| 8 | Kokoro TTS with warm-up (+ macOS `say` fallback + disabled mode) |
| 9 | Streaming LLM responses with real-time transcript chunks |
| 10 | Voice interrupt detection (mic RMS threshold cancels TTS) |
| 11 | Screenshot vision context during calls (screencapture + Qwen Vision) |
| 12 | Document input (file picker: .txt, .md, .csv, .json, .py, .pdf, images) |
| 13 | Draft detection and screen-aware reply composition |
| 14 | Live mic energy ring visualization (RMS-driven CSS animations) |
| 15 | Audio playback queue with sequential buffer decoding |
| 16 | Call timer display |
| 17 | State-reactive UI (idle/listening/speaking/processing/analyzing_screen) |
| 18 | Hold-to-talk indicator on avatar |
| 19 | Server webcam photo capture + analysis |
| 20 | Live webcam MJPEG PIP (drag, expand, snapshot) |
| 21 | Targeted memory context injected per voice turn |
| 22 | Skill triggering during voice calls (lazy-loaded from SkillRegistry) |
| 23 | Echo cooldown after TTS (1.2s mic silence post-speak) |
| 24 | Noise word filtering (40+ common Whisper artifacts) |
| 25 | Max context turns limiting (20 turns to keep LLM fast) |
| 26 | Vision Mouse Control — see screen, click UI elements by voice via UI-TARS |

---

## 2. CODEC Chat — 24 features

| # | Feature |
|:-:|---|
| 1 | 250K context window deep chat |
| 2 | Persistent conversation history with sidebar |
| 3 | Multi-file upload (text, PDF extraction, image analysis) |
| 4 | Drag-and-drop file attachment |
| 5 | Image upload + Vision API analysis |
| 6 | Markdown rendering (code blocks, bold, italic, links) |
| 7 | Copy-to-clipboard on any message |
| 8 | Voice input via Web Speech API |
| 9 | Chat mode / Agent mode toggle |
| 10 | 12 pre-built agent crews (dropdown selection) |
| 11 | Custom Agent Builder (name, role, tools, max iterations) |
| 12 | Save/load custom agent configurations |
| 13 | Agent crew scheduling from chat (daily cron) |
| 14 | Web search toggle (auto-trigger on query detection) |
| 15 | Streaming typing indicator |
| 16 | Session auto-save to server |
| 17 | FTS5 memory integration (conversations indexed for search) |
| 18 | Toast notification system |
| 19 | Notification bell with unread count (30s polling) |
| 20 | Server webcam photo + live PIP |
| 21 | Light/dark theme toggle (persistent localStorage) |
| 22 | Session lock (logout) button |
| 23 | Flash Chat (quick command panel with filtered history) |
| 24 | Flash Chat Enter-key send + source-filtered messages |

---

## 3. CODEC Dashboard — 32 features

| # | Feature |
|:-:|---|
| 1 | FastAPI web dashboard (port 8090) with 75+ API endpoints |
| 2 | PWA manifest (installable on mobile/desktop) |
| 3 | Flash Chat panel |
| 4 | History panel (session browser with full conversation replay) |
| 5 | Audit log panel (filterable by 16 event categories) |
| 6 | Settings panel with full config editing |
| 7 | Config input validation rules |
| 8 | Sensitive field masking (API keys as ****) |
| 9 | Skills list display |
| 10 | Stats grid (system metrics) |
| 11 | Touch ID biometric authentication |
| 12 | PIN code authentication (SHA-256) |
| 13 | PIN brute-force rate limiting (5 attempts + 5-min lockout) |
| 14 | TOTP 2FA (setup, confirm, verify, enable, disable + QR code) |
| 15 | Session management with configurable expiry |
| 16 | Persistent auth sessions across PM2 restarts |
| 17 | CSRF protection (double-submit cookie pattern) |
| 18 | Content Security Policy middleware |
| 19 | E2E encryption (ECDH P-256 + AES-256-GCM) |
| 20 | E2E key persistence across restarts (~/.codec/.e2e_keys.json) |
| 21 | Client-side E2E auto-renegotiation on 428 (all 5 HTML pages) |
| 22 | CORS middleware with restricted origins |
| 23 | Notification system with persistent storage |
| 24 | File upload with drag-and-drop |
| 25 | Voice input (mic button) |
| 26 | Live voice call button |
| 27 | Health check endpoints (/api/health, /api/status) |
| 28 | Cortex neural map (28 nodes, 7 zones, live activity feed) |
| 29 | Editable voice trigger manager (custom triggers per skill) |
| 30 | Keyboard shortcuts reference panel |
| 31 | Trigger persistence (~/.codec/custom_triggers.json) |
| 32 | Screenshot + webcam capture buttons |

---

## 4. CODEC Vibe — 20 features

| # | Feature |
|:-:|---|
| 1 | Monaco Editor (full IDE, same engine as VS Code v0.45.0) |
| 2 | Multi-language support (16+ languages + auto-detect) |
| 3 | AI chat panel for vibe coding |
| 4 | Voice input for code descriptions |
| 5 | Code execution (Run button with output console) |
| 6 | Live Preview panel (sandboxed iframe) |
| 7 | Inspect mode for element inspection |
| 8 | Save file to disk |
| 9 | Copy code to clipboard |
| 10 | Save as CODEC Skill |
| 11 | Test Skill (invoke run() function) |
| 12 | Skill Forge modal (3 modes: Paste Code, GitHub URL, Describe) |
| 13 | Project management sidebar (sessions) |
| 14 | Resizable panels (drag handle) |
| 15 | Output console panel |
| 16 | DOMPurify sanitization on all rendered content |
| 17 | Server webcam photo + live PIP |
| 18 | Light/dark theme toggle (syncs Monaco theme) |
| 19 | Skill review + approval workflow (human-in-the-loop) |
| 20 | URL import in Skill Forge (fetch code from GitHub raw URLs) |

---

## 5. CODEC Agents — 20 features

| # | Feature |
|:-:|---|
| 1 | Local multi-agent framework (zero external dependencies, ~800 lines) |
| 2 | Agent dataclass (name, role, tools, max_tool_calls, thinking mode) |
| 3 | Async agent execution with tool-call loop |
| 4 | Tool-call input validation (regex + length checks) |
| 5 | 7 built-in tools (web_search, web_fetch, file_read, file_write, google_docs, shell_execute, image_generate) |
| 6 | Lazy skill tool loading via SkillRegistry |
| 7 | HTTP connection pooling (httpx reuse) |
| 8 | Dangerous command blocking (via is_dangerous()) with audit logging |
| 9 | Google Docs creation with rate-limiting/dedup (60s cooldown) |
| 10 | File path traversal prevention |
| 11 | Output truncation (10K tools, 5K shell) |
| 12 | Structured audit logging |
| 13 | Tasks page: Schedules tab |
| 14 | Tasks page: History tab |
| 15 | Tasks page: Reports tab |
| 16 | Tasks page: Heartbeat tab |
| 17 | Crew status polling (4s interval) |
| 18 | Custom agent creation via API |
| 19 | 12 pre-built crews (Deep Research, Daily Briefing, Competitor Analysis, Trip Planner, Email Handler, Social Media, Code Review, Data Analysis, Content Writer, Meeting Summarizer, Invoice Generator, Custom) |
| 20 | Agent crew scheduling (cron-like, day/hour/minute selection) |

---

## 6. CODEC Skills — 59 features (56 skills + 3 infrastructure)

### Infrastructure

| # | Feature |
|:-:|---|
| 1 | SkillRegistry with AST-based lazy loading (parse metadata without importing) |
| 2 | Skill dispatch with fallback (match_all_triggers, try-next-on-None) |
| 3 | Skill Marketplace (install, search, list, update, remove, publish) |

### 56 Built-in Skills

| Category | Skills |
|---|---|
| **Google Workspace** | google_calendar, google_docs, google_drive, google_gmail, google_keep, google_sheets, google_slides, google_tasks |
| **Chrome Automation** | chrome_automate, chrome_click_cdp, chrome_close, chrome_extract, chrome_fill, chrome_open, chrome_read, chrome_scroll, chrome_search, chrome_tabs |
| **System Control** | app_switch, brightness, clipboard, file_search, network_info, process_manager, system_info, terminal, volume |
| **Vision & Mouse** | mouse_control (UI-TARS vision click), screenshot_text |
| **AI & Content** | ai_news_digest, create_skill, skill_forge, translate, web_search, memory_search |
| **Utilities** | bitcoin_price, calculator, generate_qr_code, json_formatter, notes, password_generator, pomodoro, reminders, time_date, timer, weather |
| **Smart Home** | philips_hue |
| **Media** | music |
| **Messaging** | lucy (AI persona) |
| **Dev Tools** | ask_mike_to_build, ax_control, openai_demo_runner, scheduler_skill |

---

## 7. CODEC Infrastructure — 30 features

| # | Feature |
|:-:|---|
| 1 | Centralized config system (~/.codec/config.json) |
| 2 | Configurable LLM provider (MLX, OpenAI-compatible, Ollama, cloud) |
| 3 | Configurable Vision model (Qwen VL, port 8082) |
| 4 | UI-TARS integration (dedicated UI-specialist vision, port 8083) |
| 5 | Configurable TTS (Kokoro, macOS say, disabled) |
| 6 | Configurable STT (Whisper HTTP, multilingual auto-detect) |
| 7 | Configurable hotkeys (F-keys or laptop mode) |
| 8 | Dangerous command pattern detection (46+ patterns) |
| 9 | Draft/screen keyword detection |
| 10 | Whisper transcript post-processing (hallucination/stutter removal) |
| 11 | Session runner with resource limits (120s CPU, 512MB RAM) |
| 12 | Session command preview dialog (Allow/Deny) |
| 13 | Context compaction (LLM-based summarization) |
| 14 | MCP Server (skills as MCP tools for Claude, Cursor, VS Code) |
| 15 | MCP input validation (type checks, length limits, audit logging) |
| 16 | MCP opt-in/opt-out tool exposure per skill |
| 17 | MCP memory search + recent memory tools |
| 18 | Search result TTL caching (5-min TTL, 100 entries, thread-safe) |
| 19 | Dual search backends (DuckDuckGo + Serper.dev) |
| 20 | FTS5 full-text search memory (BM25 ranking, injection prevention) |
| 21 | SQLite WAL mode with busy timeout |
| 22 | Heartbeat system (5 parallel service health checks) |
| 23 | Daily database backup with 7-day rotation |
| 24 | Scheduler (cron-like crew scheduling with dedup) |
| 25 | Audit logging across 16 categories (50MB rotation, JSON-line) |
| 26 | Process watchdog (auto-kills stuck processes >500MB RAM, <0.5% CPU) |
| 27 | iMessage agent (wake word trigger, vision, voice notes, 3 smart agents) |
| 28 | Telegram bot (DM support, conversation memory, markdown, voice notes) |
| 29 | AppKit overlay notifications (float above fullscreen, tkinter fallback) |
| 30 | AppleScript paste integration (reliable cross-app clipboard paste) |

---

## 8. CODEC Dictate — 15 features

| # | Feature |
|:-:|---|
| 1 | Hold Cmd+R to record, release to paste at cursor |
| 2 | Live typing mode (press L — words appear at cursor in real-time) |
| 3 | Multilingual transcription (Whisper auto-detect, 99 languages) |
| 4 | Draft detection and LLM refinement (grammar, tone, meaning) |
| 5 | Floating recording overlay (orange border, pulsing red dot) |
| 6 | Processing indicator overlay (blue) |
| 7 | Live typing overlay (green, real-time transcript) |
| 8 | Hallucination filter (blocks Whisper noise artifacts) |
| 9 | atexit + SIGTERM cleanup (prevents orphaned subprocesses) |
| 10 | AppleScript paste (replaces unreliable pyautogui on macOS) |
| 11 | PTT lock mode (double-tap for hands-free) |
| 12 | Configurable recording hotkey |
| 13 | Sox audio capture with auto-PATH resolution |
| 14 | Whisper HTTP endpoint integration (port 8084) |
| 15 | PM2 managed service with crash recovery |

---

## 9. CODEC Instant — 12 features

| # | Feature |
|:-:|---|
| 1 | System-wide right-click AI services (works in any app) |
| 2 | Proofread (grammar, spelling, punctuation) |
| 3 | Elevate (make text professional) |
| 4 | Explain (simplify + voice-over via Kokoro TTS) |
| 5 | Translate (any language to English + voice-over via Kokoro TTS) |
| 6 | Reply (compose contextual response with :tone syntax) |
| 7 | Prompt (optimize AI prompts) |
| 8 | Read Aloud (TTS via Kokoro) |
| 9 | Save (save to Apple Notes + local backup) |
| 10 | Clipboard integration (read selection, write result) |
| 11 | AppleScript paste for reliable cross-app insertion |
| 12 | TTS spawned as separate subprocess (survives parent exit) |

---

## Summary

| Product | Features |
|---|:-:|
| CODEC Core | 26 |
| CODEC Chat | 24 |
| CODEC Dashboard | 32 |
| CODEC Vibe | 20 |
| CODEC Agents | 20 |
| CODEC Skills | 59 |
| CODEC Infrastructure | 30 |
| CODEC Dictate | 15 |
| CODEC Instant | 12 |
| **TOTAL** | **238** |

**238 features · 56 skills · 378 tests · 33,900 lines of code**

---

<p align="center">
  Built by <a href="https://avadigital.ai">AVA Digital LLC</a> · MIT License
</p>
