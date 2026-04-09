<p align="center">
  <img src="https://i.imgur.com/RbrQ7Bt.png" alt="CODEC" width="280"/>
</p>

<h1 align="center">CODEC v2.0</h1>
<p align="center"><strong>Open-Source Intelligent Command Layer for macOS</strong></p>
<p align="center"><em>Your voice. Your computer. Your rules. No limit.</em></p>
<p align="center">
  <a href="https://opencodec.org">opencodec.org</a> · <a href="https://avadigital.ai">AVA Digital LLC</a> · <a href="#quick-start">Get Started</a> · <a href="#support-the-project">Support</a> · <a href="#professional-setup">Enterprise</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/features-234-blue?style=flat-square" alt="234 Features"/>
  <img src="https://img.shields.io/badge/skills-60-orange?style=flat-square" alt="60 Skills"/>
  <img src="https://img.shields.io/badge/tests-405-green?style=flat-square" alt="405 Tests"/>
  <img src="https://img.shields.io/badge/lines-61.7K-purple?style=flat-square" alt="61,700 Lines"/>
  <img src="https://img.shields.io/badge/license-MIT-brightgreen?style=flat-square" alt="MIT License"/>
</p>

---

<p align="center">
  <a href="https://www.youtube.com/watch?v=OEXxvxA0_AE">
    <img src="https://img.youtube.com/vi/OEXxvxA0_AE/maxresdefault.jpg" alt="CODEC Demo" width="660"/>
  </a>
  <br/>
  <em>Watch the full demo</em>
</p>

---

## What Is CODEC

CODEC is a framework that turns a Mac into a voice-controlled AI workstation. Give it a brain (any LLM — local or cloud), ears (Whisper), a voice (Kokoro), and eyes (vision model). The rest is Python.

It listens, sees the screen, speaks back, controls apps, writes code, drafts messages, manages Google Workspace, and when it doesn't know how to do something — it writes its own plugin and learns.

No cloud dependency. No subscription. No data leaving the machine. MIT licensed.

---

## 7 Products. One System.

| # | Product | What It Does |
|:-:|---|---|
| 1 | **CODEC Core** | Voice command layer + vision mouse control — 60 skills, screen clicks by voice |
| 2 | **CODEC Dictate** | Hold, speak, paste — live typing at cursor, draft refinement, floating overlays |
| 3 | **CODEC Instant** | Right-click → 8 AI services system-wide — proofread, translate, reply, explain |
| 4 | **CODEC Chat** | 250K-context conversational AI + 12 autonomous agent crews |
| 5 | **CODEC Vibe** | Browser IDE with Monaco editor + Skill Forge — the framework writes its own plugins |
| 6 | **CODEC Voice** | Real-time voice calls with interrupt detection, screen analysis mid-call |
| 7 | **CODEC Overview** | Dashboard + Cortex nerve center + full audit trail — accessible from any device |

---

### 1. CODEC Core — The Command Layer

Always-on voice assistant. Say *"Hey CODEC"* or press F13 to activate. F18 for voice commands. F16 for text input.

60 skills fire instantly: Google Calendar, Gmail, Drive, Docs, Sheets, Tasks, Keep, Chrome automation, web search, Hue lights, timers, Spotify, clipboard, terminal commands, PM2 control, and more. Most skills bypass the LLM entirely — direct action, zero latency. Skills are matched by trigger specificity — longer, more specific triggers always win over generic ones.

**Vision Mouse Control — See & Click**

No other open-source voice assistant does this. Say *"Hey CODEC, click the Submit button"* — CODEC screenshots the screen, sends it to a local UI-specialist vision model (UI-TARS), gets back pixel coordinates, and moves the mouse to click. Fully voice-controlled. Works on any app. No accessibility API required — pure vision.

| Step | What happens | Speed |
|---|---|---|
| 1 | Whisper transcribes voice command | ~2s |
| 2 | Target extracted from natural speech | instant |
| 3 | Screenshot captured and downscaled | instant |
| 4 | UI-TARS locates the element by pixel coordinates | ~4s |
| 5 | pyautogui moves cursor and clicks | instant |

*"I'm on Cloudflare and can't find the SSL button — click it for me."* That works. CODEC strips the conversational noise, extracts "SSL button", and finds it on screen.

### 2. CODEC Dictate — Hold, Speak, Paste

Hold Cmd+R. Say what you mean. Release. Text appears wherever the cursor is. Press **L** for live typing mode — words appear at the cursor in real-time as you speak.

If CODEC detects a message draft, it refines through the LLM — grammar fixed, tone polished, meaning preserved. Works in every app on macOS. A free, open-source SuperWhisper replacement that runs entirely local.

Native floating overlays: orange-bordered recording panel with pulsing red dot, blue processing indicator, green live-typing display with real-time transcript. Built-in hallucination filter blocks Whisper noise artifacts. atexit + SIGTERM cleanup prevents orphaned subprocesses.

### 3. CODEC Instant — One Right-Click

Select any text, anywhere. Right-click. Eight AI services system-wide: Proofread, Elevate, Explain, Translate, Reply (with `:tone` syntax), Prompt, Read Aloud, Save. Powered by the local LLM.

### 4. CODEC Chat — 250K Context + 12 Agent Crews

Full conversational AI. Long context. File uploads (drag-and-drop). Image analysis via vision model. Web search. Persistent conversation history with sidebar. Edit and re-send messages. Regenerate responses.

Voice input via continuous microphone with stop button. Streaming responses with typing and thinking indicators.

Plus 12 autonomous agent crews — not single prompts, full multi-step workflows. Say *"research the latest AI agent frameworks and write a report."* Minutes later there's a formatted Google Doc in Drive with sources, images, and recommendations.

| Crew | Output |
|---|---|
| Deep Research | 10,000-word illustrated report → Google Docs |
| Daily Briefing | Morning news + calendar → Google Docs |
| Competitor Analysis | SWOT + positioning → Google Docs |
| Trip Planner | Full itinerary → Google Docs |
| Email Handler | Triage inbox, draft replies |
| Social Media | Posts for Twitter, LinkedIn, Instagram |
| Code Review | Bugs + security + clean code |
| Data Analysis | Trends + insights report |
| Content Writer | Blog posts, articles, copy |
| Meeting Summarizer | Action items from transcripts |
| Invoice Generator | Professional invoices |
| Custom Agent | Define your own role, tools, task |

Schedule any crew: *"Run competitor analysis every Monday at 9am"*

The multi-agent framework is under 800 lines. Zero dependencies. No CrewAI. No LangChain. 7 built-in tools including web search, file operations, Google Docs, image generation, and vision.

### 5. CODEC Vibe — AI Coding IDE + Skill Forge

Split-screen in the browser. Monaco editor on the left (same engine as VS Code, v0.45.0). AI chat on the right. Describe what's needed — CODEC writes it, click Apply, run it, live preview in browser.

Skill Forge takes it further: three modes — paste code, import from GitHub URL, or describe a capability in plain English. CODEC converts it into a working plugin. The framework writes its own extensions. DOMPurify sanitization on all rendered content.

### 6. CODEC Voice — Live Voice Calls

Real-time voice-to-voice conversations with the AI. WebSocket pipeline with auto-reconnect (exponential backoff), heartbeat keepalive, and interrupt detection — no Pipecat, no external dependencies.

Call CODEC from a phone, talk naturally, and mid-call say *"check my screen"* — it takes a screenshot, analyzes it, and speaks the result back. Interrupt-safe: if you speak while vision is processing, it stops immediately instead of playing stale results. Voice Replies toggle (ON/OFF) persists across all pages. TTS dedup guard prevents duplicate audio playback.

Full transcript saved to memory. Every conversation becomes searchable context for future sessions. VAD thresholds (silence, duration, echo cooldown) fully configurable via `config.json`.

### 7. CODEC Overview — Dashboard, Cortex & Audit

Private dashboard accessible from any device, anywhere. Cloudflare Tunnel or Tailscale VPN — no port forwarding, no third-party relay. 75+ API endpoints. Send commands, view the screen, launch voice calls, manage agents — all from a browser. Installable as a PWA on mobile and desktop.

**Cortex — System Nerve Center**
Visual command center showing all 7 CODEC products in an interactive grid. Neural network SVG map, real-time activity feed, searchable skills panel, and detailed event log viewer. The single-pane-of-glass view of the entire system.

**Audit — Full Event Trail**
Every action CODEC takes is logged across 16 categories: command, skill, llm, auth, error, scheduled, voice, vision, tts, stt, system, security, hotkey, screenshot, config, draft. Filterable by category pills, searchable, with colored timeline dots and expandable event details. JSON-line storage with 50MB rotation. Default 24h time range with 1h/6h/24h/7d quick filters.

---

## Screenshots

<p align="center">
  <img src="docs/screenshots/quick-chat.png" alt="Quick Chat" width="720"/><br/>
  <em>Chat — ask anything, drag & drop files, full conversation history</em>
</p>

<p align="center">
  <img src="docs/screenshots/chat-analysis.png" alt="Chat with File Analysis" width="720"/><br/>
  <em>Deep Chat — upload files, select agents, get structured analysis</em>
</p>

<p align="center">
  <img src="docs/screenshots/voice-call.png" alt="Voice Call" width="720"/><br/>
  <em>Voice Call — real-time conversation with live transcript</em>
</p>

<p align="center">
  <img src="docs/screenshots/vibe-code.png" alt="Vibe Code" width="720"/><br/>
  <em>Vibe Code — describe what you want, get working code with live preview</em>
</p>

<p align="center">
  <img src="docs/screenshots/deep-research.png" alt="Deep Research Report" width="720"/><br/>
  <em>Deep Research — multi-agent reports delivered to Google Docs</em>
</p>

<p align="center">
  <img src="docs/screenshots/tasks.png" alt="Tasks & Schedules" width="720"/><br/>
  <em>Scheduled automations — morning briefings, competitor analysis, on cron</em>
</p>

<details>
<summary><strong>More screenshots</strong></summary>
<br/>
<p align="center">
  <img src="docs/screenshots/settings.png" alt="Settings" width="720"/><br/>
  <em>Settings — LLM, TTS, STT, hotkeys, wake word configuration</em>
</p>
<p align="center">
  <img src="docs/screenshots/agent-options.png" alt="Agent Options" width="420"/><br/>
  <em>12 specialized agent crews</em>
</p>
<p align="center">
  <img src="docs/screenshots/login-auth.png" alt="Authentication" width="320"/><br/>
  <em>Touch ID + PIN + 2FA authentication</em>
</p>
<p align="center">
  <img src="docs/screenshots/right-click-menu.png" alt="Right-Click Menu" width="300"/><br/>
  <em>Right-click integration — CODEC in every app</em>
</p>
<p align="center">
  <img src="docs/screenshots/terminal.png" alt="Terminal" width="400"/><br/>
  <em>60 skills loaded at startup</em>
</p>
</details>

---

## What Makes CODEC Different

| Capability | CODEC | Siri / Alexa / Google | ChatGPT / Claude |
|---|---|---|---|
| Controls the computer | Full macOS control | Limited smart home | No |
| Reads the screen | Vision model | No | No |
| Clicks UI elements by voice | Vision + mouse control | No | No (Cloud Computer Use only) |
| Runs 100% local | Yes — all models on device | No | No |
| Voice-to-voice calls | WebSocket, real-time | Yes but cloud | Yes but cloud |
| Multi-agent workflows | 12 crews, local LLM | No | Limited |
| Right-click AI services | 8 system-wide services | No | No |
| Writes its own plugins | Skill Forge | No | No |
| Live typing at cursor | Dictate L key | No | No |
| Process watchdog | Auto-kills stuck processes | No | No |
| Full audit trail | 16 event categories | No | No |
| Open source | MIT | No | No |

**What CODEC replaced with native code:**

| Before | After (CODEC Product) |
|---|---|
| Pipecat | **Voice** — own WebSocket pipeline |
| CrewAI + LangChain | **Chat** — 795-line agent framework, zero dependencies |
| SuperWhisper | **Dictate** — free, open source, live typing |
| Cursor / Windsurf | **Vibe** — Monaco + AI + Skill Forge |
| Google Assistant / Siri | **Core** — actually controls the computer |
| Grammarly | **Instant** — right-click services via local LLM |
| ChatGPT | **Chat** — 250K context, fully local |
| Cloud LLM APIs | Local stack (Qwen + Whisper + Kokoro + Vision) |
| Vector databases | FTS5 SQLite (simpler, faster, private) |
| Datadog / Sentry | **Overview** — dashboard + cortex + 16-category audit |

**External services:** DuckDuckGo for web search. Cloudflare free tier for the tunnel (or Tailscale). Everything else runs on local hardware.

---

## Quick Start

```bash
git clone https://github.com/AVADSA25/codec.git
cd codec
./install.sh
```

The setup wizard handles everything in 9 steps: LLM, voice, vision, hotkeys, Google OAuth, remote access, and more.

**Requirements:**
- macOS Ventura or later
- Python 3.10+
- An LLM (Ollama, LM Studio, MLX, or any OpenAI-compatible API)
- Whisper for voice input, Kokoro for voice output, a vision model for screen reading

---

## Supported LLMs

| Model | How to run |
|---|---|
| **Qwen 3.5 35B** (recommended) | `mlx-lm.server --model mlx-community/Qwen3.5-35B-A3B-4bit` |
| **Llama 3.3 70B** | `mlx-lm.server --model mlx-community/Llama-3.3-70B-Instruct-4bit` |
| **Mistral 24B** | `mlx-lm.server --model mlx-community/Mistral-Small-3.1-24B-Instruct-2503-4bit` |
| **Gemma 3 27B** | `mlx-lm.server --model mlx-community/gemma-3-27b-it-4bit` |
| **GPT-4o** (cloud) | `"llm_url": "https://api.openai.com/v1"` |
| **Claude** (cloud) | OpenAI-compatible proxy |
| **Ollama** (any model) | `"llm_url": "http://localhost:11434/v1"` |

Configure in `~/.codec/config.json`:
```json
{
  "llm_url": "http://localhost:8081/v1",
  "model": "mlx-community/Qwen3.5-35B-A3B-4bit"
}
```

---

## Keyboard Shortcuts

| Key | Action |
|---|---|
| F13 | Toggle CODEC ON/OFF |
| F18 (hold) | Record voice → release to send |
| F18 (double-tap) | PTT Lock — hands-free recording |
| F16 | Text input dialog |
| Cmd+R (hold) | Dictate — hold, speak, release to paste |
| L (during dictate) | Live typing — words appear at cursor in real-time |
| `* *` | Screenshot + AI analysis |
| `+ +` | Document mode |
| Camera icon | Live webcam PIP — drag around, snapshot anytime |
| Select text → right-click | 8 AI services in context menu |

**Laptop (F1-F12):** F5 = toggle, F8 = voice, F9 = text input. Run `python3 setup_codec.py` → select "Laptop / Compact" in Step 4.

Custom shortcuts in `~/.codec/config.json`. Restart after changes: `pm2 restart open-codec`

---

## Privacy & Security

**6-layer security stack:**

| Layer | Protection |
|---|---|
| Network | Cloudflare Zero Trust tunnel or Tailscale VPN, CORS restricted origins with explicit header whitelist |
| Auth | Touch ID + PIN + TOTP 2FA, timing-safe token comparison, brute-force rate limiting |
| Encryption | AES-256-GCM + ECDH P-256 key exchange, per-session keys, key persistence across restarts |
| Execution | Subprocess isolation, resource limits (512MB RAM, 120s CPU), 46+ dangerous command patterns, human review gate |
| Data | Local SQLite with WAL, parameterized queries, FTS5 full-text search with injection prevention — searchable, private, yours |
| Audit | Full event trail across 16 categories, 50MB rotating JSON-line logs, every action tracked |

Every conversation is stored locally in SQLite with FTS5 full-text search. No cloud sync. No analytics. No telemetry.

---

## MCP Server — CODEC Inside Claude, Cursor, VS Code

CODEC exposes tools as an MCP server. Any MCP-compatible client can invoke CODEC skills directly:

```json
{
  "mcpServers": {
    "codec": {
      "command": "python3",
      "args": ["/path/to/codec-repo/codec_mcp.py"]
    }
  }
}
```

Then in Claude Desktop: *"Use CODEC to check my calendar for tomorrow."*

Skills opt-in to MCP exposure with `SKILL_MCP_EXPOSE = True`. Input validation enforces 5KB task / 10KB context limits with type checking on every call.

---

## Debugging & Development

**Recommended tools:**

| Tool | How it helps |
|---|---|
| **[Claude Code](https://claude.ai/claude-code)** | Terminal AI — reads the full codebase, runs commands, fixes errors in context |
| **[Cursor](https://cursor.com)** | AI IDE — navigate CODEC's 60+ files, refactor, debug with full project awareness |
| **[Windsurf](https://windsurf.ai)** | AI IDE — strong at multi-file reasoning |
| **[Antigravity](https://antigravity.dev)** | AI debugging assistant — paste errors, get fixes with codebase context |

**Quick debug commands:**

```bash
# Check all services
pm2 list

# Check specific service logs
pm2 logs open-codec --lines 30 --nostream        # Main CODEC process
pm2 logs codec-dashboard --lines 30 --nostream    # Dashboard API
pm2 logs codec-dictate --lines 10 --nostream      # Dictation hotkeys
pm2 logs codec-watchdog --lines 10 --nostream     # Process watchdog
pm2 logs whisper-stt --lines 10 --nostream        # Speech-to-text
pm2 logs kokoro-82m --lines 10 --nostream         # Text-to-speech

# Verify LLM is responding
curl -s http://localhost:8081/v1/models | python3 -m json.tool

# Verify dashboard is up
curl -s http://localhost:8090/health

# Restart everything
pm2 restart all

# Full health check
python3 -c "from codec_config import *; print('Config OK')"
```

**Common issues:**

<details>
<summary><strong>Keys don't work</strong></summary>

- macOS stealing F-keys? System Settings → Keyboard → "Use F1, F2, etc. as standard function keys"
- After config change: `pm2 restart open-codec`
</details>

<details>
<summary><strong>Wake word doesn't trigger</strong></summary>

- **Check logs first**: `pm2 logs open-codec --lines 30 --nostream | grep -i wake`
- **Energy too low?** Look for `Wake mic: energy=XX (threshold=YY)`. If energy < threshold, speak louder or lower `wake_energy` in `~/.codec/config.json` (default: 130)
- **Whisper mishearing?** Look for `Wake heard: 'xxx'` — Whisper often transcribes "Hey CODEC" as "and codec", "and kodak", "hey codex". All common variants are matched automatically via keyword detection (any text containing "codec", "codex", "kodak", etc. triggers)
- **Whisper hallucinating?** Long repetitive transcriptions (100+ chars of gibberish) are filtered automatically
- **Mic not found?** Listener defaults to "default" CoreAudio device, but prefers Anker webcam mic if found. Check: `python3 -c "import sounddevice as sd; [print(f'{i}: {d[\"name\"]}') for i,d in enumerate(sd.query_devices()) if d['max_input_channels']>0]"`
- **Mic permission?** Python must be in System Settings → Privacy → Microphone. Run `python3 request_mic.py` in iTerm to request access
- **sox not found?** PM2 doesn't inherit shell PATH. CODEC auto-adds `/opt/homebrew/bin` to PATH and resolves sox via `shutil.which()`
- **state["active"] blocking?** Wake word runs independently of F13 toggle — no need to press F13 first. Wake word auto-activates CODEC when triggered
- **Bluetooth headphones?** A2DP mode records silence from CLI. Use wired mic or webcam mic
</details>

<details>
<summary><strong>No voice output / Voice call issues</strong></summary>

- Check Kokoro TTS: `curl http://localhost:8085/v1/models`
- Fallback: `"tts_engine": "say"` in config.json (macOS built-in)
- Disable: `"tts_engine": "none"`
- **Voice toggle:** Check the burger menu on any page — Voice Replies ON/OFF persists via localStorage across all pages
- **Double TTS playback?** A dedup guard prevents the same text playing twice. If you hear duplicates, restart: `pm2 restart codec-dashboard`
- **Qwen 3.5 reasoning/content split**: MLX server puts thinking in `reasoning` field, actual answer in `content`. With low `max_tokens`, model burns all tokens on thinking → empty `content`. Fix: set `max_tokens: 2000+` and only read `content` field, filter `<think>` tags
- **Voice screenshot silent after "analyzing"**: If mic noise sets `self.interrupted` flag during long operations (screenshot/vision), it kills subsequent responses. Fix: clear `self.interrupted` before speaking response
- **RGBA→JPEG crash**: macOS screenshots are PNG with alpha (RGBA). Must `img.convert("RGB")` before saving as JPEG
</details>

<details>
<summary><strong>Dictate not working</strong></summary>

- **Check logs:** `pm2 logs codec-dictate --lines 20 --nostream`
- **Cmd+R not recording?** Ensure codec-dictate is running: `pm2 status codec-dictate`. If errored, restart: `pm2 restart codec-dictate`
- **Text not pasting?** CODEC uses `pyautogui.hotkey('command', 'v')` for reliable cross-app paste. If using osascript keystroke, it can drop the Cmd modifier — this was fixed in v2.0
- **Live typing (L key) not working?** L only activates during an active Cmd+R session or when pressed standalone. Check that pyperclip and pyautogui are installed: `pip3 install pyperclip pyautogui`
- **Overlay not showing?** Requires tkinter. On Python 3.13: `brew install python-tk@3.13`. The overlay is a separate subprocess — if it crashes, dictation still works (just no visual feedback)
- **Sox "no default input device"?** Check: `sox -d -r 16000 -c 1 -t wav test.wav trim 0 2` — if this fails, set your mic in System Settings → Sound → Input
</details>

<details>
<summary><strong>Draft/paste not working (IC-1)</strong></summary>

- **Path mismatch**: `DRAFT_TASK_FILE` in codec.py must match `TASK_FILE` in codec_watcher.py. Both should be `~/.codec/draft_task.json`
- Run smoke test: `python3 tests/test_smoke.py` — checks path alignment automatically
- Check watcher: `pm2 logs codec-dashboard --lines 10 --nostream | grep -i draft`
</details>

<details>
<summary><strong>Screenshot crashes (IC-3)</strong></summary>

- **NameError: `log` not defined**: codec.py uses `print()` not `log.info()`. If you see `log.xxx()` calls, replace with `print(f"[CODEC] ...")`
- Run smoke test: `python3 tests/test_smoke.py` — checks for undefined references
</details>

<details>
<summary><strong>Dashboard not loading</strong></summary>

- Check: `curl http://localhost:8090/health`
- Restart: `pm2 restart codec-dashboard`
- Remote via Cloudflare: `pm2 logs cloudflared --lines 3 --nostream`
- Remote via Tailscale: access CODEC at `http://100.x.x.x:8090`
</details>

<details>
<summary><strong>Agents timing out</strong></summary>

- First run takes 2-5 min — multi-step research with multiple searches
- Check: `pm2 logs codec-dashboard --lines 30 --nostream | grep -i agent`
- Agents run as background jobs — no Cloudflare timeout
</details>

<details>
<summary><strong>Flash Chat empty or not loading</strong></summary>

- **Empty chat?** Check auth — if not authenticated, the API returns `{"error":"Not authenticated"}` instead of an array, which silently fails. Log in first.
- **Messages in wrong order?** Flash Chat shows newest at bottom (reversed). If you see newest on top, restart: `pm2 restart codec-dashboard`
- **F13/F16 commands showing in Flash Chat?** Session cleanup was writing to the conversations table. This was fixed — session commands should only appear in History and Audit tabs.
</details>

<details>
<summary><strong>Microphone sending messages automatically</strong></summary>

- The mic button in the dashboard and chat uses continuous mode with `interimResults: true`. It does NOT auto-send — you must click the send button.
- If messages are auto-sending, clear browser cache and reload. An older cached version may have the auto-send behavior.
- The mic shows a red square stop button while recording. Click it to stop, then send manually.
</details>

<details>
<summary><strong>tkinter errors in Terminal sessions</strong></summary>

- Agent sessions spawn in Terminal via `python3.13`. If python3.13 doesn't have tkinter, command preview dialogs fail with `ModuleNotFoundError: No module named '_tkinter'`.
- Fix: `brew install python-tk@3.13`
- CODEC wraps all tkinter imports in try/except — if tkinter is missing, safe commands auto-approve and dangerous commands auto-deny.
</details>

<details>
<summary><strong>Stuck processes eating RAM</strong></summary>

- CODEC includes a watchdog (`codec-watchdog` in PM2) that monitors all Python, Terminal, and iTerm processes.
- It only kills processes using >500MB RAM with <0.5% CPU for 10+ consecutive minutes (truly stuck/zombie).
- Active processes are never killed — a model using 8GB at 80% CPU is safe.
- Check status: `pm2 logs codec-watchdog --lines 20 --nostream`
- If the watchdog itself is not running: `pm2 restart codec-watchdog`
</details>

<details>
<summary><strong>Complex questions not opening Terminal</strong></summary>

- CODEC routes queries by complexity: short queries (<60 chars AND <8 words) try instant skills first. Longer or complex questions always open a Terminal agent session.
- If a complex question is handled by a skill instead of Terminal, it's because a skill trigger matched. Check: `pm2 logs open-codec --lines 20 --nostream | grep -i skill`
- To force Terminal: make sure the question is >60 chars or >8 words, or doesn't match any skill trigger.
</details>

---

## Project Structure

```
codec.py              — Entry point (hotkeys, dispatch, wake word, recording)
codec_identity.py     — Shared CODEC identity, voice prompt, chat prompt
codec_config.py       — Configuration + transcript cleaning + 46 dangerous patterns
codec_dictate.py      — Dictation hotkeys (Cmd+R, L for live typing)
codec_watchdog.py     — Process monitor (kills stuck/zombie processes)
codec_dispatch.py     — Skill matching and dispatch (with fallback)
codec_agent.py        — LLM session builder
codec_agents.py       — Multi-agent crew framework (12 crews, 7 tools)
codec_voice.py        — WebSocket voice pipeline (reconnect, heartbeat)
codec_voice.html      — Voice call UI
codec_dashboard.py    — Web API + dashboard (75+ endpoints)
codec_dashboard.html  — Dashboard UI (Flash Chat, History, Audit, Settings, Stats, Skills)
codec_chat.html       — Chat UI (agents, file upload, voice input)
codec_vibe.html       — Vibe Code IDE (Monaco + Skill Forge)
codec_cortex.html     — Cortex system overview (neural map, product grid)
codec_audit.html      — Audit log viewer (16 categories, filterable)
codec_audit.py        — Audit logger (JSON-line, 50MB rotation, thread-safe)
codec_auth.html       — Authentication (Touch ID + PIN + TOTP 2FA)
codec_textassist.py   — 8 right-click services
codec_search.py       — DuckDuckGo + Serper search
codec_mcp.py          — MCP server (input validation, opt-in exposure)
codec_memory.py       — FTS5 memory search (WAL, BM25, injection prevention)
codec_compaction.py   — Context compaction (LLM-based summarization)
codec_heartbeat.py    — Health monitoring (5 services) + daily DB backup
codec_session.py      — Agent session runner (resource limits, command preview)
codec_scheduler.py    — Cron-like agent scheduling
codec_marketplace.py  — Skill marketplace CLI
codec_overlays.py     — AppKit overlay notifications (fullscreen compatible)
ax_bridge/            — Swift AX accessibility bridge
swift-overlay/        — Native macOS status bar app (NSPanel, event JSONL poller)
skills/               — 60 built-in skills (incl. vision mouse control)
tests/                — 405 pytest tests across 22 files
request_mic.py        — macOS microphone permission helper (AVFoundation)
install.sh            — One-line installer
setup_codec.py        — Setup wizard (9 steps)
ecosystem.config.js   — PM2 process management (10+ services)
```

---

## What's Coming

- [ ] Linux support
- [ ] Windows via WSL
- [ ] Multi-machine sync (skills + memory across devices)
- [ ] iOS app (dictation + remote dashboard)
- [ ] Streaming voice responses (first token plays while rest generates)
- [ ] Multi-LLM routing (fast model for simple, strong model for complex)

---

## Contributing

All skill contributions welcome. 60 built-in skills, 405 tests, marketplace growing.

```bash
git clone https://github.com/AVADSA25/codec.git
cd codec && ./install.sh
python3 -m pytest   # all tests must pass
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the skill template and trigger matching rules.

---

## Support the Project

If CODEC saves you time:

- **Star** this repo
- **[Donate via PayPal](https://paypal.me/avadsa25)** — ava.dsa25@proton.me
- **Enterprise setup:** [avadigital.ai](https://avadigital.ai)

---

## Professional Setup

Need CODEC configured for a business, integrated with existing tools, or deployed across a team?

[Contact AVA Digital](https://avadigital.ai) for professional setup and custom skill development.

---

<p align="center">
  Star it. Clone it. Rip it apart. Make it yours.
</p>
<p align="center">
  Built by <a href="https://avadigital.ai">AVA Digital LLC</a> · MIT License
</p>
