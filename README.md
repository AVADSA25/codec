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
  <a href="https://opencodec.org">opencodec.org</a> · AVA Digital LLC
</p>

---

## What is CODEC?

CODEC turns your computer into a voice-controlled AI workstation. Press a key or say *"Hey Q"* — CODEC listens, thinks (using any LLM you choose), and acts: opening apps, drafting messages, reading your screen, analyzing documents, controlling Spotify, setting timers, and anything else you can describe.

A private, open-source alternative to Siri/Alexa that actually controls your computer.

*Built for macOS.* Linux support planned.

"Hey Q, open Safari and go to GitHub"       → Opens Safari, navigates to github.com
"Draft a reply saying thanks"                → Reads screen, writes reply, pastes it
"What's on my screen?"                       → Screenshots display, describes what it sees
"Play the next song"                         → Spotify skips to next track


## Quickstart

### 1. Clone
⁠ bash
git clone https://github.com/AVADSA25/codec.git
cd codec
 ⁠

### 2. Install dependencies
⁠ bash
pip3 install pynput sounddevice soundfile numpy requests simple-term-menu
brew install sox
 ⁠

### 3. Run the setup wizard
⁠ bash
python3 setup_codec.py
 ⁠

The wizard walks you through everything:
•⁠  ⁠*Choose your LLM* — Ollama, OpenAI, Gemini, Anthropic, MLX, or any compatible server
•⁠  ⁠*Choose your voice* — Kokoro 82M, macOS Say, or disable
•⁠  ⁠*Choose your STT* — Whisper local server or disable
•⁠  ⁠*Set keyboard shortcuts* — defaults or custom
•⁠  ⁠*Enable wake word* — "Hey Q" hands-free activation
•⁠  ⁠*Pick your skills* — 13 built-in plugins

### 4. Start CODEC
⁠ bash
python3 codec.py
 ⁠

Press *F13* to activate, then:
•⁠  ⁠*F18* — hold to record voice, release to send
•⁠  ⁠*F16* — type a command
•⁠  ⁠*"Hey Q"* — wake word (hands-free)

### Optional: Voice Services

If you chose Kokoro TTS or Whisper STT in the wizard, start them before running CODEC:
⁠ bash
# TTS (voice output) — only if you selected Kokoro in setup
pip3 install mlx-audio misaki num2words phonemizer-fork spacy
python3 -m spacy download en_core_web_sm
python3 -m mlx_audio.server --port 8085

# STT (voice input) — only if you selected Whisper in setup
pip3 install mlx-whisper fastapi uvicorn
python3 whisper_server.py
 ⁠

## Features

•⁠  ⁠*Voice Commands* — Hold F18 to speak, release to send
•⁠  ⁠*Wake Word* — Say "Hey Q" hands-free
•⁠  ⁠*Screen Reading* — Double-tap ** to screenshot + ask about what you see
•⁠  ⁠*Document Analysis* — Double-tap ++ to load any file
•⁠  ⁠*Draft & Paste* — Say "draft a reply" and CODEC writes + pastes into your chat app
•⁠  ⁠*Task Execution* — Q-Agent runs bash/AppleScript commands step by step
•⁠  ⁠*Command Chaining* — Multi-step instructions in one go
•⁠  ⁠*Streaming Responses* — See words as the LLM generates
•⁠  ⁠*Persistent Memory* — Remembers across sessions
•⁠  ⁠*13 Built-in Skills* — Calculator, weather, timer, Spotify, volume, notes, and more
•⁠  ⁠*Custom Skills* — Drop a .py file in ~/.codec/skills/
•⁠  ⁠*Consistent Voice* — Kokoro 82M TTS or macOS Say

## Supported LLM Providers

| Provider | Setup |
|---|---|
| *Ollama* | Free, local — ⁠ ollama serve ⁠ then select in wizard |
| *LM Studio* | Local GUI — start server then select in wizard |
| *MLX Server* | Apple Silicon optimized — select in wizard |
| *OpenAI* | Enter API key in wizard |
| *Anthropic* | Enter API key in wizard |
| *Google Gemini* | Enter API key in wizard (free tier available) |
| *Any OpenAI-compatible* | Enter URL in wizard |

## Skills

| Skill | Example |
|---|---|
| Calculator | "Calculate 25 times 47" |
| Weather | "Weather in Tokyo" |
| Time & Date | "What time is it" |
| System Info | "How much disk space" |
| Web Search | "Search for SpaceX" |
| Translate | "Say good morning in French" |
| Notes | "Take a note: call bank tomorrow" |
| Timer | "Timer for 5 minutes" |
| Volume | "Volume to 50" / "Mute" |
| Reminders | "Remind me to review code" |
| Music | "Play Spotify" / "Next song" |
| Clipboard | "Show clipboard history" |
| App Switch | "Switch to Chrome" |

### Create Your Own
⁠ python
"""My Custom Skill"""
SKILL_NAME = "my_skill"
SKILL_TRIGGERS = ["trigger phrase"]

def run(task, app="", ctx=""):
    return "Response spoken back to you"
 ⁠

Drop in ⁠ ~/.codec/skills/ ⁠ — auto-loads on restart.

## Configuration

Setup wizard saves to ⁠ ~/.codec/config.json ⁠. Edit directly or re-run ⁠ python3 setup_codec.py ⁠.

See ⁠ config.json.example ⁠ for all options.

## Safety and Guardrails

CODEC takes safety seriously. This is a tool with real computer control, and that requires real safeguards.

Built-in protections:

- No deletion without confirmation. The Q-Agent will never delete files, folders, or data without explicitly asking you first. This is hardcoded into the agent — not a suggestion, a rule.
- 8-step execution cap. No task can run more than 8 steps, preventing runaway command chains.
- Skill isolation. The 13 built-in skills handle common tasks instantly without calling the LLM at all — no risk of misinterpretation for things like volume, timer, calculator.
- Dispatch classification. Every command goes through a classifier before execution — it determines whether something is a skill, a draft, a question, or a task before any action is taken.
- Wake word filtering. Background noise and TV audio are filtered out so CODEC doesn't act on random sounds.
- Local-first by design. You choose what runs locally and what touches the cloud. With Ollama or MLX, zero data leaves your machine.
- You pick the LLM. The guardrails of whatever model you connect (Gemini, Claude, GPT, Llama) apply on top of CODEC's own safety rules.

What we're adding in v2:

- Command preview mode — see what CODEC will do before it executes
- Allowlist/blocklist for commands — restrict what the agent can run
- Confirmation prompts for system-level changes (not just deletes)
- Audit log of every action taken

If you have suggestions for additional safety measures, open an issue on GitHub. We take this seriously.

## Safety and Guardrails

CODEC takes safety seriously. This is a tool with real computer control, and that requires real safeguards.

Built-in protections:

- No deletion without confirmation. The Q-Agent will never delete files, folders, or data without explicitly asking you first. This is hardcoded into the agent — not a suggestion, a rule.
- 8-step execution cap. No task can run more than 8 steps, preventing runaway command chains.
- Skill isolation. The 13 built-in skills handle common tasks instantly without calling the LLM at all — no risk of misinterpretation for things like volume, timer, calculator.
- Dispatch classification. Every command goes through a classifier before execution — it determines whether something is a skill, a draft, a question, or a task before any action is taken.
- Wake word filtering. Background noise and TV audio are filtered out so CODEC doesn't act on random sounds.
- Local-first by design. You c- Local-fi runs - Local-first at- Local-first by design. Yola- Local-first by design. You c- Locchine.
- You pick the LLM. The guardrails of whatever model you connect apply on top of CODEC own safety rules.

What we are adding in v2: command preview mode, allowlist and blocklist for commands, confirmation prompts for system-level changes, and a full audit log.

## Architecture

codec.py           → Main: keyboard listener, wake word, dispatch
codec_watcher.py   → Draft agent: reads screen, writes + pastes replies
whisper_server.py  → STT server (optional)
~/.codec/skills/   → Plugin folder
~/.codec/config.json → Your config


## Keyboard Shortcuts

| Key | Action |
|---|---|
| F13 | Toggle ON/OFF |
| F18 | Hold to record, release to send |
| F16 | Text input dialog |
| ** | Screenshot + ask |
| ++ | Load document |
| "Hey Q" | Wake word |

## License

MIT License — use it however you want.

## Credits

Built by [AVA Digital LLC](https://avadigital.ai) · [opencodec.org](https://opencodec.org)

Powered by: [Ollama](https://ollama.com) · [Kokoro TTS](https://huggingface.co/hexgrad/Kokoro-82M) · [Whisper](https://github.com/openai/whisper) · [MLX](https://github.com/ml-explore/mlx)
