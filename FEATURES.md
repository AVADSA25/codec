# Sovereign AI Workstation — Full Product Breakdown

> Engine: **CODEC v3.2** — 401 features · 89 skills · 2000+ tests · 52K+ lines of production code · 9 products

The product name is **Sovereign AI Workstation**. Throughout this document
and the codebase, **CODEC** refers to the underlying open-source engine /
codename (visible in `codec_*` Python modules, PM2 process names, and the
`~/.codec/` config directory). Sub-products keep their established names —
*CODEC Core*, *CODEC Dictate*, *CODEC Chat*, *CODEC Vibe*, etc. — because
those are concrete engine components rather than separate products.

**v2.3 adds Phase 1 (audit + plugin substrate), Phase 2 (continuous
observation + automation), Phase 3 (drop-a-project autonomous agents),
Phase 3.5 (UX polish + proactive overlay), and CODEC Pilot (the 8th product
— browser automation you can teach)** — sections 10–14 below.

Phase 3 ships `codec-agent-runner`, the autonomous-agent daemon that makes
CODEC a "real AI employee" at the substrate level — drop a project, agent
plans + builds + sends updates back proactively, with permission gates
and resume-after-restart guarantees throughout.

CODEC Pilot ships the 8th product slot: a dedicated headless Chromium driven
by Qwen, record-by-doing teach mode, deterministic XPath → CSS → LLM-rescue
replay, and an approval gate that protects the SkillRegistry from
prompt-injection-spawned skills. With Project promoted to product #9 in this
release, CODEC is now a **9-product system**.

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

## 2. CODEC Chat — 30 features

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
| 9 | Four-mode chat composer: **Chat / Think / Agents / Project** *(v2.3)* |
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
| 25 | **Project mode** — drop a project description, Qwen-3.6 drafts plan + permission manifest, you approve once, agent runs autonomously *(v2.3)* |
| 26 | **Project folder auto-creation** — every project gets `~/codec-projects/<slug>/` (Claude Code-style, openable in any IDE) *(v2.3)* |
| 27 | **Inline plan review** — Approve / Reject / View plan buttons in chat thread; plan + manifest rendered inline *(v2.3)* |
| 28 | **Agent status pills** above input — running / paused / blocked / awaiting_approval, with inline approve/pause/resume/abort actions, polls `/api/agents` every 5s *(v2.3)* |
| 29 | **Auto-escalation gate** — chat-mode message detected as multi-step → "Promote to Project mode?" prompt with 2-signal classifier (Qwen verdict + checkpoint estimate ≥3) *(v2.3)* |
| 30 | **Per-conversation silence** — first "No" silences the auto-escalation prompt for the rest of that session *(v2.3)* |

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
| 10 | Test Skill (invoke run() function) |
| 11 | Project management sidebar (sessions) |
| 12 | Resizable panels (drag handle) |
| 13 | Output console panel |
| 14 | DOMPurify sanitization on all rendered content |
| 15 | Server webcam photo + live PIP |
| 16 | Light/dark theme toggle (syncs Monaco theme) |
| 17 | Skill review + approval workflow (human-in-the-loop, exclusive path) |

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

## 6. CODEC Skills — 79 features (75 skills + 4 infrastructure)

### Infrastructure

| # | Feature |
|:-:|---|
| 1 | SkillRegistry with AST-based lazy loading (parse metadata without importing) |
| 2 | Skill dispatch with fallback (match_all_triggers, try-next-on-None) |
| 3 | Skill Marketplace (install, search, list, update, remove, publish) |
| 4 | **`SKILL_OBSERVATION_TRIGGER` declarative trigger metadata** — skills opt into auto-fire via 5 trigger types (window_title_match, clipboard_pattern, file_change, time, compound) *(Phase 2 Step 6)* |

### 75 Built-in Skills

MCP tool name shown where it differs from the file name.

| Category | Skills |
|---|---|
| **Google Workspace** (8) | google_calendar, google_docs, google_drive, google_gmail, google_keep, google_sheets, google_slides, google_tasks |
| **Chrome Automation** (10) | chrome_automate, chrome_click_cdp, chrome_close, chrome_extract, chrome_fill, chrome_open, chrome_read, chrome_scroll, chrome_search, chrome_tabs |
| **System Control** (11) | app_switch, brightness, clipboard, file_ops, file_search, file_write, network_info, process_manager, `system` (system_info), terminal, `volume_brightness` (volume) |
| **Vision & Mouse** (2) | mouse_control (UI-TARS vision click), screenshot_text |
| **AI & Content** (6) | `AI_News_Digest` (ai_news_digest), create_skill, skill_forge, translate, web_search, memory_search |
| **Memory Layer** (5) | memory_search (FTS5), memory_history (temporal facts), memory_entities (CCF map), memory_save, auto_memorize, fact_extract |
| **Utilities** (12) | bitcoin_price, calculator, json_formatter, notes, password_generator, pomodoro, `qr_generator`, reminders, `time` (time_date), timer, weather, web_fetch |
| **Communication** (2) | imessage_send, tts_say |
| **Smart Home** (1) | philips_hue |
| **Media** (1) | music (Spotify + Apple Music) |
| **Delegation** (1) | `delegate` (n8n workflow task orchestrator) |
| **Dev Tools** (5) | ax_control, pm2_control, python_exec, `scheduler` (scheduler_skill), codec (meta-dispatcher) |
| **Observability** (4) | audit_report, backup_status, health_check, notification_reader |
| **Phase 1+ — agent-facing shims** (4) | ask_user (blocking pause + strict-consent), stuck (loop detection), self_improve (audit-driven proposal), shift_report (end-of-day summary) |
| **Phase 2 first trigger** (1) | clipboard_url_fetch (first real `SKILL_OBSERVATION_TRIGGER` — auto-fetches clipboard URLs with consent gate) |

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
| 14 | MCP Server (skills as MCP tools for Claude Desktop, Claude Code, Cursor, VS Code) |
| 15 | MCP input validation (type checks, 5KB task / 10KB context limits, audit logging) |
| 16 | MCP opt-in/opt-out tool exposure per skill (blocklist for python_exec, terminal, pm2_control, process_manager) |
| 17 | MCP full tool exposure — all 75 skills (plus approved Pilot skills) available as `mcp__codec__*` tools |
| 18 | MCP tool-name sanitization preserving original SKILL_NAME for registry lookup |
| 19 | MCP memory search + recent memory tools |
| 20 | **Tiered Memory Loading** — identity.txt L0/L1 boot payload injected into every session (<200 tokens) |
| 21 | **Temporal Fact Store** — `facts` table with valid_from/valid_until/superseded_by; auto-supersession on key conflict |
| 22 | **CCF Compression** — rule-based entity abbreviation + filler stripping for recalled memory blocks (~65% token reduction) |
| 23 | **Active facts injection** — currently-valid temporal facts auto-added to system prompt on every session build |
| 24 | Search result TTL caching (5-min TTL, 100 entries, thread-safe) |
| 25 | Dual search backends (DuckDuckGo + Serper.dev) |
| 26 | FTS5 full-text search memory (BM25 ranking, injection prevention) |
| 27 | SQLite WAL mode with busy timeout |
| 28 | Heartbeat system (5 parallel service health checks) |
| 29 | Daily database backup with 7-day rotation |
| 30 | Scheduler (cron-like crew scheduling with dedup) |
| 31 | Audit logging across 16 categories (daily rotation, 30-day retention, JSON-line) |
| 32 | Process watchdog (auto-kills stuck processes >500MB RAM, <0.5% CPU) |
| 33 | iMessage agent (wake word trigger, vision, voice notes, 3 smart agents) |
| 34 | Telegram bot (DM support, conversation memory, markdown, voice notes) |
| 35 | AppKit overlay notifications (float above fullscreen, tkinter fallback) |
| 36 | AppleScript paste integration (reliable cross-app clipboard paste) |

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

## 10. Phase 1 — Agent Substrate (18 features) *(v2.3)*

The foundation that Phase 2 + 3 reuse. Audit envelope, plugin lifecycle hooks,
blocking ask-user with strict-consent, stuck-loop detection, per-checkpoint
step budget, self_improve as plugin.

| # | Feature |
|:-:|---|
| 1 | **Unified audit envelope** (`schema:1`) — every event JSON-line: `ts`, `event`, `source`, `outcome`, `transport`, `correlation_id`, `extra` |
| 2 | **Paired correlation_id contract** — multi-emit operations share one cid (Step 1 §1.4) so analytics can join `op_started → op_completed` chains |
| 3 | Daily audit log rotation + 30-day retention, append-only, thread-safe |
| 4 | `codec_audit.log_event` adapter — backward-compat wrapper for legacy callers |
| 5 | **5 plugin lifecycle hooks** — `pre_tool` / `post_tool` / `on_error` / `on_operation_start` / `on_operation_end` |
| 6 | `HookCtx` / `HookVeto` / `PluginRegistry` / `run_with_hooks` — wraps every skill dispatch at the chokepoint |
| 7 | AST-based plugin discovery — broken plugins never break startup |
| 8 | Lazy plugin module load — metadata read from disk; module imported on first hook fire |
| 9 | `hook_fired` / `hook_error` / `tool_vetoed` audit events |
| 10 | **`AskUserQuestion`** — blocking pause-and-ask via `threading.Event`, atomic state at `~/.codec/pending_questions.json`, PWA + voice answer paths |
| 11 | **§1.7 strict-consent gate** — literal verb-match for irreversible actions (generic "yes" rejected), two-strike → `ambiguous_consent` timeout |
| 12 | Voice fuzzy-match for ask-user (3-tier: substring → synonym dict → Levenshtein); strict-consent BYPASSES fuzzy |
| 13 | **Stuck detection** — per-agent ring buffer M=5; warn at N=3; escalate at N+2=5 (action: `ask_user` / `abort` / `warn_only`) |
| 14 | **Step budget** — per-turn cap (`chat=5`, `voice=5`, `mcp=None`); warn-at-N-1; one `step_budget_exhausted` audit emit per request |
| 15 | 6 Phase 1 audit events: `ask_user_question_emit/_answer/_timeout`, `stuck_warning/_escalated`, `step_budget_exhausted` |
| 16 | 3 kill switches: `ASKUSER_ENABLED` / `STUCK_DETECTION_ENABLED` / `STEP_BUDGET_ENABLED` (env vars, default true) |
| 17 | **`self_improve` migrated to plugin** — registers `post_tool` / `on_error` / `on_operation_end`; in-memory ring buffer of last 200 signals; per-tool 30-min throttle; daemon thread for LLM draft so user's tool call doesn't block |
| 18 | Self-recursion guard — `_SELF_TOOLS = {"self_improve", ""}` prevents the plugin from firing on its own emits |

---

## 11. Phase 2 — Continuous Observation + Automation (24 features) *(v2.3)*

Background daemon watches what you're doing (window / clipboard / files);
declarative skill triggers fire on patterns; end-of-day shift report consolidates
everything CODEC observed.

| # | Feature |
|:-:|---|
| 1 | **`codec-observer` PM2 daemon** — 5s tick, lazy-imports Quartz with graceful non-mac fallback |
| 2 | **`RingBuffer`** — last 10 minutes of observation snapshots, RAM-only, no disk persistence |
| 3 | **Observation injection contract (Q5 override)** — always inject for `transport=local`; cloud transports gate on possessive pronoun OR continuation phrase OR `SKILL_NEEDS_OBSERVATION` flag |
| 4 | OCR-with-retry-once (slow-poll degraded path) when `screencapture` is slow |
| 5 | **`ocr_enabled` config flag** — bypasses macOS Screen Recording prompts when permission not yet granted to `python3.13` + PM2 parent (default false until explicitly granted) |
| 6 | Image redaction — never logs raw pixels |
| 7 | Stop-noun list filters trivial captures from observation summaries |
| 8 | Observation cardinality control — one `observation_tick` per 5s |
| 9 | Slow-poll degraded mode emits `observation_tick_slow` (graceful when OCR is disabled or slow) |
| 10 | `/api/observer/buffer?debug=1` PWA debug endpoint |
| 11 | Forward-compat snapshot schema reserves keys for Step 6 + 7 |
| 12 | **5 Phase 2 Step 5 audit events** — `observation_tick`, `_slow`, `_summary_injected`, `observer_started`, `observer_stopped` |
| 13 | **`SKILL_OBSERVATION_TRIGGER` declarative metadata** — skills opt into auto-fire via 5 matcher types: `window_title_match`, `clipboard_pattern`, `file_change`, `time`, `compound` |
| 14 | Per-trigger RAM cooldowns (configurable seconds) |
| 15 | **Persistent kill state at `~/.codec/triggers_killed.json`** — atomic tmp+rename writes |
| 16 | Stable `sha8` keys per `(skill_name, trigger_type, params_hash)` so kill survives skill rename |
| 17 | `routes/triggers.py` — 3 PWA endpoints: `GET /api/triggers`, `GET /api/triggers/{key}`, `POST /api/triggers/{key}/kill` |
| 18 | `codec_ask_user.ask` confirmation gate before any non-explicitly-pre-approved trigger fires |
| 19 | 3 Step 6 audit events — `trigger_fired`, `trigger_skipped`, `trigger_killed` |
| 20 | **First real Step 6 trigger shipped** — `clipboard_url_fetch` skill auto-fetches HTTP/HTTPS URLs via consent gate (10-min cooldown per URL) |
| 21 | **`shift_report` skill** — end-of-day 5-section markdown (Completed tasks · Blocked moments · Observed work patterns · Pending decisions · Tomorrow) |
| 22 | 3 trigger paths for shift_report: time (daily HH:MM), idle (continuous idle ≥ N min), manual chat invocation |
| 23 | Per-day dedup at `~/.codec/shift_report_state.json` (atomic) — time/idle paths fire at most once per local-date |
| 24 | 2 paired Step 7 audit events — `shift_report_started` + `_completed` (extras: trigger_kind, sections_included, word_count, audit_records_scanned, duration_ms) |

---

## 12. Phase 3 — Drop-a-Project Autonomous Agents (32 features) *(v2.3)*

The flagship Phase 3 feature. Drop a project description → Qwen-3.6 drafts a
plan with explicit permission manifest → user approves once → `codec-agent-runner`
executes autonomously with permission-gated skill loops, plan-hash tamper detection,
resume-after-restart, and proactive update messages back to chat.

### Step 8 — Plan + Permission Contract (10 features)

| # | Feature |
|:-:|---|
| 1 | **`Plan` / `Checkpoint` / `PermissionManifest` dataclasses** (`schema:1`) — versioned, atomic R/W, JSON-roundtrippable |
| 2 | **Qwen-3.6 plan drafter** (local-only, no cloud fallback per Q1) — structured-JSON prompt, validates skills against `codec_skill_registry`, rejects unknown skills hard |
| 3 | **Vague-description clarifying loop (Q3)** — up to 3 rounds of `codec_ask_user.ask` clarifying questions before draft fails with `description_too_vague` |
| 4 | **Plan-hash tamper detection (Q13)** — `manifest.plan_hash = sha256(canonical plan.json)` computed at approval, verified by Step 9 daemon every tick |
| 5 | **Global allowlist tier (Q4)** — `~/.codec/agent_global_grants.json` with 4 grant kinds (network_domains / read_paths / write_paths / skills); items in global → marked `auto_approved` in per-agent grants |
| 6 | Plan revision flow — user edits inline, agent re-validates, flips `awaiting_approval → revised → awaiting_approval` |
| 7 | State machine — `draft_pending → awaiting_approval → approved/rejected/revised` (Step 9 extends with runtime states) |
| 8 | **Pre-approval re-validation** — checks skills still exist in registry between draft + approve (handles deleted skills) |
| 9 | **9 PWA endpoints** — `POST /api/agents` create+draft, `GET /api/agents` list, `GET /api/agents/{id}` detail, `POST /approve/reject/revise`, `GET/POST/DELETE /api/agent_global_grants` |
| 10 | 6 Step 8 audit events: `agent_plan_drafted/_approved/_rejected/_revised`, `agent_global_grant_added/_removed` |

### Step 9 — Background Execution + Permission Gate (12 features)

| # | Feature |
|:-:|---|
| 11 | **`codec-agent-runner` PM2 daemon** — 5s tick, lazy-imports, multi-agent thread pool |
| 12 | **`Action` dataclass** — skill / task / is_destructive / network_call / network_domain / touches_path / path / reads_path / read_path / kind, returned by Qwen next-action driver |
| 13 | **`permission_gate(action, agent_grants, global_grants)`** — UNION of grants enforced as skill / write_path / network_domain / read_path matrix; raises `PermissionViolation` on any gap |
| 14 | **Step 3 §1.7 strict-consent gate as universal floor** — destructive ops STILL hit consent (verb-match) even if pre-approved |
| 15 | **Per-checkpoint `_execute_checkpoint` loop** — Qwen → permission_gate → strict_consent (if destructive) → run_skill (Step 1+2 hooks fire) → append history → repeat until checkpoint_done OR step_budget cap |
| 16 | **Resume after PM2 restart (Q5)** — daemon scans `status=running` agents on boot, marks `crashed_resumed`, respawns from last atomic checkpoint save |
| 17 | **Multi-agent concurrency (Q6, Q8)** — `MAX_CONCURRENT=3` (env var `AGENT_RUNNER_MAX_CONCURRENT`); blocked agents occupy a slot |
| 18 | Per-agent thread inside daemon, atomic state writes after each operation (resume guarantee) |
| 19 | **`StepBudgetExhausted` → `paused` (review I2)** — agent paused with reason, user resumes via `POST /api/agents/{id}/extend_budget {additional_steps}` (overrides stored in `state.json`, plan stays immutable) |
| 20 | **4 PWA endpoints (Step 9)** — `POST /api/agents/{id}/abort`, `/pause`, `/resume`, `/grant` (kind+value) |
| 21 | 8 Step 9 audit events: `agent_started`, `_checkpoint_started`, `_completed`, `_paused`, `_resumed`, `_blocked_on_permission`, `_completed`, `_aborted` |
| 22 | `codec_dispatch.run_skill` chokepoint — every skill call wrapped in Step 2's `run_with_hooks` automatically; Phase 3 reuses the entire Step 1+2 substrate |

### Step 10 — Proactive Messaging + Auto-Escalation (10 features)

| # | Feature |
|:-:|---|
| 23 | **`AgentMessage` dataclass** — frozen vocab: `agent_update` / `agent_blocked` / `agent_question` / `agent_done` / `agent_aborted` / `user_reply` |
| 24 | **`post_message` dispatch** — writes to `~/.codec/agents/{id}/messages.jsonl` (append-only timeline) AND `~/.codec/notifications.json` (banner) |
| 25 | **60s batching window (Q10)** — multiple `agent_update` messages within window merge into ONE banner (count incremented, latest body wins); timeline preserves all entries 1:1 |
| 26 | **5 `_run_agent` lifecycle emit sites** — agent start, checkpoint completion, blocked-on-permission, destructive-rejected abort, final completion |
| 27 | **User reply pickup** — `POST /api/agents/{id}/messages` writes `type=user_reply`; daemon picks up between checkpoints, feeds into next Qwen call as additional context |
| 28 | **Per-agent silence kill-switch** — `POST /api/agents/{id}/silence`, persists at `~/.codec/agent_silence.json`; silenced = timeline written, notifications skipped (no badge spam) |
| 29 | **Auto-escalation classifier** — Qwen-3.6 driven 2-signal gate (verdict `is_project=True` AND `estimated_checkpoints ≥ 3`) on every chat-mode message |
| 30 | **Q11 session silence** — first "No" to "Promote to Project mode?" silences for that session (in-memory `_autoescalate_silence_set`, mutex-guarded), resets on new session |
| 31 | 3 PWA endpoints (Step 10) — `GET /api/agents/{id}/messages`, `POST /api/agents/{id}/messages`, `POST /api/agents/{id}/silence` |
| 32 | 3 Step 10 audit events: `agent_message_sent`, `_received`, `agent_auto_escalated_from_chat` |

---

## 13. Phase 3.5 — UX Polish + Proactive Overlay (24 features) *(v2.3)*

Closes Phase 3 with the user-facing affordances and review deferrals.
Project mode in chat composer, IDE-browseable project folders,
opt-in proactive nudges, dedicated `blocked_on_qwen` status with
auto-resume, symmetric read/write path enforcement, multi-channel
notification dispatch.

### Project mode UI (5 features)

| # | Feature |
|:-:|---|
| 1 | **Project mode chip in `codec_chat.html`** — alongside Chat / Think / Agents (no emoji, target-icon SVG) |
| 2 | **Inline project instructions panel** — examples (Marbella property bot, EUR/USD vol monitor, launch plan), shows above messages when Project selected |
| 3 | **`sendMessage()` Project branch** — POSTs to `/api/agents`, renders **Approve plan / Reject / View plan** buttons inline in chat thread |
| 4 | **`viewAgentPlan(id)`** — fetches `/api/agents/{id}` and renders the plan + permission manifest as an assistant message before approval |
| 5 | **Agent status pills above chat input** — color-coded (green running, orange paused/crashed, yellow awaiting_approval, red blocked, grey draft_pending), inline approve/pause/resume/abort actions, polls every 5s, silently hides on 401/403 |

### Project folder (Claude Code-style) (5 features)

| # | Feature |
|:-:|---|
| 6 | **`~/codec-projects/<slug>/` auto-creation** at agent spawn — human-browseable folder, openable in any IDE |
| 7 | **`_slugify(title)`** — lowercase + dash-separated, max 50 chars, unicode stripped, trailing dash trimmed; falls back to "project" if pure-punctuation |
| 8 | **Collision disambiguation** — `<slug>` → `<slug>-2` → `<slug>-3` → `<slug>-<agent_id>` after 99 collisions |
| 9 | **`manifest.project_dir`** field — full absolute path stored at creation; surfaced in `POST /api/agents` response and chat composer callout |
| 10 | **Plan-drafter prompt extension** — Qwen told the project_dir; defaults `permission_manifest.write_paths` to `<project_dir>/**` so files land where the user can open them. Override via env `CODEC_PROJECT_ROOT_DIR` or `~/.codec/config.json:agents.project_root_dir` |

### Proactive intelligence overlay (4 features) — opt-in only

| # | Feature |
|:-:|---|
| 11 | **`codec_proactive.py`** — observer-driven contextual nudges. OFF by default (`PROACTIVE_OVERLAY_ENABLED=false`). User opts in. |
| 12 | **`long_form_dwell` v1 pattern** — fires when active window is on Notion / Google Docs / Substack / Medium / NYTimes / FT / Economist / NewYorker for ≥30 min consecutively. Posts: "Want me to summarize?" with [Acknowledge / Dismiss today / Disable forever] buttons |
| 13 | **3-gate kill model** — global env switch + per-pattern killed-forever (`~/.codec/proactive_state.json:killed_patterns`) + per-day dismissed (resets next UTC midnight) |
| 14 | **Rate limits** — per-pattern cooldown 1 hour + global 30-min between any two suggestions (prevents pattern-cluster burst) |

### Step 9 review polish (5 features)

| # | Feature |
|:-:|---|
| 15 | **`blocked_on_qwen` dedicated status (review C2)** — distinct from `blocked_on_permission` (no permission to grant; service is just down) |
| 16 | **Daemon auto-resume on Qwen recovery** — when daemon ticks an agent in `blocked_on_qwen`, probes Qwen with a 1-token call; if alive → transitions to running and respawns. No user click needed |
| 17 | **`Action.reads_path` + `read_path` fields (review M4)** — symmetric read/write gating; permission_gate now checks `read_path` against `read_paths` UNION |
| 18 | **Symmetric `~` expansion** — `permission_gate` expands tilde on BOTH the action path AND the grant glob, so `~/Documents/foo` matches grant `~/Documents/**` |
| 19 | **`recovery_cid` threading** — daemon's crash-recovery `AGENT_RESUMED` emit shares correlation_id with the resumed `_run_agent`'s subsequent emit chain (review I4) |

### Multi-channel notifications (3 features)

| # | Feature |
|:-:|---|
| 20 | **`macos` channel** — `osascript display notification` banner. Works out of the box (no setup) |
| 21 | **`imessage` channel** — reuses `skills/imessage_send._send`; recipient read from `~/.codec/config.json:notifications.imessage_recipient`. Skipped silently if unconfigured |
| 22 | **`telegram` channel** — direct Bot API call (no daemon coupling). Reads `notifications.telegram_token` + `:telegram_chat_id`. Skipped silently if unconfigured |

### Phase 3 review fast-follow (2 features)

| # | Feature |
|:-:|---|
| 23 | **`POST /api/agents/{id}/extend_budget`** — bumps current checkpoint's step_budget via `state.json:step_budget_overrides[checkpoint_id]` (plan stays immutable, plan_hash check intact); transitions paused → running |
| 24 | **3 new Phase 3.5 audit events** — `proactive_suggestion_emitted`, `_acknowledged`, `_dismissed`. `PHASE35_PROACTIVE_EVENTS` frozenset exposed |

---

## 14. CODEC Pilot — Browser Automation You Can Teach (32 features) *(v2.3)*

The 8th product — a complete browser-automation pillar with a dedicated headless Chromium, ReAct-style agent loop driven by Qwen, deterministic record-replay with selector fallback, a skill approval gate, and human-in-the-loop takeover. Lives in `~/codec/pilot/` (11 modules), runs as `pilot-runner` on PM2 port 8094.

### Browser substrate (5 features)

| # | Feature |
|:-:|---|
| 1 | **Dedicated headless Chromium** on CDP port **9223** (separate from user's daily Chrome on 9222) — never interferes with user's browsing |
| 2 | **Persistent profile** at `~/.codec/pilot_chrome_profile/` — cookies, sessions, login state survive restarts |
| 3 | **Playwright control wrapper** (`pilot/pilot_chrome.py`) — async lifecycle, navigation, XPath click/type primitives, screenshot, snapshot escape hatch |
| 4 | **`--disable-blink-features=AutomationControlled`** — basic anti-fingerprint, hides `navigator.webdriver` |
| 5 | **`pilot_session()` async context manager** — RAII-style start/stop for tests and one-shot replays |

### Indexed-DOM snapshot (3 features)

| # | Feature |
|:-:|---|
| 6 | **Single-pass JS extractor** walks the page in one `evaluate()` call — typically `<500ms` even on heavy pages |
| 7 | **ARIA-role allowlist** — only interactive elements indexed (`button`, `link`, `textbox`, `combobox`, `checkbox`, `radio`, `tab`, `option`, …) capped at 150 per snapshot |
| 8 | **Per-element selectors captured** — `[N]` index + XPath + CSS selector + accessible name + ARIA role + bounding box, sorted top-to-bottom left-to-right |

### Agent ReAct loop (5 features)

| # | Feature |
|:-:|---|
| 9 | **Qwen-driven agent** (`pilot/pilot_agent.py`) on local LLM (port 8083) — temperature 0.0, 256 max tokens |
| 10 | **8-action vocabulary** — `navigate`, `click`, `type`, `scroll`, `wait`, `extract`, `select_option`, `done` (+ `error` for surrender) |
| 11 | **Step budget** (default 40, configurable per run) — prevents runaway loops |
| 12 | **Hallucinated-index validation** — every `click`/`type` re-resolves against fresh snapshot, refuses indices that don't exist |
| 13 | **`StubLLM` offline fallback** — keeps the loop testable without Qwen |

### Manual record / teach mode (4 features)

| # | Feature |
|:-:|---|
| 14 | **`POST /record/start`** — opens an empty `AgentRun`, marks runner as recording. One concurrent session enforced (returns 409 on double-start) |
| 15 | **Recording hook** on `/navigate`, `/click/{idx}`, `/type/{idx}` — every action lands in the active trace with full selector capture |
| 16 | **`GET /record/status`** — survives page reloads; dashboard reattaches to in-flight recording |
| 17 | **`POST /record/stop`** — saves trace JSON, auto-compiles to a pending skill, returns the file path |

### Trace + compiler (4 features)

| # | Feature |
|:-:|---|
| 18 | **Per-run JSON trace** at `~/.codec/pilot_traces/{run_id}/trace.json` — steps with action, selectors, snapshot text, error, timing |
| 19 | **`compile_skill()` template** — emits a `Replayer`-based Python module with `SKILL_NAME`, `SKILL_DESCRIPTION`, `SKILL_TAGS`, and an async `run()` |
| 20 | **`compile_to_pending()` one-shot** — used by `/record/stop` and `/run/{id}/compile`; appends numeric suffix on name collision |
| 21 | **`from_dict` round-trip** — traces reload with all selectors intact, ready for replay |

### Replay engine — 3-tier reliability ladder (5 features)

| # | Feature |
|:-:|---|
| 22 | **Tier 1: XPath** — 3 attempts × 500ms backoff, 1.5s per-attempt timeout; typical step under 100 ms when DOM is stable |
| 23 | **Tier 2: CSS selector** — 1 attempt × 2s timeout; catches XPath drift on dynamic classnames |
| 24 | **Tier 3: LLM rescue** — re-snapshot, ask Qwen to find the original element by stored name + role, execute against new XPath. 10s timeout, 1 attempt |
| 25 | **`allow_llm_rescue=False`** mode — fully offline replay for Scheduler / cron contexts |
| 26 | **`ReplayResult.to_dict()`** — status, methods used per step, `rescues_used`, durations — full audit trail per replay |

### Skill approval gate (3 features)

| # | Feature |
|:-:|---|
| 27 | **`~/.codec/skills/.pending/`** directory — compiled skills do NOT auto-register; landing zone for human review. Blocks prompt-injection-spawned auto-registration |
| 28 | **Dashboard preview** — `GET /skills/pending/{slug}` returns full Python source; one-click ✓ Approve moves to `~/.codec/skills/`, ✕ Reject deletes |
| 29 | **`slugify()` + collision suffix** — filesystem-safe names from free-form task descriptions, `_2`/`_3`/… suffix appended on duplicate slugs |

### HITL (human-in-the-loop) takeover (3 features)

| # | Feature |
|:-:|---|
| 30 | **`HitlController.pause/resume/inject`** — agent loop checks an `asyncio.Event` every step; human can pause, push manual actions to a queue, resume |
| 31 | **`takeover()/handback()`** — full human control mid-run; agent re-snapshots and continues from wherever it was left |
| 32 | **HITL HTTP endpoints** — `/hitl/{run_id}/pause|resume|inject|takeover|handback|status` mirror the in-process API for the dashboard |

### Live view + infrastructure (3 features bundled)

- **MJPEG live stream** at `/screenshot/stream` — ~3 fps multipart feed, ~350 KB/s; falls back to 2-second polling on disconnect
- **30 HTTP endpoints** on `pilot-runner` (FastAPI, port 8094) with CORS enabled for `codec.lucyvpa.com` cross-origin calls
- **Cloudflare tunnel** at `pilot.lucyvpa.com` for off-LAN dashboard access; PM2 service `pilot-runner` with autorestart + isolated log files

---

## Summary

| Product / Phase | Features |
|---|:-:|
| 1. CODEC Core | 26 |
| 2. CODEC Chat | 30 |
| 3. CODEC Dashboard | 32 |
| 4. CODEC Vibe | 20 |
| 5. CODEC Agents | 20 |
| 6. CODEC Skills | 79 |
| 7. CODEC Infrastructure | 36 |
| 8. CODEC Dictate | 15 |
| 9. CODEC Instant | 12 |
| 10. Phase 1 — Agent Substrate *(v2.3)* | 18 |
| 11. Phase 2 — Continuous Observation + Automation *(v2.3)* | 24 |
| 12. Phase 3 — Drop-a-Project Autonomous Agents *(v2.3)* | 32 |
| 13. Phase 3.5 — UX Polish + Proactive Overlay *(v2.3)* | 24 |
| 14. CODEC Pilot — Browser Automation You Can Teach *(v2.3)* | 32 |
| **TOTAL** | **400** |

**400 features · 88 skills · 2000+ tests · 52K+ lines of production code · 9 products**

### What's new in v3.2

**The self-updating release.** CODEC now updates itself — and a long-standing bundle-version-drift bug in the build pipeline is fixed at the source.

- **In-app auto-update (Sparkle-compatible)** — pure-Python client (`codec_update.py`) reads a signed Sparkle appcast and verifies every download's **Ed25519 signature** against the embedded `SUPublicEDKey` before installing. Tampered builds are refused. Dashboard endpoints `GET /api/update/check` + `POST /api/update/download`; in-app update banner ("Download & open") polls on load and every 6h.
- **GitHub-hosted update feed** — each release (signed `.dmg` + appcast) lives at `AVADSA25/codec-updates`; the app polls a permanent `releases/latest/download/appcast.xml` URL that always resolves to the newest version. Host is a one-line switch (`sparkle_feed_url` / `CODEC_APPCAST_PREFIX`) for a future move to a custom domain or Cloudflare R2.
- **Bundle version stamped from `VERSION` at build time** — `build_app.sh` injects the F-5 single source of truth into `CFBundleShortVersionString` so the bundle version can never drift from the source again. Guarded by a regression test.
- **Gatekeeper signing fix** — bundled Python now lives under `Contents/Resources/python` instead of `Frameworks/` (a bare Python tree under `Frameworks/` was treated as a nested bundle and broke the code-signing seal). The DMG is notarized + stapled so it opens cleanly on a never-online Mac.
- **Unified `:8083` model port** — all Qwen MLX endpoints reconciled (was split `:8081` / `:8082`); UI-TARS vision served from the same unified server.

### What's new in v3.1

**The 9th product + adversarial-hardening release.** CODEC Project promoted to a live product, Pilot security-hardened end-to-end, calmer 2026 palette across the dashboard.

- **9 products. One system.** Cortex neural map expanded from 28 → 38 nodes (+31 edges); CODEC Pilot and CODEC Project added as live product cards.
- **Pilot security-hardening wave (PP-1…PP-12)** — full adversarial-audit remediation: AST safety gate at skill-approval time (refuses to activate dangerous compiled skills), untrusted-input fencing on the LLM selector-rescue prompt (page text can't redirect element selection), irreversible-click blocking on replay unless `PILOT_ALLOW_DESTRUCTIVE=1`, path/glob-traversal neutralization in `slugify()` lookups, forensic audit trail on every skill write/approve/reject/block. The `pilot.lucyvpa.com` Cloudflare tunnel was removed after the RCE finding — Pilot is local-only.
- **Conversational continuity in Project mode** — a Project-mode chat thread now binds to the agent it drafts; follow-ups route to the running agent instead of spawning duplicates. Pulsing *"Talking to …"* chip with one-click exit.
- **Voice in / voice out** — Kokoro TTS now actually speaks assistant replies when *"Voice Replies"* is on (the toggle was previously inert); per-message Speak button; Pilot tab gets its own 🎤 dictate + 🔊 speak controls.
- **Live preview panel** — slide-out shows the most-recently-modified files in a running agent's project folder (5s poll), so you can watch output appear without leaving the chat.
- **Auto-grant of user-typed paths** — paths the user typed themselves (`~/…`, `$HOME/…`, `/Users/<n>/…`) no longer trigger mid-run `blocked_on_permission`; sensitive paths (`~/.ssh`, `~/.aws`, `/etc`, …) stay blocklisted.
- **2026 palette refresh** — calmer brand accent (`#d97757` dark / `#b85a3a` light) and a muted Tailwind-500 node palette across the Cortex map. Same identity, far less "Windows-XP-bright."
- **F-wave engineering hygiene** — ruff baseline + CI lint gate (F-4), `VERSION` single source of truth + CHANGELOG-driven tag helper (F-5), `pyproject.toml` packaging metadata (F-15), pricing docs (F-11), GitHub Discussions (F-12), `lucy`→`delegate` skill rename (F-6).

### What's new in v2.3 — Phase 1 + 2 + 3 + 3.5

**The drop-a-project release.** CODEC becomes a "real AI employee" at the substrate level — drop a project description, agent plans + builds + sends updates back proactively, with permission gates and resume-after-restart guarantees.

- **Phase 1 — Agent substrate** (18 features). Unified audit envelope (`schema:1` + paired `correlation_id` per Step 1 §1.4 contract), 5 plugin lifecycle hooks (`pre_tool` / `post_tool` / `on_error` / `on_operation_*`), `AskUserQuestion` blocking pause with §1.7 strict-consent gate, stuck-loop detection ring buffer, per-turn step budget, `self_improve` migrated to plugin architecture.
- **Phase 2 — Continuous observation + automation** (24 features). New `codec-observer` PM2 daemon with 10-min RAM ring buffer, observation-injection contract for chat/voice/MCP, declarative `SKILL_OBSERVATION_TRIGGER` (5 matcher types), end-of-day `shift_report` with 5-section markdown.
- **Phase 3 — Drop-a-project autonomy** (32 features). New `codec-agent-runner` PM2 daemon. Plan + Permission Contract (Step 8) → Background Execution + Permission Gate (Step 9) → Proactive Messaging (Step 10). Plan-hash tamper detection, multi-agent concurrency cap=3, resume from last atomic checkpoint after PM2 restart. 17 new `agent_*` audit events, 17 new PWA endpoints under `/api/agents/`.
- **Phase 3.5 — UX polish** (24 features). Project mode chip in `codec_chat.html` (Chat / Think / Agents / Project), `~/codec-projects/<slug>/` auto-creation (Claude Code-style human-browseable folder), inline plan-review buttons, agent status pills polling `/api/agents` every 5s, opt-in proactive intelligence overlay (`long_form_dwell` pattern), `blocked_on_qwen` dedicated status with daemon auto-resume on Qwen probe, symmetric read/write path enforcement, multi-channel notification dispatch (macOS / iMessage / Telegram).

### What's new in v2.2

- **Live MCP bridge** — CODEC exposed as MCP server to Claude Desktop / Claude Code / Cursor. All skills callable from any MCP-compatible client.
- **Memory Layer upgrade** — three-tier memory with identity boot payload, temporal fact tracking, and CCF rule-based compression.
- **MCP audit fixes** — skill-name sanitization bug fixed (AI_News_Digest loads), pomodoro stop/status, reminders/notes read path, file_search word-boundary parsing, network_info multi-interface detection.
- **F5 live dictation** — hands-free typing mode with pipelined audio capture and focus-preserving paste.

---

<p align="center">
  Built by <a href="https://avadigital.ai">AVA Digital LLC</a> · MIT License
</p>
