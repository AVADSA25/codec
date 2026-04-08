# CODEC Product Breakdown v2.0
**Updated:** April 8, 2026 | **Repo:** github.com/AVADSA25/codec

---

## 1. CODEC Voice — 21 features
1. Push-to-talk via configurable hotkeys (F13/F18/F16)
2. Wake word detection ("Hey CODEC" + configurable phrases)
3. WebSocket real-time voice pipeline (PCM16 streaming)
4. WebSocket auto-reconnect (exponential backoff 1s->30s, max 5 retries)
5. WebSocket ping/heartbeat (15s keepalive)
6. Whisper STT integration (whisper-large-v3-turbo on port 8084)
7. Kokoro TTS with warm-up (+ macOS "say" fallback + disabled mode)
8. Streaming LLM responses with real-time transcript chunks
9. Voice interrupt detection (mic RMS threshold cancels TTS)
10. Screenshot vision context (screencapture + Qwen Vision)
11. Document input (file picker: .txt, .md, .csv, .json, .py, .pdf, images)
12. Draft detection and screen-aware reply composition
13. Live mic energy ring visualization (RMS-driven CSS animations)
14. Audio playback queue with sequential buffer decoding
15. Call timer display
16. State-reactive UI (idle/listening/speaking/processing/analyzing_screen)
17. Hold-to-talk indicator on avatar
18. Server webcam photo capture + analysis
19. Live webcam MJPEG PIP (drag, expand, snapshot)
20. Voice Replies ON/OFF toggle (persistent across all pages via localStorage)
21. TTS deduplication guard (prevents double playback)

## 2. CODEC Dictate — 12 features
1. Hold Right CMD (Cmd+R) push-to-talk recording
2. L key live typing mode (types at cursor position in real-time)
3. Draft mode (prefix "draft" to polish via Qwen3.5)
4. Recording overlay (680px, CODEC orange border, pulsing red dot)
5. Processing overlay (blue border, "Transcribing..." status)
6. Live typing overlay (green border, real-time transcript display)
7. Sox-based audio recording from macOS coreaudio
8. Whisper server transcription with hallucination filtering
9. Text paste via pyautogui CMD+V (reliable cross-app)
10. 3-second streaming chunks for live mode
11. PM2-managed with auto-restart
12. Startup banner with keyboard shortcut reference

## 3. CODEC Chat — 23 features
1. 250K context window deep chat
2. Persistent conversation history with sidebar
3. Multi-file upload (text, PDF extraction, image analysis)
4. Drag-and-drop file attachment
5. Image upload + Vision API analysis
6. Markdown rendering (code blocks, bold, italic, links)
7. Copy-to-clipboard on any message
8. Edit & re-send user messages
9. Regenerate assistant responses
10. Voice input via Web Speech API (continuous mode, stop button)
11. Chat mode / Agent mode toggle
12. 12 pre-built agent crews (dropdown selection)
13. Custom Agent Builder (name, role, tools, max iterations)
14. Save/load custom agent configurations
15. Agent crew scheduling from chat (daily cron)
16. Web search toggle
17. Streaming typing indicator with thinking animation
18. Session auto-save to server
19. FTS5 memory integration (conversations indexed for search)
20. Toast notification system
21. Notification bell with unread count (30s polling)
22. Light/dark theme toggle (persistent localStorage)
23. Session lock (logout) button

## 4. CODEC Dashboard — 30 features
1. FastAPI web dashboard (port 8090) with OpenAPI docs
2. PWA manifest (installable on mobile/desktop)
3. Quick Chat panel (Flash Chat with newest-at-bottom)
4. History panel (session browser)
5. Audit log panel (filterable by 16 event categories)
6. Settings panel with full config editing
7. Config input validation rules
8. Sensitive field masking (API keys as ****)
9. Skills list display with trigger info
10. Stats grid (system metrics)
11. Touch ID biometric authentication
12. PIN code authentication (SHA-256)
13. PIN brute-force rate limiting (5 attempts + 5-min lockout)
14. TOTP 2FA (setup, confirm, verify, enable, disable + QR code)
15. Session management with configurable expiry
16. Persistent auth sessions across PM2 restarts
17. CSRF protection (double-submit cookie pattern)
18. Content Security Policy middleware
19. E2E encryption (ECDH P-256 + AES-256-GCM)
20. E2E key persistence across restarts (~/.codec/.e2e_keys.json)
21. Client-side E2E auto-renegotiation on 428 (all HTML pages)
22. CORS middleware (explicit header whitelist)
23. Notification system with persistent storage
24. Page customization UI toggles (hide/show buttons per page)
25. File upload with drag-and-drop
26. Voice input (mic button with continuous mode)
27. Live voice call button
28. Health check endpoints (/api/health, /api/status)
29. Consistent burger menu across all pages (7 pages)
30. 75+ API routes across command, memory, vision, auth, agents

## 5. CODEC Vibe — 18 features
1. Monaco Editor (full IDE with syntax highlighting, v0.45.0)
2. Multi-language support (16+ languages + auto-detect)
3. AI chat panel for vibe coding
4. Voice input for code descriptions
5. Code execution (Run button)
6. Live Preview panel (sandboxed iframe)
7. Inspect mode for element inspection
8. Save file to disk
9. Copy code to clipboard
10. Save as CODEC Skill
11. Test Skill (invoke run() function)
12. Skill Forge modal (3 modes: Paste Code, GitHub URL, Describe)
13. Project management sidebar
14. Resizable panels (drag handle)
15. Output console panel
16. DOMPurify sanitization (v3.0.8)
17. Server webcam photo + live PIP
18. Light/dark theme toggle (syncs Monaco)

## 6. CODEC Cortex — 8 features
1. Product showcase grid (7 CODEC products)
2. Interactive neural network SVG map
3. Real-time activity feed
4. Skills panel with search
5. Settings float panel
6. Network graph / Products grid view switcher
7. Detailed log viewer per event
8. Responsive product cards with status indicators

## 7. CODEC Audit — 10 features
1. Filterable audit log (16 categories: command, skill, llm, auth, error, scheduled, voice, vision, tts, stt, system, security, hotkey, screenshot, config, draft)
2. Category pill multi-select with color coding
3. Search input for event summaries
4. Event timeline with colored category dots
5. Stats bar (total events, errors, commands, skills, LLM calls)
6. JSON-line audit file with 50MB rotation (1 backup)
7. Thread-safe logging with UTC timestamps
8. Expandable event detail panels
9. Always-on 24h view with 200 event limit
10. Auto-refresh capability

## 8. CODEC Agents — 19 features
1. Local multi-agent framework (zero external dependencies)
2. Agent dataclass (name, role, tools, max_tool_calls, thinking mode)
3. Async agent execution with tool-call loop
4. Tool-call input validation (regex + length checks)
5. 7 built-in tools (web_search, web_fetch, file_read, file_write, google_docs_create, image_generate, vision)
6. Lazy skill tool loading via SkillRegistry
7. HTTP connection pooling (httpx reuse)
8. Dangerous command blocking (via is_dangerous()) with audit logging
9. Google Docs creation with rate-limiting/dedup (60s cooldown)
10. File path traversal prevention
11. Output truncation (10K tools, 5K shell)
12. Structured audit logging
13. Tasks page: Schedules tab
14. Tasks page: History tab
15. Tasks page: Reports tab
16. Tasks page: Heartbeat tab
17. Crew status polling (4s interval)
18. Custom agent creation via API
19. 4 crew types (General, Research, Analysis, Writing)

## 9. CODEC Skills — 63 features (60 skills + 3 infrastructure)

### Infrastructure:
1. Skill Registry with AST-based lazy loading
2. Skill dispatch fallback (match_all_triggers sorted by specificity, try-next-on-None)
3. Skill Marketplace (install, search, list, update, remove, publish)

### 60 Built-in Skills:
ai_news_digest, app_switch, ask_mike_to_build, ax_control, bitcoin_price, brightness, calculator, case_insensitive_dict_and_lookup_dict, chrome_automate, chrome_click_cdp, chrome_close, chrome_extract, chrome_fill, chrome_open, chrome_read, chrome_scroll, chrome_search, chrome_tabs, clipboard, codec, create_skill, file_ops, file_search, google_calendar, google_docs, google_drive, google_gmail, google_keep, google_sheets, google_slides, google_tasks, json_formatter, lucy, memory_search, mouse_control, music, network_info, notes, openai_demo_runner, password_generator, philips_hue, pm2_control, pomodoro, process_manager, python_exec, qr_generator, reminders, requests_utils, scheduler_skill, screenshot_text, skill_forge, system_info, terminal, time_date, timer, translate, volume, weather, web_search

## 10. CODEC Infrastructure — 30 features
1. Centralized config system (~/.codec/config.json)
2. Configurable LLM provider (MLX, OpenAI-compatible, custom)
3. Configurable Vision model (Qwen VL, port 8082)
4. UI-TARS integration (dedicated UI-specialist, port 8083)
5. Configurable TTS (Kokoro, macOS say, disabled)
6. Configurable STT (Whisper HTTP)
7. Configurable hotkeys
8. Dangerous command pattern detection (46+ patterns across 8 categories)
9. Draft/screen keyword detection
10. Whisper transcript post-processing (hallucination/stutter removal)
11. Session runner with resource limits (120s CPU, 512MB RAM)
12. Session command preview dialog (Allow/Deny)
13. Session correction detection and learning
14. Context compaction (LLM-based summarization, keeps 5 recent raw)
15. MCP Server (all skills as MCP tools)
16. MCP input validation (type checks, 5KB task / 10KB context limits)
17. MCP opt-in/opt-out tool exposure
18. MCP memory search + recent memory tools
19. Search result TTL caching (5-min TTL, 100 entries, thread-safe)
20. Dual search backends (DuckDuckGo + Serper.dev)
21. FTS5 full-text search memory (with injection prevention + BM25 ranking)
22. SQLite WAL mode with busy timeout
23. Heartbeat system (parallel health checks on 5 services)
24. Scheduler (cron-like crew scheduling)
25. Audit logging throughout all subsystems (16 categories)
26. Watchdog process monitor (kills stuck/zombie processes after 10 min idle)
27. Swift native overlay (NSPanel, menu bar integration, event JSONL poller)
28. PM2 ecosystem with 10+ managed processes
29. Daily memory DB backup (keeps 7 backups)
30. Osascript notification sanitization (injection-safe)

---

## Summary

| Product | Features |
|---------|----------|
| CODEC Voice | 21 |
| CODEC Dictate | 12 |
| CODEC Chat | 23 |
| CODEC Dashboard | 30 |
| CODEC Vibe | 18 |
| CODEC Cortex | 8 |
| CODEC Audit | 10 |
| CODEC Agents | 19 |
| CODEC Skills | 63 |
| CODEC Infrastructure | 30 |
| **TOTAL** | **234** |

**234 features** | **60 skills** | **405 tests across 22 files** | **~61,700 lines** (52K Python + 9.2K HTML + 273 Swift + 123 JS)
