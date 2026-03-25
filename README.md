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

CODEC turns your computer into a voice-controlled AI workstation. Press a key or say *"Hey Q"* — CODEC listens, thinks (using any LLM you choose), and acts: opening apps, drafting messages, reading your screen, analyzing documents, controlling Spotify, setting timers, writing code, and anything else you can describe.

A private, open-source alternative to Siri/Alexa that actually controls your computer — and writes its own plugins.

*Built for macOS.* Linux support planned.

```
"Hey Q, open Safari and go to GitHub"             → Opens Safari, navigates to github.com
"Draft a reply saying thanks for the update"       → Reads screen, writes contextual reply, pastes it
"What's on my screen?"                             → Screenshots display, describes what it sees
"Play the next song"                               → Spotify skips to next track
"Create a skill that checks Bitcoin price"         → Writes, installs, and activates a new skill
"Remind me to review the PR at 3pm"               → Sets a native Apple Reminder
"Translate good morning to Japanese"               → Speaks the translation aloud
"How much disk space do I have?"                   → Checks system and reports back
```

### Advanced Use Cases

CODEC is not just a voice assistant — it is an AI agent with full computer control.

```
"Read my screen and summarize the email thread"    → Vision captures display, LLM summarizes
"Open Notes and create a meeting summary"          → Launches Apple Notes, writes structured notes
"Find all PDFs on my desktop and list them"        → Runs shell commands, reports results
"Draft a message to the team about the deadline"   → Reads chat context, composes professional reply
"Set volume to 30 and play my playlist"            → Chains multiple commands in one request
"Switch to VS Code and explain the selected code"  → App switch + screen read + code analysis
"Create a skill that monitors my CPU temperature"  → Self-writes a Python skill and installs it live
```

Double-tap `--` to launch **live voice-to-voice chat** — a real-time conversation with your AI, powered by Pipecat. No buttons, auto-connects. Like having JARVIS on speed dial.

---

## Quickstart

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

The wizard walks you through everything: choose your LLM (Ollama, OpenAI, Gemini, Anthropic, MLX, or any compatible server), choose your voice engine (Kokoro 82M, macOS Say, or disable), choose your STT (Whisper local server or disable), set keyboard shortcuts (defaults or custom), enable wake word ("Hey Q" hands-free activation), and pick your skills (14 built-in plugins plus self-writing skill creator).

### 4. Start CODEC

```bash
python3 codec.py
```

Press your toggle key to activate (default F13), then use your configured voice key (default F18) to speak commands.

### 5. Keep CODEC always running (recommended)

Use PM2 to keep CODEC running in the background and auto-start on reboot:

```bash
npm install -g pm2
pm2 start "python3 codec.py" --name codec
pm2 save
pm2 startup
```

Run the command that `pm2 startup` outputs to enable auto-start on reboot. CODEC will survive reboots and run 24/7.

**macOS permissions required:** CODEC needs keyboard access. Grant permissions in System Settings > Privacy & Security for both **Accessibility** and **Input Monitoring** (add Terminal or your Python binary).

### Optional: Voice Services

If you chose Kokoro TTS or Whisper STT in the wizard, start them before running CODEC:

```bash
# TTS (voice output) — Kokoro 82M, optimized for Apple Silicon
pip3 install mlx-audio misaki num2words phonemizer-fork spacy
python3 -m spacy download en_core_web_sm
python3 -m mlx_audio.server --host 0.0.0.0 --port 8085

# STT (voice input) — Whisper Large v3 Turbo via MLX
pip3 install mlx-whisper fastapi uvicorn
python3 whisper_server.py
```

Both servers expose OpenAI-compatible endpoints, so they work across your local network. Use `--host 0.0.0.0` to allow LAN access from other machines.

---

## Features

- **Voice Commands** — Hold your voice key to speak, release to send
- **Wake Word** — Say "Hey Q" hands-free, with intelligent noise filtering that rejects TV, music, and background chatter
- **Screen Reading** — Double-tap `**` to screenshot and ask about what you see
- **Document Analysis** — Double-tap `++` to load any file (PDF, text, code, images)
- **Live Voice Chat** — Double-tap `--` to launch a real-time voice conversation via Pipecat (optional)
- **Draft & Paste** — Say "draft a reply" and CODEC reads the screen, writes a contextual response, and pastes it into your active app
- **Task Execution** — Q-Agent runs bash and AppleScript commands step by step with safety checks
- **Command Chaining** — Multi-step instructions in one go ("set volume to 30 and play my playlist")
- **Self-Writing Skills** — Say "create a skill that does X" and CODEC writes and installs a Python plugin on the fly
- **Streaming Responses** — See words as the LLM generates them in real time
- **Persistent Memory** — Remembers context across sessions
- **15 Built-in Skills** — Calculator, weather, timer, Spotify, volume, notes, reminders, translate, and more
- **Custom Skills** — Drop a `.py` file in `~/.codec/skills/` or let CODEC write one for you
- **Consistent Voice** — Kokoro 82M TTS (82 million params, fast on Apple Silicon) or macOS Say
- **Multi-Machine Support** — Run voice services on a powerful Mac, control from any Mac on the network

## Supported LLM Providers

| Provider | Setup |
|---|---|
| **Ollama** | Free, local — `ollama serve` then select in wizard |
| **LM Studio** | Local GUI — start server then select in wizard |
| **MLX Server** | Apple Silicon optimized — best performance on M-series chips |
| **OpenAI** | Enter API key in wizard |
| **Anthropic** | Enter API key in wizard |
| **Google Gemini** | Enter API key in wizard (free tier available) |
| **Any OpenAI-compatible** | Enter URL in wizard — works with vLLM, TGI, LocalAI, etc. |

## Skills

| Skill | Example |
|---|---|
| Calculator | "Calculate 25 times 47" |
| Weather | "Weather in Tokyo" |
| Time & Date | "What time is it in London" |
| System Info | "How much disk space" / "Show battery" |
| Web Search | "Search for SpaceX latest launch" |
| Translate | "Say good morning in Japanese" |
| Notes | "Take a note: call bank tomorrow" |
| Timer | "Timer for 5 minutes" — speaks when done |
| Volume | "Volume to 50" / "Mute" |
| Reminders | "Remind me to review the PR" |
| Music | "Play Spotify" / "Next song" / "Pause" |
| Clipboard | "Show clipboard history" |
| App Switch | "Switch to Chrome" / "Open Terminal" |
| Create Skill | "Create a skill that checks Bitcoin price" |

### Create Your Own

```python
"""My Custom Skill"""
SKILL_NAME = "my_skill"
SKILL_TRIGGERS = ["trigger phrase", "another trigger"]
SKILL_DESCRIPTION = "What this skill does"

def run(task, app="", ctx=""):
    # Your logic here — full Python, shell commands, APIs, AppleScript
    return "Response spoken back to you"
```

Drop in `~/.codec/skills/` — auto-loads on restart. Or just ask CODEC: *"Create a skill that does X"* and it writes one for you using your LLM.

## Keyboard Shortcuts

All shortcuts are configurable via `setup_codec.py` or `~/.codec/config.json`.

| Default Key | Action |
|---|---|
| F13 | Toggle ON/OFF |
| F18 | Hold to record, release to send |
| F16 | Text input dialog |
| `**` | Double-tap star — screenshot and ask |
| `++` | Double-tap plus — load document for analysis |
| `--` | Double-tap minus — live voice chat (requires Pipecat) |
| "Hey Q" | Wake word (always-on, hands-free) |

**MacBook users:** MacBooks only have F1-F12. The setup wizard offers laptop-friendly defaults like `fn+F5`, `fn+F6`, `fn+F7`. You can also set "Use F1, F2, etc. keys as standard function keys" to ON in System Settings > Keyboard to avoid holding fn every time.

## Configuration

Re-run the wizard anytime:

```bash
python3 setup_codec.py
```

Or edit directly:

```bash
cat ~/.codec/config.json
```

Key config options include LLM provider and model, TTS/STT engine and voice, keyboard shortcuts, wake word phrases and sensitivity, draft keywords, pipecat URL for live chat, and require_confirmation for dangerous commands.

See `config.json.example` for all options.

## Safety and Guardrails

CODEC takes safety seriously. This is a tool with real computer control, and that requires real safeguards.

### Built-in protections (v1.1.1)

- **Dangerous command blocker.** CODEC maintains a blocklist of dangerous patterns (`rm -rf`, `sudo`, `shutdown`, `killall`, `dd`, `diskutil erase`, `curl|bash`, etc.). When the LLM generates a flagged command, CODEC warns you and asks for explicit confirmation before executing. Every flagged command is logged.

- **Full audit log.** Every task, command, wake word event, and blocked action is timestamped and written to `~/.codec/audit.log`. Review exactly what CODEC did and when.

- **Dry-run mode.** Start with `python3 codec.py --dry-run` to see what commands would execute without running them. Perfect for testing or building trust before going live.

- **Interactive confirmation.** Dangerous commands show a `[SAFETY] Execute this command? (y/n)` prompt. You decide — CODEC never runs risky commands silently.

- **No deletion without confirmation.** The Q-Agent will never delete files, folders, or data without asking first. Hardcoded, not optional.

- **8-step execution cap.** No task can run more than 8 steps, preventing runaway command chains.

- **Skill isolation.** Built-in skills handle common tasks without calling the LLM — no risk of misinterpretation for volume, timer, calculator, etc.

- **Wake word noise filtering.** Background noise, TV, and music are filtered using word-level analysis. CODEC won't act on random sounds.

- **Local-first by design.** You choose what runs locally and what touches the cloud. With Ollama or MLX, zero data leaves your machine.

- **You pick the LLM.** The guardrails of whatever model you connect apply on top of CODEC's own safety rules.

### Planned for v2

- Command preview mode — see what CODEC will do before it executes, with confirm/deny UI
- Customizable allowlist/blocklist for commands via config
- Signed commits and dependency pinning as the project grows
- Phone dashboard for remote monitoring

Have suggestions for additional safety measures? Open an issue. We take this seriously.

## Architecture

```
codec.py           → Main: keyboard listener, wake word, dispatch, safety checks
codec_watcher.py   → Draft agent: reads screen, writes + pastes replies
whisper_server.py  → STT server (optional)
setup_codec.py     → Interactive setup wizard (7 steps)
~/.codec/skills/   → Plugin folder (15 built-in + custom + self-written)
~/.codec/config.json → Your config
~/.codec/audit.log → Safety audit trail
```

## Live Voice Chat (Optional)

CODEC supports live voice-to-voice chat via [Pipecat](https://github.com/pipecat-ai/pipecat). Double-tap `--` to instantly open a real-time conversation in your browser — auto-connects, no clicking.

Think of it as two modes: **F18** is task mode (record a command, get a result), and **--** is conversation mode (live back-and-forth like talking to a person).

Requires a separate Pipecat server. See the [Pipecat docs](https://docs.pipecat.ai/) for setup. Configure in your config:

```json
{
  "pipecat_url": "http://localhost:3000/auto"
}
```

## Multi-Machine Setup

Run CODEC on a lightweight MacBook while offloading LLM, TTS, and STT to a powerful Mac on the same network.

**On your server Mac (e.g., Mac Studio):**

```bash
# LLM — Qwen 3.5 35B via MLX
python3 -m mlx_lm.server --model mlx-community/Qwen3.5-35B-A3B-4bit --port 8081

# TTS — Kokoro 82M (use --host 0.0.0.0 for LAN access)
python3 -m mlx_audio.server --host 0.0.0.0 --port 8085

# STT — Whisper Large v3 Turbo
python3 whisper_server.py
```

**On your client Mac (e.g., MacBook Air), set config.json:**

```json
{
  "llm_base_url": "http://192.168.1.73:8081/v1",
  "tts_url": "http://192.168.1.73:8085/v1/audio/speech",
  "stt_url": "http://192.168.1.73:8084/v1/audio/transcriptions"
}
```

Your MacBook becomes a thin client — all the heavy lifting happens on the server.

---

## Support the Project

CODEC is free and open source. If it saves you time or you want to see it grow, consider supporting development:

☕ **PayPal:** [ava.dsa25@proton.me](https://www.paypal.com/paypalme/avadsa25)

Every contribution helps fund development, testing on new hardware, and keeping CODEC independent and ad-free. Thank you.

⭐ **Star this repo** if you find CODEC useful — it helps others discover the project.

## Professional Setup

**Need AI infrastructure for your business?**

[AVA Digital LLC](https://avadigital.ai) specializes in deploying private, local AI systems for businesses and professionals. We design and configure complete voice-controlled AI workstations from the ground up — hardware selection, local LLM deployment, voice pipelines, custom skill development, multi-machine networks, and ongoing support.

Services include:

- **CODEC deployment** — Full setup, configuration, and customization for your workflow
- **Local LLM hosting** — Private AI that runs on your hardware, your data stays yours
- **Custom skill development** — Purpose-built CODEC skills for your industry or workflow
- **Multi-machine AI networks** — Distribute LLM, TTS, and STT across your office
- **AI workflow automation** — Voice-driven automation pipelines with n8n, Pipecat, and more
- **Consulting** — Architecture guidance for local-first AI infrastructure

📧 **ava.dsa25@proton.me** · 🌐 **[avadigital.ai](https://avadigital.ai)** · 🌐 **[opencodec.org](https://opencodec.org)**

---

## License

MIT License — use it however you want.

## Credits

Built by [AVA Digital LLC](https://avadigital.ai) · [opencodec.org](https://opencodec.org)

Powered by: [Ollama](https://ollama.com) · [Kokoro TTS](https://huggingface.co/hexgrad/Kokoro-82M) · [Whisper](https://github.com/openai/whisper) · [MLX](https://github.com/ml-explore/mlx) · [Pipecat](https://github.com/pipecat-ai/pipecat)
