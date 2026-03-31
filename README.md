<p align="center">
  <img src="https://i.imgur.com/RbrQ7Bt.png" alt="CODEC" width="280"/>
</p>

<h1 align="center">CODEC</h1>
<p align="center"><strong>Open-Source Intelligent Command Layer for macOS</strong></p>
<p align="center"><em>Your voice. Your computer. Your rules. No limit.</em></p>
<p align="center">
  <a href="https://opencodec.org">opencodec.org</a> · <a href="https://avadigital.ai">AVA Digital LLC</a> · <a href="#quick-start">Get Started</a> · <a href="#support-the-project">Support</a> · <a href="#professional-setup">Enterprise</a>
</p>

---

## Why CODEC Exists

All I wanted was to speak to my computer. Tell it to read my screen and reply to a message. Have it hear me think out loud and act on it — privately, on my hardware, with nothing leaving my machine.

Nothing existed that did this. So I started building.

CODEC is a framework that turns your Mac into a voice-controlled AI workstation. You give it a brain (any LLM — local or cloud), ears (Whisper), a voice (Kokoro), and eyes (vision model). Four ingredients. The rest is Python.

From there, it listens, sees your screen, speaks back, controls your apps, writes code, drafts your messages, researches topics, manages your Google Workspace, and when it doesn't know how to do something — it writes its own plugin and learns.

No cloud. No subscription. No data leaving your machine. MIT licensed.

---

## 7 Products. One System.

### CODEC Core — The Command Layer

Always-on voice assistant. Say *"Hey CODEC"* or press F13 to activate. F18 for voice commands. F16 for text input.

50+ skills fire instantly: Google Calendar, Gmail, Drive, Docs, Sheets, Tasks, Keep, Chrome automation, web search, Hue lights, timers, Spotify, clipboard, terminal commands, and more. Most skills bypass the LLM entirely — direct action, zero latency.

The interaction that made me know this was possible: hands-free, say *"Hey CODEC, look at my screen and reply to this message."* CODEC reads your screen, sees the conversation, writes a contextual response, and pastes it into the text field. Try that with Siri.

### CODEC Dictate — Hold, Speak, Paste

Hold a key. Say what you mean. Release. Text appears wherever your cursor is. If CODEC detects you're drafting a message, it refines through the LLM first — grammar fixed, tone polished, meaning preserved. Works in every app on macOS. A free, open-source SuperWhisper replacement that runs entirely on your machine.

### CODEC Instant — One Right-Click

Select any text, anywhere. Right-click. Eight AI services system-wide: Proofread, Elevate, Explain, Translate, Reply (with `:tone` syntax), Prompt, Read Aloud, Save. Powered by your own LLM. Reducing text manipulation to a single click.

### CODEC Chat — 250K Context + 12 Agent Crews

Full conversational AI on your hardware. Long context. File uploads. Image analysis via vision model. Web search. Conversation history.

Plus 12 autonomous agent crews — not single prompts, full multi-step workflows. Say *"research the latest AI agent frameworks and write a report."* Minutes later there's a formatted Google Doc in your Drive with sources, images, and recommendations. Local inference. Zero cloud costs.

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

The multi-agent framework is under 800 lines. Zero dependencies. No CrewAI. No LangChain.

### CODEC Vibe — AI Coding IDE + Skill Forge

Split-screen in your browser. Monaco editor on the left (same engine as VS Code). AI chat on the right. Describe what you want — CODEC writes it, you click Apply, run it, live preview in browser. Point your cursor to select what needs fixing, auto-reload on errors.

Skill Forge takes it further: describe a new capability in plain English, CODEC converts it into a working plugin. The framework writes its own extensions.

### CODEC Voice — Live Voice Calls

Real-time voice-to-voice conversations with your AI. WebSocket pipeline — no Pipecat, no external dependencies. Call CODEC from your phone, talk naturally, and mid-call say *"check my screen"* — it takes a screenshot, analyzes it, and speaks the result back.

Full transcript saved to memory. Every conversation becomes searchable context for future sessions.

### CODEC Overview — Your Mac in Your Pocket

Private dashboard accessible from any device, anywhere. Cloudflare Tunnel or Tailscale VPN — no port forwarding, no third-party relay. Send commands, view your screen, launch voice calls, manage agents — all from a browser on your phone.

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
  <em>50+ skills loaded at startup</em>
</p>
</details>

---

## What CODEC Replaced

Every feature started by depending on established tools, then was progressively replaced with native code:

| Before | After |
|---|---|
| Pipecat | CODEC Voice (own WebSocket pipeline) |
| CrewAI + LangChain | CODEC Agents (795 lines, zero dependencies) |
| SuperWhisper | CODEC Dictate (free, open source) |
| Cursor / Windsurf | CODEC Vibe (Monaco + AI + Skill Forge) |
| Google Assistant / Siri | CODEC Core (actually controls your computer) |
| Grammarly | CODEC Instant (right-click services via your own LLM) |
| ChatGPT | CODEC Chat (250K context, fully local) |
| Cloud LLM APIs | Local stack (Qwen + Whisper + Kokoro + Vision) |
| Vector databases | FTS5 SQLite (simpler, faster, yours) |

**External services:** DuckDuckGo for web search. Cloudflare free tier for the tunnel (or Tailscale). Everything else is your hardware, your models, your code.

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
| `* *` | Screenshot + AI analysis |
| `+ +` | Document mode |
| Camera icon | Live webcam PIP — drag around, snapshot anytime |
| Select text → right-click | 8 AI services in context menu |

**Laptop (F1-F12):** F5 = toggle, F8 = voice, F9 = text input. Run `python3 setup_codec.py` → select "Laptop / Compact" in Step 4.

Custom shortcuts in `~/.codec/config.json`. Restart after changes: `pm2 restart open-codec`

---

## Privacy & Security

This isn't a marketing section. It's the architecture.

Hey Siri. Hey Alexa. Where do those commands go? Through someone else's servers, hardware, and database. CODEC runs on your machine. Period.

**5-layer security stack:**

| Layer | Protection |
|---|---|
| Network | Cloudflare Zero Trust tunnel or Tailscale VPN, CORS restricted origins |
| Auth | Touch ID + PIN + TOTP 2FA, timing-safe token comparison |
| Encryption | AES-256-GCM + ECDH P-256 key exchange, per-session keys |
| Execution | Subprocess isolation, resource limits (512MB RAM, 120s CPU), command blocklist, human review gate |
| Data | Local SQLite, parameterized queries, FTS5 full-text search — searchable, readable, private, yours |

Every conversation is stored locally in SQLite with FTS5 full-text search. No cloud sync. No analytics. No telemetry. That's not a feature. That's the point.

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

Skills opt-in to MCP exposure with `SKILL_MCP_EXPOSE = True`.

---

## Debugging & Development Assistance

CODEC is a complex system with many moving parts. Loading the project into an AI-assisted IDE dramatically speeds up setup, debugging, and development.

**Recommended tools:**

| Tool | How it helps |
|---|---|
| **[Claude Code](https://claude.ai/claude-code)** | Terminal AI — reads your full codebase, runs commands, fixes errors in context |
| **[Cursor](https://cursor.com)** | AI IDE — navigate CODEC's 60+ files, refactor, debug with full project awareness |
| **[Windsurf](https://windsurf.ai)** | AI IDE — similar to Cursor, strong at multi-file reasoning |
| **[Antigravity](https://antigravity.dev)** | AI debugging assistant — paste errors, get fixes with codebase context |

**Quick debug commands:**

```bash
# Check all services
pm2 list

# Check specific service logs
pm2 logs open-codec --lines 30 --nostream        # Main CODEC process
pm2 logs codec-dashboard --lines 30 --nostream    # Dashboard API
pm2 logs whisper-stt --lines 10 --nostream        # Speech-to-text
pm2 logs kokoro-82m --lines 10 --nostream         # Text-to-speech

# Verify LLM is responding
curl -s http://localhost:8081/v1/models | python3 -m json.tool

# Verify dashboard is up
curl -s http://localhost:8090/health

# Verify Whisper STT
curl -s http://localhost:8084/v1/models

# Verify Kokoro TTS
curl -s http://localhost:8085/v1/models

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

- Check Whisper: `pm2 logs whisper-stt --lines 5 --nostream`
- Check mic permission: System Settings → Privacy → Microphone
- Say "Hey CODEC" clearly — 3 distinct syllables
</details>

<details>
<summary><strong>No voice output</strong></summary>

- Check Kokoro TTS: `curl http://localhost:8085/v1/models`
- Fallback: `"tts_engine": "say"` in config.json (macOS built-in)
- Disable: `"tts_engine": "none"`
</details>

<details>
<summary><strong>Dashboard not loading</strong></summary>

- Check: `curl http://localhost:8090/health`
- Restart: `pm2 restart codec-dashboard`
- Remote via Cloudflare: `pm2 logs cloudflared --lines 3 --nostream`
- Remote via Tailscale: access CODEC at `http://100.x.x.x:8090` — no domain needed
</details>

<details>
<summary><strong>Agents timing out</strong></summary>

- First run takes 2-5 min — multi-step research with multiple searches
- Check: `pm2 logs codec-dashboard --lines 30 --nostream | grep -i agent`
- Agents run as background jobs — no Cloudflare timeout
</details>

---

## Project Structure

```
codec.py              — Entry point
codec_config.py       — Configuration + transcript cleaning
codec_keyboard.py     — Keyboard listener, PTT lock, wake word
codec_dispatch.py     — Skill matching and dispatch
codec_agent.py        — LLM session builder
codec_agents.py       — Multi-agent crew framework (12 crews)
codec_voice.py        — WebSocket voice pipeline
codec_voice.html      — Voice call UI
codec_dashboard.py    — Web API + dashboard (60+ endpoints)
codec_dashboard.html  — Dashboard UI
codec_chat.html       — Chat UI
codec_vibe.html       — Vibe Code IDE
codec_auth.html       — Authentication (Touch ID + PIN + TOTP 2FA)
codec_textassist.py   — 8 right-click services
codec_search.py       — DuckDuckGo + Serper search
codec_mcp.py          — MCP server
codec_memory.py       — FTS5 memory search
codec_heartbeat.py    — Health monitoring + task auto-execution
codec_scheduler.py    — Cron-like agent scheduling
codec_marketplace.py  — Skill marketplace CLI
codec_overlays.py     — AppKit overlay notifications (fullscreen compatible)
ax_bridge/            — Swift AX accessibility bridge
swift-overlay/        — SwiftUI status bar app
skills/               — 50+ built-in skills
tests/                — 212+ pytest tests
install.sh            — One-line installer
setup_codec.py        — Setup wizard (9 steps)
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

All skill contributions welcome. 50+ built-in, marketplace growing.

```bash
git clone https://github.com/AVADSA25/codec.git
cd codec && ./install.sh
python3 -m pytest   # all tests must pass
```

See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Support the Project

If CODEC saves you time:

- **Star** this repo
- **[Donate via PayPal](https://paypal.me/avadsa25)** — ava.dsa25@proton.me
- **Enterprise setup:** [avadigital.ai](https://avadigital.ai)

---

## Professional Setup

Need CODEC configured for your business, integrated with your tools, or deployed across a team?

[Contact AVA Digital](https://avadigital.ai) for professional setup and custom skill development.

---

<p align="center">
  Star it. Clone it. Rip it apart. Make it yours.
</p>
<p align="center">
  Built by <a href="https://avadigital.ai">AVA Digital LLC</a> · MIT License
</p>
