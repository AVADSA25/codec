# CODEC — Open Source Computer Command Framework

> **Your voice. Your computer. Your rules.**
> Turn any LLM into a full computer agent — voice, text, always-on, fully local.

[![MIT License](https://img.shields.io/badge/License-MIT-orange.svg)](LICENSE)
[![macOS](https://img.shields.io/badge/Platform-macOS-blue.svg)]()
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-green.svg)]()
[![Stars](https://img.shields.io/github/stars/AVADSA25/codec?style=social)](https://github.com/AVADSA25/codec)

**CODEC** is an open source framework that connects any LLM directly to your Mac — voice, keyboard, wake word, phone dashboard, and a full IDE. You speak or type, your machine executes. Not a chatbot. Not a wrapper. An actual bridge between you and your operating system.

24 skills. Zero cloud dependency. MIT licensed.

[opencodec.org](https://opencodec.org) | [GitHub](https://github.com/AVADSA25/codec)

---

## What It Does

You say **"Hey C, open Safari and search for flights to Tokyo"** — it opens your browser and does it.

You say **"draft a reply saying I'll review it tonight"** — it reads your screen, sees the email or Slack message, writes a polished reply, and pastes it right into the text field.

You say **"what's on my screen"** — it screenshots your display, runs it through a vision model, and describes everything it sees.

You say **"create a skill that checks Bitcoin price"** — it writes a Python plugin on the spot, drops it in the skills folder, and it works immediately.

You say **"ask Lucy to schedule lunch with John tomorrow at 2pm"** — it delegates to your personal AI assistant running on n8n, who adds the event to your Google Calendar and confirms back through voice.

From your phone at dinner, you type **"check if the backup finished"** — your Mac runs the command silently and sends back the result through your own Cloudflare tunnel.

All of this works by voice, by text, by wake word, or from your phone.

---

## Quick Start

```bash
git clone https://github.com/AVADSA25/codec.git
cd codec
pip3 install pynput sounddevice soundfile numpy requests simple-term-menu
brew install sox
python3 setup_codec.py
python3 codec.py
```

Five minutes from clone to "Hey C, what time is it."

---

## Features at a Glance

### Voice and Text Control
- **Hold-to-talk voice** — hold F18, speak, release. Whisper transcribes, LLM processes, Kokoro speaks the answer.
- **Text input** — press F16 for a dialog box.
- **Wake word** — say "Hey C" hands-free from across the room.
- **Draft and paste** — reads your screen, writes a reply, pastes it into whatever app you're using. Slack, WhatsApp, email, anything.
- **Live voice chat** — double-tap minus for real-time voice-to-voice conversation via Pipecat.

### Right-Click Text Assistant
Select any text in any app, right-click, Services:
- **CODEC Proofread** — fixes spelling, grammar, punctuation. Replaces the text instantly.
- **CODEC Elevate** — rewrites to be more polished and professional.
- **CODEC Explain** — explains the text in simple terms, opens in Terminal.

Works system-wide. Particularly useful for dyslexia and ADHD.

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
| Lucy VPA | Delegate tasks to external AI assistant |
| Google Calendar | Check your schedule |
| Google Gmail | Check inbox and search emails |
| Google Drive | Search and list files |
| Google Docs | Create and read documents |
| Google Sheets | Read and write spreadsheets |
| Google Slides | Create presentations |
| Google Tasks | Manage task lists |
| Google Keep | Create and manage notes |

Custom skills are Python files. Write 20 lines, drop in a folder, CODEC loads it.

### Google Workspace Integration
Direct access to Calendar, Gmail, Drive, Docs, Sheets, Slides, Tasks, and Keep. Pure Python, no n8n required. One-time OAuth setup.

### Phone Dashboard (PWA)
Control your Mac from your phone anywhere on the planet.
- Text commands, voice input, voice replies
- Screenshot your Mac display live
- Upload PDFs and images for AI analysis
- Full chat history and audit log
- Dark and light mode

Two Python files. FastAPI backend, vanilla HTML frontend. No React, no npm. Point Cloudflare Tunnel at port 8090, add email auth, done.

### Deep Chat — 250K Context Window
Full AI chat at `/chat` on your dashboard. Drop entire codebases, long documents, research papers. File upload with PDF extraction, drag and drop, microphone input, conversation history sidebar.

### Vibe Code — AI-Powered IDE
Split-screen coding environment at `/vibe`. Monaco editor (VS Code engine), Mike Chat sidebar, live preview panel. Ask Mike to build something, code appears in the editor, preview opens automatically. Run Python, JavaScript, or Bash directly. Save as CODEC skill with one click. Project history sidebar.

### Agent Delegation
CODEC delegates complex tasks to external AI agents via webhooks. The Lucy skill sends commands to n8n workflows. Lucy responds directly back to Q through a synchronous webhook — fully private, no Telegram. This works with any webhook system: n8n, Make, Zapier, custom APIs.

### Multi-Machine Setup
Run your LLM on a Mac Studio, use a MacBook Air as a thin client over LAN. The Air sends voice to the Studio's Whisper, gets answers from the Studio's LLM, hears audio from the Studio's Kokoro. No model needed on the Air.

---

## Security

- **Dangerous command blocker** — rm -rf, sudo, shutdown and 20+ patterns require y/n confirmation
- **Full audit log** — every action logged to ~/.codec/audit.log with timestamps
- **Dry-run mode** — see what would execute without running anything
- **Wake word noise filter** — rejects TV and music false triggers
- **8-step execution cap** on agent tasks
- **Skill isolation** — common tasks skip the LLM
- **Cloudflare Zero Trust** — email auth on phone dashboard
- **Code sandbox** — Vibe Code has 30-second timeout and blocks dangerous commands

---

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| F13 | Toggle CODEC ON/OFF (with sound) |
| F18 (hold) | Record voice, release to send |
| F16 | Text input dialog |
| ** (double-tap) | Screenshot and ask about screen |
| ++ (double-tap) | Open file picker for document analysis |
| -- (double-tap) | Live voice chat via Pipecat |
| Right-click Services | Text Assistant |
| Hey C | Wake word activation |

All shortcuts are configurable in ~/.codec/config.json.

---

## Supported LLMs

| Provider | Setup |
|----------|-------|
| Ollama | ollama serve, select in wizard |
| LM Studio | Start server, point to localhost:1234 |
| MLX Server | mlx_lm.server, point to localhost:8081 |
| OpenAI | Paste API key |
| Anthropic | Paste API key |
| Google Gemini | Paste API key (free tier works) |
| Any OpenAI-compatible | Enter base URL and model |

Tested on Mac Studio M1 Ultra with Mikewen 3.5 35B locally, and on MacBook Air M2 with Gemini free tier.

---

## Phone Dashboard Setup

```bash
python3 codec_dashboard.py
```

Local: http://localhost:8090

Remote via Cloudflare Tunnel:
1. brew install cloudflared
2. cloudflared tunnel create my-codec
3. cloudflared tunnel route dns my-codec codec.yourdomain.com
4. Add to config.yml, add email auth in Zero Trust
5. On phone: open URL in Chrome, Add to Home Screen

---

## Google Workspace Setup

```bash
pip3 install google-api-python-client google-auth-oauthlib --break-system-packages
```

1. Google Cloud Console — Create OAuth Client ID (Desktop app)
2. Download JSON to ~/.codec/google_credentials.json
3. Enable Calendar, Gmail, Drive, Docs, Sheets, Slides, Tasks APIs
4. Run auth: python3 reauth_google.py
5. All Google skills work immediately

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

Drop in ~/.codec/skills/, restart CODEC. Or use Vibe Code to build and save skills visually.

---

## Project Structure

```
codec.py              — Main agent
codec_watcher.py      — Draft and paste agent
codec_textassist.py   — Right-click text assistant
codec_dashboard.py    — Dashboard server (PWA + Deep Chat + Vibe Code)
codec_dashboard.html  — Phone dashboard
codec_chat.html       — Deep Chat interface
codec_vibe.html       — Vibe Code IDE
setup_codec.py        — 8-step interactive installer
whisper_server.py     — Local Whisper STT server
reauth_google.py      — Google OAuth helper
skills/               — 24 skill plugins
```

---

## Requirements

- macOS (Ventura or later)
- Python 3.10+
- sox (brew install sox)
- An LLM (local or cloud)
- Optional: Whisper for STT, Kokoro for TTS

Linux support planned.

---

## Contributing

MIT licensed. Use it however you want. Found a bug? Open an issue. Built a skill? Submit a PR. Want Linux support? Let's talk.

---

## Support

PayPal: ava.dsa25@proton.me

Professional AI automation services:
AVA Digital LLC — custom AI agents, n8n workflows, voice automation.

mikarina@avadigital.ai | [avadigital.ai](https://avadigital.ai) | [opencodec.org](https://opencodec.org)

---

MIT License — CODEC v1.3.0

Built by Mickael Farina — AVA Digital LLC
EITCA/AI Certified | Based in Marbella, Spain
We speak AI, so you don't have to.
