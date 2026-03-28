<p align="center">
  <img src="https://i.imgur.com/RbrQ7Bt.png" alt="CODEC" width="280"/>
</p>

<h1 align="center">CODEC</h1>
<p align="center"><strong>Open Source Computer Command Framework</strong></p>
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

CODEC turns your computer into a voice-controlled AI workstation. Press a key or say *"Hey C"* — CODEC listens, thinks (using any LLM you choose), and acts: opening apps, drafting messages, reading your screen, analyzing documents, researching topics, writing code, and anything else you can describe.

A private, open-source alternative to Siri and Alexa that actually controls your computer — and writes its own plugins.

*Built for macOS.* Linux support planned.

```
"Hey C, open Safari and go to GitHub"             → Opens Safari, navigates to github.com
"Draft a reply saying thanks for the update"       → Reads screen, writes contextual reply, pastes it
"What's on my screen?"                             → Screenshots display, describes what it sees
"What's on my calendar today?"                     → Checks your Google Calendar, reads back schedule
"Research the latest AI agent frameworks"           → Searches web, writes report, creates Google Doc
"Create a skill that checks Bitcoin price"         → Writes, installs, and activates a new skill
```

### Right-Click Text Assistant

Select any text in any app, right-click, and choose from five CODEC services:

```
CODEC Proofread   → Fixes spelling, grammar, punctuation. Replaces text instantly.
CODEC Elevate     → Rewrites to be more polished and professional. Replaces text.
CODEC Explain     → Explains in simple terms. Opens in Terminal.
CODEC Prompt      → Rewrites as an optimized LLM prompt. Replaces text.
CODEC Translate   → Translates any language to English. Opens in Terminal.
CODEC Reply       → Reads the selected message, writes a natural reply. Add :direction for intent.
```

Works system-wide via macOS Services. Built for accessibility — particularly useful for dyslexia and ADHD. Your AI proofreader and translator is always one right-click away.

### Advanced Use Cases

CODEC is not just a voice assistant — it is an AI agent with full computer control.

```
"Read my screen and summarize the email thread"    → Vision captures display, LLM summarizes
"Draft a message to the team about the deadline"   → Reads chat context, composes professional reply
"Set volume to 30 and play my playlist"            → Chains multiple commands in one request
"Switch to VS Code and explain the selected code"  → App switch + screen read + code analysis
"Create a skill that monitors my CPU temperature"  → Self-writes a Python skill and installs it live
"Ask Lucy to schedule lunch tomorrow at 2pm"       → Delegates to external AI agent via webhook
```

Double-tap `--` to launch **live voice-to-voice chat** — a real-time conversation with your AI, powered by Pipecat. Auto-connects, no clicking. All conversations are saved to shared memory.

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

The wizard walks you through everything in 8 steps: LLM provider, voice engine, speech-to-text, keyboard shortcuts, wake word, features, skills (24 built-in), and phone dashboard.

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

## Features

### Voice and Text Control
- **Hold-to-talk voice** — hold F18, speak, release. Whisper transcribes, LLM processes, Kokoro speaks the answer.
- **Text input** — press F16 for a dialog box.
- **Wake word** — say "Hey C" hands-free from across the room. Customizable in config.
- **Draft and paste** — reads your screen, writes a reply, pastes it into whatever app you're using.
- **Live voice chat** — double-tap minus for real-time voice-to-voice conversation via Pipecat. Transcripts saved to shared memory.
- **Command Preview** — before executing any bash or AppleScript command, a popup shows what will run with Allow/Deny buttons. Full control.

### Right-Click Text Assistant (5 Services)
Select text anywhere, right-click, Services:
- **CODEC Proofread** — fixes spelling, grammar, punctuation instantly
- **CODEC Elevate** — rewrites to be more polished and professional
- **CODEC Explain** — explains the text in simple terms (opens in Terminal)
- **CODEC Prompt** — rewrites as an optimized prompt for any LLM
- **CODEC Translate** — translates any language to English (opens in Terminal)

### Deep Research
Type a research topic in Deep Chat, switch to Research mode. CrewAI agents search the web via Serper, Qwen synthesizes findings with deep reasoning, and CODEC creates a formatted Google Doc with the full report — complete with headers, citations, and images from Pexels.

### Vibe Code — AI-Powered IDE
Split-screen coding environment at `/vibe` on your dashboard.
- Monaco Editor (the VS Code engine) with syntax highlighting
- AI chat sidebar — describe what to build, C writes the code
- Auto-apply — code appears directly in the editor
- Live Preview — HTML/CSS/JS renders in embedded iframe
- Run button — execute Python, JavaScript, Bash directly
- Save as Skill — turn any script into a CODEC plugin with one click
- Project history sidebar — resume previous projects

### Deep Chat — 250K Context Window
Full AI chat at `/chat` on your dashboard.
- 250,000 token context window
- File upload with PDF extraction, images via vision model
- Drag and drop, microphone input
- Chat and Research mode toggle
- Conversation history sidebar

### 24 Built-in Skills

Skills fire instantly without calling the LLM.

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
| Volume | Volume control by voice |
| Apple Reminders | Add to Apple Reminders |
| Music | Control Spotify and Apple Music |
| Clipboard | Clipboard history |
| App Switch | Switch apps by name |
| Create Skill | Write new skills with natural language |
| Webhook Delegation | Delegate tasks to external AI agents |
| Google Calendar | Check your schedule |
| Google Gmail | Check inbox and search emails |
| Google Drive | Search and list files |
| Google Docs | Create and read documents |
| Google Sheets | Read and write spreadsheets |
| Google Slides | Create presentations |
| Google Tasks | Manage task lists |
| Google Keep | Create and manage notes |

### Google Workspace Integration
Direct access to Calendar, Gmail, Drive, Docs, Sheets, Slides, Tasks, and Keep. Pure Python with full read and write access. One-time OAuth setup.

### Phone Dashboard (PWA)
Control your Mac from your phone anywhere.
- Text commands and voice input with voice replies
- Screenshot your Mac display live
- Upload PDFs and images for AI analysis
- Deep Chat and Vibe Code access
- Chat history and audit log
- Dark and light mode

Two Python files. FastAPI backend, vanilla HTML frontend. No React, no npm. Point Cloudflare Tunnel at port 8090, add email auth, done.

### Agent Delegation via Webhooks
CODEC delegates complex tasks to external AI agents via webhooks. Connect to any webhook system — n8n, Make, Zapier, custom APIs. The external agent responds directly back through CODEC's voice. Fully private, fully extensible.

### Multi-Machine Setup
Run your LLM on a powerful Mac, use a lightweight MacBook as a thin client over LAN. The client sends voice to the server's Whisper, gets answers from the server's LLM, hears audio from the server's Kokoro. No model needed on the client.

### Shared Memory
All conversations are saved to a unified SQLite database — voice commands, text input, phone dashboard commands, Pipecat live chat, and skill responses. CODEC remembers everything across all input methods.

---

## Security

- **Command Preview UI** — every bash and AppleScript command shows a popup with Allow/Deny buttons before executing
- **Dangerous command blocker** — rm -rf, sudo, shutdown, killall and 20+ patterns require explicit confirmation
- **Full audit log** — every action logged to ~/.codec/audit.log with timestamps
- **Dry-run mode** — see what would execute without running anything
- **Wake word noise filter** — rejects TV, music, and background audio false triggers
- **8-step execution cap** on agent tasks
- **Skill isolation** — common tasks skip the LLM entirely
- **Cloudflare Zero Trust** — email authentication on phone dashboard
- **Code sandbox** — Vibe Code has 30-second timeout and blocks dangerous commands

---

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| F13 | Toggle CODEC ON/OFF (with sound effects) |
| F18 (hold) | Record voice, release to send |
| F16 | Text input dialog |
| ** (double-tap) | Screenshot and ask about screen |
| ++ (double-tap) | Open file picker for document analysis |
| -- (double-tap) | Live voice-to-voice chat via Pipecat |
| Right-click Services | Text Assistant (6 modes) |
| Hey C | Wake word — hands-free activation |

All shortcuts are configurable. CODEC's default assistant name is C — you can rename it to anything in your config.

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

Drop in ~/.codec/skills/ or use Vibe Code to build and save skills visually. Or just say "create a skill that does X" and CODEC writes one for you.

---

## Project Structure

```
codec.py              — Main agent
codec_watcher.py      — Draft and paste agent
codec_textassist.py   — Right-click text assistant (5 modes)
codec_dashboard.py    — Dashboard server (PWA + Deep Chat + Vibe Code)
codec_dashboard.html  — Phone dashboard
codec_chat.html       — Deep Chat + Deep Research interface
codec_vibe.html       — Vibe Code IDE
deep_research.py      — CrewAI research engine
setup_codec.py        — 8-step interactive installer
whisper_server.py     — Local Whisper STT server
pipecat_bot.py        — Live voice chat server
reauth_google.py      — Google OAuth helper
skills/               — 24 skill plugins
```

---

## What's Coming

- SwiftUI native macOS overlay
- Voice-to-voice with action execution (bidirectional, hands-free)
- Long-term vector memory
- Vibe Code inline editing and point-click preview elements
- Linux port
- Installable .dmg
- Skill marketplace
- AXUIElement accessibility API integration

---

## Requirements

- macOS (Ventura or later)
- Python 3.10+
- sox (brew install sox)
- An LLM (local or cloud)
- Optional: Whisper for STT, Kokoro for TTS

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

Powered by: [Ollama](https://ollama.com) · [Kokoro TTS](https://huggingface.co/hexgrad/Kokoro-82M) · [Whisper](https://github.com/openai/whisper) · [MLX](https://github.com/ml-explore/mlx) · [Pipecat](https://github.com/pipecat-ai/pipecat) · [CrewAI](https://github.com/crewAIInc/crewAI)
