<p align="center">
  <img src="https://i.imgur.com/RbrQ7Bt.png" alt="CODEC" width="280"/>
</p>

<h1 align="center">CODEC</h1>
<p align="center"><strong>Open Source AI Control for Your Computer</strong></p>
<p align="center">
  Voice-controlled, local-first AI agent that runs on your machine with any LLM.<br/>
  No cloud. No subscription. No data leaves your computer.
</p>
<p align="center">
  <a href="https://opencodec.org">opencodec.org</a> · 
  <a href="#quickstart">Quickstart</a> · 
  <a href="#features">Features</a> · 
  <a href="#skills">Skills</a> · 
  <a href="#configuration">Configuration</a>
</p>

---

## What is CODEC?

CODEC turns your computer into a voice-controlled AI workstation. Press a key or say **"Hey Q"** — CODEC listens, thinks (using any LLM you choose), and acts: opening apps, drafting messages, reading your screen, analyzing documents, controlling Spotify, setting timers, and anything else you can describe.

Think of it as a private, open-source alternative to Siri/Alexa that actually controls your computer — powered by the LLM of your choice running locally or via API.

**Built for macOS.** Linux support planned.

```
You: "Hey Q, open Safari and go to GitHub"
  → CODEC opens Safari, navigates to github.com

You: "Draft a reply saying thanks, I'll review it tonight"
  → CODEC reads your screen, writes a polished reply, pastes it into your chat

You: "What's on my screen?"
  → CODEC screenshots your display, describes what it sees

You: "Play the next song"
  → Spotify skips to the next track instantly
```

## Why CODEC?

| | CODEC | Claude Cowork | Siri |
|---|---|---|---|
| **Cost** | Free forever | $20/month | Free (limited) |
| **Privacy** | 100% local option | Data sent to cloud | Data sent to Apple |
| **LLM Choice** | Any (Ollama, OpenAI, Gemini, etc.) | Claude only | Apple only |
| **Voice Control** | Yes + wake word | Yes | Yes |
| **Screen Reading** | Yes (Vision AI) | Yes | No |
| **Draft & Paste** | Yes, into any app | Limited | No |
| **Skills/Plugins** | 13 built-in + custom | No | Limited |
| **Open Source** | Yes (MIT) | No | No |

## Features

- **Voice Commands** — Hold F18 to speak, release to send
- **Text Input** — F16 opens a text dialog
- **Wake Word** — Say "Hey Q" hands-free, no button press
- **Screen Reading** — Double-tap `**` to screenshot + ask about what you see
- **Document Analysis** — Double-tap `++` to load any file (PDF, image, text, JSON)
- **Draft & Paste** — Say "draft a reply" and CODEC writes + pastes directly into your chat app
- **Task Execution** — "Open Calculator", "Create a folder on my desktop" — Q-Agent runs bash/AppleScript
- **Command Chaining** — "Open Safari, go to weather.com, and tell me the temperature" — multi-step
- **Streaming Responses** — See words appear in real-time as the LLM generates
- **Persistent Memory** — Q remembers conversations across sessions
- **Correction Learning** — Say "No, I meant Gmail" and CODEC remembers for next time
- **13 Built-in Skills** — Calculator, weather, timer, Spotify, volume, notes, translate, and more
- **Custom Skills** — Drop a `.py` file in `~/.codec/skills/` to add any capability
- **Consistent Voice** — Kokoro 82M TTS with selectable voices (Adam, Nicole, Emma, George...)
- **F13 Toggle** — One key to turn CODEC on/off, auto-closes terminal sessions

## Quickstart

### 1. Clone the repo

```bash
git clone https://github.com/AVADSA25/codec.git
cd codec
```

### 2. Install dependencies

```bash
# Core
pip3 install pynput sounddevice soundfile numpy requests

# Audio recording
brew install sox

# TTS (optional but recommended)
pip3 install mlx-audio misaki num2words phonemizer-fork spacy
python3 -m spacy download en_core_web_sm

# STT (optional but recommended)  
pip3 install mlx-whisper
```

### 3. Start your services

**LLM** — pick one:
```bash
# Option A: Ollama (free, local)
ollama serve
ollama pull llama3.2:8b

# Option B: Any OpenAI-compatible server
# Just have your API key ready
```

**TTS** (voice output):
```bash
python3 -m mlx_audio.server --port 8085
```

**STT** (voice input):
```bash
python whisper_server.py
# Runs on port 8084
```

### 4. Run the setup wizard

```bash
python setup_codec.py
```

The wizard walks you through choosing your LLM, TTS voice, keyboard shortcuts, wake word, and skills. Takes 2 minutes.

### 5. Start CODEC

```bash
python codec.py
```

Or with PM2 for auto-restart:
```bash
pm2 start python3 --name codec -- -u codec.py
pm2 save
```

### 6. Use it

Press **F13** to activate, then:
- **F18** — hold to record voice, release to send
- **F16** — type a command
- **"Hey Q"** — wake word (hands-free)
- **`**`** — double-tap for screenshot + ask
- **`++`** — double-tap to load a document

## Skills

CODEC ships with 13 built-in skills. Skills execute instantly without opening a terminal window.

| Skill | Example Voice Command |
|---|---|
| Calculator | "Calculate 25 times 47" |
| Weather | "What's the weather in Tokyo" |
| Time & Date | "What time is it" |
| System Info | "How much disk space do I have" |
| Web Search | "Search for SpaceX latest launch" |
| Translate | "How do you say good morning in French" |
| Notes | "Take a note: call the bank tomorrow" |
| Timer | "Set a timer for 5 minutes" |
| Volume | "Volume to 50" / "Mute" |
| Reminders | "Remind me to review the code tonight" |
| Music | "Play Spotify" / "Next song" / "What's playing" |
| Clipboard | "Show clipboard history" |
| App Switch | "Switch to Chrome" / "Go to Finder" |

### Create Your Own Skill

Drop a `.py` file in `~/.codec/skills/`:

```python
"""My Custom Skill"""
SKILL_NAME = "my_skill"
SKILL_TRIGGERS = ["trigger phrase one", "trigger phrase two"]

def run(task, app="", ctx=""):
    # Your code here
    return "Response spoken back to you"
```

CODEC auto-loads it on next restart. See `skills/_template.py` for the full template.

## Configuration

The setup wizard saves config to `~/.codec/config.json`. You can also edit it directly:

```json
{
  "llm_provider": "ollama",
  "llm_base_url": "http://localhost:11434/v1",
  "llm_model": "llama3.2:8b",
  "tts_engine": "kokoro",
  "tts_url": "http://localhost:8085/v1/audio/speech",
  "tts_voice": "am_adam",
  "stt_engine": "whisper_http",
  "stt_url": "http://localhost:8084/v1/audio/transcriptions",
  "wake_word_enabled": true,
  "wake_phrases": ["hey", "aq", "eq", "hey q"],
  "streaming": true,
  "key_toggle": "f13",
  "key_voice": "f18",
  "key_text": "f16",
  "skills": ["calculator", "weather", "timer", "music", "notes", "translate", "volume", "web_search", "reminders", "clipboard", "app_switch", "system_info", "time_date"]
}
```

### Supported LLM Providers

| Provider | How to Connect |
|---|---|
| **Ollama** | `ollama serve` → auto-detected on port 11434 |
| **LM Studio** | Start server → auto-detected on port 1234 |
| **MLX Server** | `mlx_lm.server --port 8081` → configure port in wizard |
| **OpenAI** | Enter API key in wizard |
| **Anthropic** | Enter API key in wizard |
| **Google Gemini** | Enter API key in wizard |
| **Any OpenAI-compatible** | Enter URL + model name in wizard |

### Keyboard Shortcuts

Default shortcuts (customizable in wizard):

| Key | Action |
|---|---|
| F13 | Toggle CODEC ON/OFF |
| F18 | Hold to record voice, release to send |
| F16 | Open text input dialog |
| `**` | Double-tap: screenshot + ask about screen |
| `++` | Double-tap: load document for analysis |
| "Hey Q" | Wake word — hands-free activation |

## Architecture

CODEC is two files + skills:

```
codec.py           → Main process: keyboard listener, wake word, dispatch
codec_watcher.py   → Draft agent: reads screen, writes replies, pastes into apps
~/.codec/skills/   → Plugin folder: each .py file is a skill
~/.codec/config.json → Your configuration
```

**How it works:**

1. You speak or type a command
2. CODEC classifies it: skill? draft? task? question?
3. Skills execute instantly (no LLM needed for simple things)
4. Drafts → screenshot screen → LLM writes reply → pastes into your chat
5. Tasks → Q-Agent runs bash/AppleScript commands step by step
6. Questions → LLM answers in terminal + speaks via TTS

**Services CODEC connects to:**

| Service | Purpose | Default Port |
|---|---|---|
| LLM Server | Brain (thinking) | Varies |
| Kokoro 82M | Voice output (TTS) | 8085 |
| Whisper Server | Voice input (STT) | 8084 |
| Qwen Vision | Screen reading (optional) | 8082 |

## Roadmap

- [x] Voice commands + wake word
- [x] Screen reading via Vision AI
- [x] Document analysis (PDF, images, text)
- [x] Draft & paste into any app
- [x] 13 built-in skills
- [x] Persistent memory across sessions
- [x] Streaming responses
- [x] Setup wizard
- [ ] PWA phone dashboard (control CODEC from your phone)
- [ ] config.json integration (currently uses hardcoded values)
- [ ] Linux support
- [ ] Setup wizard visual improvements
- [ ] More skills (email, calendar, smart home)
- [ ] Vibe coding mode (AI builds apps for you)

## Contributing

CODEC is open source under the MIT License. Contributions welcome!

**Best ways to contribute:**
- Build new skills and submit a PR
- Test on different Mac models and report issues
- Improve documentation
- Add support for new LLM providers

## License

MIT License — use it however you want.

## Credits

Built by [AVA Digital LLC](https://avadigital.ai) · [opencodec.org](https://opencodec.org)

Powered by open source: [Ollama](https://ollama.com) · [Kokoro TTS](https://huggingface.co/hexgrad/Kokoro-82M) · [Whisper](https://github.com/openai/whisper) · [MLX](https://github.com/ml-explore/mlx)
