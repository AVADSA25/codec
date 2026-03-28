<p align="center">
  <img src="https://i.imgur.com/RbrQ7Bt.png" alt="CODEC" width="280"/>
</p>

<h1 align="center">CODEC</h1>
<p align="center"><strong>Open Source Computer Command Framework — v1.4.0</strong></p>
<p align="center">
  Voice-controlled, local-first AI agent that runs on your machine with any LLM.<br/>
  No cloud. No subscription. No data leaves your computer.
</p>
<p align="center">
  <a href="https://opencodec.org">opencodec.org</a> · <a href="https://avadigital.ai">AVA Digital LLC</a>
</p>
<p align="center">
  <a href="#support-the-project">☕ Support the Project</a> · <a href="#professional-setup">🏢 Enterprise Setup</a>
</p>

---

## What is CODEC?

CODEC turns your computer into a voice-controlled AI workstation. Press a key or say *"Hey CODEC"* — CODEC listens, thinks (using any LLM you choose), and acts: opening apps, drafting messages, reading your screen, analyzing documents, researching topics, writing code, and anything else you can describe.

A private, open-source alternative to Siri and Alexa that actually controls your computer — and writes its own plugins.

*Built for macOS.* Linux support planned.

```
"Hey CODEC, open Safari and go to GitHub"         → Opens Safari, navigates to github.com
"Draft a reply saying yes but suggest Thursday"   → Reads screen, writes reply, pastes it
"What's on my screen?"                            → Screenshots display, describes what it sees
"What's on my calendar today?"                    → Checks Google Calendar, reads back schedule
"Research the latest AI agent frameworks"          → Searches web, writes report, creates Google Doc
"Create a skill that checks Bitcoin price"        → Writes, installs, and activates a new skill
```

**36 skills · 6 right-click services · 5 AI agent crews · 250K context · FTS5 memory · MIT licensed**

---

## Quick Start

### 1. Clone

```bash
git clone https://github.com/AVADSA25/codec.git
cd codec
```

### 2. Install dependencies

```bash
pip3 install pynput sounddevice soundfile numpy requests simple-term-menu
brew install sox
```

### 3. Run the setup wizard

```bash
python3 setup_codec.py
```

The wizard walks you through everything in 8 steps: LLM provider, voice engine, speech-to-text, keyboard shortcuts, wake word, features, skills, and phone dashboard.

### 4. Start CODEC

```bash
python3 codec.py
```

Press your toggle key to activate (default F13), then use your configured voice key (default F18) to speak commands.

### 5. Keep CODEC always running

```bash
npm install -g pm2
pm2 start "python3 codec.py" --name codec
pm2 save && pm2 startup
```

**macOS permissions required:** Grant Accessibility and Input Monitoring in System Settings > Privacy & Security.

### Optional: Voice Services

```bash
# TTS — Kokoro 82M, optimized for Apple Silicon
pip3 install mlx-audio misaki num2words phonemizer-fork spacy
python3 -m spacy download en_core_web_sm
python3 -m mlx_audio.server --host 0.0.0.0 --port 8085

# STT — Whisper Large v3 Turbo via MLX
pip3 install mlx-whisper fastapi uvicorn
python3 whisper_server.py
```

---

## 7 Product Frames

### Frame 1 — CODEC Core: Voice and Text Control

Your always-on AI command layer for macOS.

- **F13** — toggle CODEC on/off (with sound effects)
- **F18 (hold)** — hold to record, release to send voice command
- **F16** — text input dialog
- **`**` (double-tap)** — screenshot your screen and ask Q about it
- **`++` (double-tap)** — open file picker for document analysis
- **`--` (double-tap)** — start CODEC Voice live call
- **"Hey CODEC"** — always-on wake word, hands-free from across the room (customizable)
- **Draft & Paste** — reads the active screen, understands the conversation context, writes a natural reply, and pastes it instantly into Slack, WhatsApp, iMessage, email — any app
- **36 native skills** — fire instantly without calling the LLM (calculator, calendar, weather, music, web search, and more)
- **Command Preview UI** — every bash and AppleScript command shows an Allow/Deny popup before executing
- **FTS5 Memory Search** — full-text search over all conversations via SQLite FTS5 BM25 ranking. Say "search my memory for X" to recall anything

All shortcuts are configurable. CODEC's default assistant name is C — you can rename it to anything in your config.

---

### Frame 2 — CODEC Dictate: Hold to Speak Anywhere

A free, open-source SuperWhisper replacement.

- Hold **Right CMD**, speak naturally, release — text is transcribed and pasted directly into whatever app is active
- Whisper transcription → Qwen refinement for cleaner message output
- Works in any text field: email, Slack, Notes, VS Code, browser, terminal
- No popup, no modal — instant paste
- Powered by local Whisper STT. Zero latency, zero cloud

---

### Frame 3 — CODEC Assist: Right-Click Text Services

Select any text in any app, right-click, and choose from six CODEC services:

```
CODEC Proofread   → Fixes spelling, grammar, punctuation. Replaces text instantly.
CODEC Elevate     → Rewrites to be more polished and professional. Replaces text.
CODEC Explain     → Explains in simple terms. Opens in Terminal.
CODEC Prompt      → Rewrites as an optimized LLM prompt. Replaces text.
CODEC Translate   → Translates any language to English. Opens in Terminal.
CODEC Reply       → Reads the selected message, writes a natural reply. Add :direction for intent. Replaces text.
```

Works system-wide via macOS Services. Built for accessibility — particularly useful for dyslexia and ADHD. Your AI proofreader and translator is always one right-click away.

---

### Frame 4 — CODEC Chat: Deep Chat + AI Agents

Full AI chat at `/chat` on your dashboard.

- 250,000 token context window
- File upload with PDF extraction, images via vision model
- Drag and drop, microphone input, conversation history sidebar
- **CODEC Agents** — 5 pre-built multi-agent crews (no external dependencies):
  - **Deep Research** → multi-step web research → styled Google Doc with Pexels images
  - **Daily Briefing** → calendar + email + weather + news in one report
  - **Trip Planner** → web research → itinerary → Google Calendar events
  - **Competitor Analysis** → market research → strategic Google Doc report
  - **Email Handler** → reads Gmail → categorizes → drafts smart replies
- **Custom Agent Builder** — build your own agent from the chat UI: name it, write its system prompt, pick tools from all 36 skills, set max iterations. Save and reuse.

---

### Frame 5 — CODEC Vibe: AI-Powered IDE + Skill Forge

Split-screen coding environment at `/vibe`.

- Monaco Editor (VS Code engine) with syntax highlighting and language detection
- AI chat sidebar — describe what to build, Q writes and applies the code automatically
- **Skill Forge** — 3-mode skill creator:
  - **Paste Code** — paste any Python/JS → converted to CODEC skill
  - **GitHub URL** — paste raw GitHub/Gist URL → fetched and converted automatically
  - **Describe** — plain English description → Q generates skill from scratch
- Live Preview — HTML/CSS/JS renders in embedded iframe
- Run + Stop buttons — execute code, cancel generation
- Save as Skill — one click installs to `~/.codec/skills/`
- Project history sidebar with session persistence

---

### Frame 6 — CODEC Voice: Live Voice Calls with Skill Dispatch

Real-time voice-to-voice conversation at `/voice`.

- Full-duplex WebSocket audio pipeline — no external dependencies
- **Two-task architecture**: audio receiver + pipeline run concurrently
- **Interruption support**: start speaking and Q stops mid-sentence immediately
- **Skill dispatch**: Q recognizes requests in real-time and calls skills (calendar, web search, weather, etc.)
- All 36 skills accessible by voice during calls
- Complete transcript with streaming display
- Conversation saved to shared FTS5 memory after each call
- Double-tap `--` from dashboard to auto-connect
- Built from scratch — our own WebSocket pipeline

---

### Frame 7 — CODEC Remote: Private Web Dashboard (PWA)

Control your Mac from your phone anywhere.

- Text commands and voice input with voice replies
- Screenshot your Mac display live
- Upload PDFs and images for AI analysis
- Deep Chat, Vibe Code, and Voice Call access from any device
- Chat history and audit log
- Dark and light mode, Add to Home Screen
- FastAPI backend, vanilla HTML frontend — no React, no npm
- Cloudflare Tunnel + Zero Trust email authentication

---

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| F13 | Toggle CODEC ON/OFF (with sound effects) |
| F18 (hold) | Record voice, release to send |
| F16 | Text input dialog |
| Right CMD (hold) | CODEC Dictate — speak and paste anywhere |
| `**` (double-tap) | Screenshot and ask about screen |
| `++` (double-tap) | Open file picker for document analysis |
| `--` (double-tap) | Start CODEC Voice live call |
| Right-click Services | Text Assistant (6 modes) |
| Hey CODEC | Wake word — hands-free activation |

---

## 36 Built-in Skills

Skills fire instantly without calling the LLM. Used directly in voice, text, and agent crews.

| Skill | What it does |
|-------|-------------|
| Calculator | Quick math |
| Weather | Current weather by city |
| Time and Date | Current time and date |
| System Info | CPU, disk, memory stats |
| Web Search | DuckDuckGo instant answers |
| Translate | Multi-language translation |
| Apple Notes | Save and read notes |
| Timer | Set timers with voice alerts |
| Volume | Volume control |
| Brightness | Screen brightness control |
| Apple Reminders | Add to Apple Reminders |
| Music | Control Spotify and Apple Music |
| Clipboard | Clipboard history |
| App Switch | Switch apps by name |
| Create Skill | Write new skills with natural language |
| Memory Search | Full-text search over all conversations |
| Skill Forge | Convert any code to a CODEC skill |
| Network Info | IP, WiFi, connection details |
| Process Manager | List and kill processes |
| Terminal | Run shell commands by voice |
| Screenshot Text | Read text from your screen |
| File Search | Find files by name |
| Chrome Open/Close | Open and close Chrome |
| Chrome Search | Google search via Chrome |
| Chrome Read | Read current tab content |
| Chrome Tabs | Switch and list tabs |
| Google Calendar | Check schedule and create events |
| Google Gmail | Check inbox and search emails |
| Google Drive | Search and list files |
| Google Docs | Create and read documents |
| Google Sheets | Read and write spreadsheets |
| Google Slides | Create presentations |
| Google Tasks | Manage task lists |
| Google Keep | Create and manage notes |
| Webhook / Lucy | Delegate tasks to external AI agents |
| QR Code | Generate QR codes |

---

## Google Workspace Integration

Direct access to Calendar, Gmail, Drive, Docs, Sheets, Slides, Tasks, and Keep. Pure Python with full read and write access. One-time OAuth setup.

---

## CODEC Agents — Built-in Crews

CODEC ships with a fully local ReAct multi-agent framework. No external dependencies, no rate limits, no API keys beyond your LLM.

Each crew is a sequence of specialized agents with curated tool access:

```
Deep Research:
  Researcher (web_search + web_fetch, 5 calls) →
  Writer (google_docs_create, 2 calls) →
  Outputs: styled Google Doc with Pexels images

Daily Briefing:
  Scout (google_calendar + weather + web_search, 4 calls) →
  Outputs: text briefing read aloud

Trip Planner:
  Researcher (web_search + web_fetch, 5 calls) →
  Planner (google_docs_create + google_calendar, 2 calls) →
  Outputs: Google Doc itinerary + calendar events

Competitor Analysis:
  Analyst (web_search + web_fetch, 5 calls) →
  Writer (google_docs_create, 2 calls) →
  Outputs: styled competitor report in Google Docs

Email Handler:
  Handler (google_gmail + web_search, 4 calls) →
  Outputs: summary + draft replies
```

Custom agents: name, role prompt, tool selection, iterations — built and saved from the chat UI.

---

## Security

- **Command Preview UI** — every bash and AppleScript command shows a popup with Allow/Deny before executing
- **Dangerous command blocker** — rm -rf, sudo, shutdown, killall and 30+ patterns require explicit confirmation
- **Full audit log** — every action logged to `~/.codec/audit.log` with timestamps
- **Structured logging** — Python `logging` module with `[HH:MM:SS] [CODEC]` format
- **Wake word noise filter** — rejects TV, music, and background audio false triggers
- **8-step execution cap** on agent tasks
- **Skill isolation** — common tasks skip the LLM entirely
- **Cloudflare Zero Trust** — email authentication on phone dashboard
- **Code sandbox** — Vibe Code has 30-second timeout and blocks dangerous commands

---

## Custom Skills

```python
"""My Custom Skill"""
SKILL_NAME = "btc_price"
SKILL_TRIGGERS = ["bitcoin price", "btc price", "check bitcoin"]
SKILL_DESCRIPTION = "Check current Bitcoin price"

import requests

def run(task, app="", ctx=""):
    r = requests.get("https://api.coindesk.com/v1/bpi/currentprice.json", timeout=10)
    price = r.json()["bpi"]["USD"]["rate"]
    return f"Bitcoin is currently ${price}"
```

Drop in `~/.codec/skills/` or use **Skill Forge** in Vibe Code to convert any existing code. Or say "create a skill that does X" and CODEC writes one for you.

---

## Phone Dashboard Setup

```bash
python3 codec_dashboard.py
```

Local: http://localhost:8090

Remote via Cloudflare Tunnel:
1. brew install cloudflared
2. cloudflared tunnel create my-codec
3. Route DNS and add to config.yml
4. Add email auth in Cloudflare Zero Trust
5. On phone: open URL, Add to Home Screen

---

## Project Structure

```
codec.py              — Main agent (voice + text + wake word)
codec_watcher.py      — Draft and paste agent (CODEC Dictate)
codec_textassist.py   — Right-click text assistant (6 services)
codec_dashboard.py    — Dashboard server (PWA + APIs)
codec_dashboard.html  — Phone dashboard UI
codec_chat.html       — Deep Chat + Agent Crews
codec_vibe.html       — Vibe Code IDE + Skill Forge
codec_voice.html      — CODEC Voice live call UI
codec_voice.py        — Voice WebSocket pipeline (v2, two-task)
codec_agents.py       — CODEC Agents multi-agent framework
codec_memory.py       — SQLite FTS5 memory search
codec_gdocs.py        — Styled Google Docs creator
setup_codec.py        — 8-step interactive installer
whisper_server.py     — Local Whisper STT server
reauth_google.py      — Google OAuth helper
skills/               — 36 skill plugins
```

---

## Supported LLMs

| Provider | Setup |
|----------|-------|
| Ollama | ollama serve, select in wizard |
| LM Studio | Start server, point to localhost:1234 |
| MLX Server | Apple Silicon optimized, point to localhost:8081 |
| OpenAI | Paste API key |
| Anthropic | Paste API key |
| Google Gemini | Paste API key (free tier works) |
| Any OpenAI-compatible | Enter base URL and model |

---

## Requirements

- macOS (Ventura or later)
- Python 3.10+
- sox (`brew install sox`)
- An LLM (local or cloud)
- Optional: Whisper for STT, Kokoro for TTS

---

## What's Coming

- SwiftUI native macOS overlay
- Long-term vector memory
- Vibe Code inline editing and point-click preview elements
- Linux port
- Installable .dmg
- Skill marketplace
- AXUIElement accessibility API integration

---

## Contributing

MIT licensed. Use it however you want. Found a bug? Open an issue. Built a skill? Submit a PR.

---

## Support the Project

CODEC is free and open source. If it saves you time or you want to see it grow:

☕ **PayPal:** [ava.dsa25@proton.me](https://www.paypal.com/paypalme/avadsa25)

⭐ **Star this repo** — it helps others discover the project.

## Professional Setup

**Need AI infrastructure for your business?**

[AVA Digital LLC](https://avadigital.ai) deploys private, local AI systems. CODEC setup, custom skills, multi-machine networks, voice pipelines, and ongoing support.

📧 **mikarina@avadigital.ai** · 🌐 **[avadigital.ai](https://avadigital.ai)** · 🌐 **[opencodec.org](https://opencodec.org)**

---

MIT License

Built by [AVA Digital LLC](https://avadigital.ai) · [opencodec.org](https://opencodec.org)

Powered by: [Ollama](https://ollama.com) · [Kokoro TTS](https://huggingface.co/hexgrad/Kokoro-82M) · [Whisper](https://github.com/openai/whisper) · [MLX](https://github.com/ml-explore/mlx) · [CODEC Voice](https://github.com/AVADSA25/codec) · [CODEC Agents](https://github.com/AVADSA25/codec)
