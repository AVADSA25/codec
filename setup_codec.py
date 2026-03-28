#!/usr/bin/env python3
"""
CODEC Setup Wizard — Interactive Configuration
Run: python3 setup_codec.py
"""
import os, json, sys, subprocess, time, shutil

# ── COLORS ────────────────────────────────────────────────────────────────────
O = "\033[38;2;232;113;26m"   # Orange
G = "\033[38;2;0;200;100m"    # Green
R = "\033[38;2;255;60;60m"    # Red
W = "\033[38;2;200;200;200m"  # White
D = "\033[38;2;100;100;100m"  # Dim
B = "\033[1m"                 # Bold
X = "\033[0m"                 # Reset

CONFIG_DIR = os.path.expanduser("~/.codec")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
SKILLS_DIR = os.path.join(CONFIG_DIR, "skills")

def clear():
    os.system("clear" if os.name != "nt" else "cls")

def banner():
    print(f"""
{O}
   ██████  ██████  ██████  ███████  ██████
  ██      ██    ██ ██   ██ ██      ██
  ██      ██    ██ ██   ██ █████   ██
  ██      ██    ██ ██   ██ ██      ██
   ██████  ██████  ██████  ███████  ██████

  {W}opencodec.org  ·  AVA Digital LLC{O}
  {D}─────────────────────────────────────────{R}
""")

def ask(prompt, options=None, default=None):
    """Ask user a question with arrow-key selection or numbered fallback"""
    print(f"\n{O}{'─'*50}{X}")
    print(f"{W}{prompt}{X}")
    if options:
        try:
            from simple_term_menu import TerminalMenu
            default_idx = options.index(default) if default in options else 0
            menu = TerminalMenu(
                options,
                cursor_index=default_idx,
                menu_cursor="  ► ",
                menu_cursor_style=("fg_yellow", "bold"),
                menu_highlight_style=("fg_yellow", "bold"),
            )
            idx = menu.show()
            if idx is None:
                return default or options[0]
            return options[idx]
        except ImportError:
            for i, opt in enumerate(options, 1):
                marker = f"{G}►{X}" if default and opt == default else " "
                print(f"  {marker} {O}{i}{X}  {opt}")
            while True:
                d_hint = f" [{options.index(default)+1}]" if default else ""
                choice = input(f"\n{O}  >{X} Choose{d_hint}: ").strip()
                if not choice and default:
                    return default
                try:
                    idx = int(choice) - 1
                    if 0 <= idx < len(options):
                        return options[idx]
                except ValueError:
                    pass
                print(f"  {R}Invalid choice. Try again.{X}")
    else:
        d_hint = f" [{default}]" if default else ""
        val = input(f"\n{O}  >{X}{d_hint}: ").strip()
        return val if val else default

def ask_yn(prompt, default=True):
    """Yes/No question"""
    d = "Y/n" if default else "y/N"
    val = input(f"\n{W}  {prompt} {D}({d}){X}: ").strip().lower()
    if not val:
        return default
    return val in ['y', 'yes']

def ask_text(prompt, default=""):
    """Free text input"""
    d_hint = f" {D}[{default}]{X}" if default else ""
    val = input(f"\n{W}  {prompt}{d_hint}: ").strip()
    return val if val else default

def section(title, step, total):
    """Print section header"""
    print(f"\n{O}  ┌─ Step {step}/{total}: {title}")
    print(f"  └{'─'*45}{X}")

def check_command(cmd):
    """Check if a command exists"""
    return shutil.which(cmd) is not None

def check_port(port):
    """Check if a port is in use"""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        s.connect(("localhost", port))
        s.close()
        return True
    except:
        return False

# ══════════════════════════════════════════════════════════════════════════════
def main():
    clear()
    banner()
    print(f"{W}  Welcome to the CODEC setup wizard.{X}")
    print(f"{D}  This will configure CODEC on your machine.{X}")
    print(f"{D}  Press Enter to accept defaults shown in brackets.{X}")
    input(f"\n{O}  Press Enter to begin...{X}")

    config = {}
    total_steps = 8

    # ── STEP 1: LLM ──────────────────────────────────────────────────────────
    clear()
    banner()
    section("Language Model (LLM)", 1, total_steps)
    print(f"\n{W}  CODEC needs an LLM to think. Choose your provider:{X}")

    llm_choice = ask("Select your LLM provider:", [
        "Local — Ollama (recommended, free)",
        "Local — LM Studio",
        "Local — MLX Server (Apple Silicon)",
        "Cloud — OpenAI API",
        "Cloud — Anthropic API",
        "Cloud — Google Gemini API",
        "Custom — I have my own OpenAI-compatible server"
    ], default="Local — Ollama (recommended, free)")

    if "Ollama" in llm_choice:
        config["llm_provider"] = "ollama"
        config["llm_base_url"] = "http://localhost:11434/v1"
        print(f"\n{W}  Which Ollama model?{X}")
        model = ask_text("Model name", "llama3.2:8b")
        config["llm_model"] = model
        config["llm_kwargs"] = {}
        if not check_command("ollama"):
            print(f"\n{R}  ⚠ Ollama not found. Install it: https://ollama.com{X}")
        else:
            print(f"\n{G}  ✓ Ollama detected{X}")

    elif "LM Studio" in llm_choice:
        config["llm_provider"] = "lmstudio"
        config["llm_base_url"] = "http://localhost:1234/v1"
        config["llm_model"] = ask_text("Model name", "default")
        config["llm_kwargs"] = {}

    elif "MLX" in llm_choice:
        config["llm_provider"] = "mlx"
        port = ask_text("MLX server port", "8081")
        config["llm_base_url"] = f"http://localhost:{port}/v1"
        config["llm_model"] = ask_text("Model name", "mlx-community/Qwen3.5-35B-A3B-4bit")
        config["llm_kwargs"] = {"chat_template_kwargs": {"enable_thinking": False}}
        if check_port(int(port)):
            print(f"\n{G}  ✓ MLX server detected on port {port}{X}")
        else:
            print(f"\n{R}  ⚠ Nothing running on port {port}. Start your MLX server first.{X}")

    elif "OpenAI" in llm_choice:
        config["llm_provider"] = "openai"
        config["llm_base_url"] = "https://api.openai.com/v1"
        config["llm_model"] = ask_text("Model", "gpt-4o")
        api_key = ask_text("OpenAI API key")
        config["llm_api_key"] = api_key
        config["llm_kwargs"] = {}

    elif "Anthropic" in llm_choice:
        config["llm_provider"] = "anthropic"
        config["llm_base_url"] = "https://api.anthropic.com/v1"
        config["llm_model"] = ask_text("Model", "claude-sonnet-4-20250514")
        api_key = ask_text("Anthropic API key")
        config["llm_api_key"] = api_key
        config["llm_kwargs"] = {}

    elif "Gemini" in llm_choice:
        config["llm_provider"] = "gemini"
        config["llm_base_url"] = "https://generativelanguage.googleapis.com/v1beta/openai"
        gemini_model = ask("Select Gemini model:", [
            "gemini-2.5-flash — Fast, free tier available",
            "gemini-2.5-flash-lite — Cheapest, high volume",
            "gemini-3.1-pro-preview — Most powerful, paid",
            "gemini-3-flash-preview — Strong reasoning, paid",
        ], default="gemini-2.5-flash — Fast, free tier available")
        config["llm_model"] = gemini_model.split(" — ")[0]
        api_key = ask_text("Gemini API key")
        config["llm_api_key"] = api_key
        config["llm_kwargs"] = {}

    elif "Custom" in llm_choice:
        config["llm_provider"] = "custom"
        config["llm_base_url"] = ask_text("Server URL (OpenAI-compatible)", "http://localhost:8080/v1")
        config["llm_model"] = ask_text("Model name")
        config["llm_kwargs"] = {}

    print(f"\n{G}  ✓ LLM configured: {config['llm_provider']} / {config['llm_model']}{X}")

    # ── STEP 2: TTS ──────────────────────────────────────────────────────────
    clear()
    banner()
    section("Text-to-Speech (Voice Output)", 2, total_steps)

    tts_choice = ask("Select your TTS engine:", [
        "Kokoro 82M — Local, fast, consistent (recommended)",
        "macOS Say — Built-in, no install needed",
        "Disable — No voice output"
    ], default="Kokoro 82M — Local, fast, consistent (recommended)")

    if "Kokoro" in tts_choice:
        config["tts_engine"] = "kokoro"
        port = ask_text("Kokoro server port", "8085")
        config["tts_url"] = f"http://localhost:{port}/v1/audio/speech"
        config["tts_model"] = "mlx-community/Kokoro-82M-bf16"

        voices = {
            "am_adam": "Adam (American male, warm)",
            "af_nicole": "Nicole (American female, clear)",
            "af_heart": "Heart (American female, expressive)",
            "bf_emma": "Emma (British female, crisp)",
            "bm_george": "George (British male, authoritative)",
            "am_michael": "Michael (American male, professional)",
        }
        print(f"\n{W}  Choose a voice:{X}")
        voice_list = list(voices.keys())
        voice_labels = [f"{k} — {v}" for k, v in voices.items()]
        voice_choice = ask("Select voice:", voice_labels, default=voice_labels[0])
        config["tts_voice"] = voice_list[voice_labels.index(voice_choice)]

        if not check_port(int(port)):
            print(f"\n{R}  ⚠ Kokoro not running on port {port}.{X}")
            print(f"{D}    Install: pip install mlx-audio misaki num2words phonemizer-fork spacy{X}")
            print(f"{D}    Then: python3 -m spacy download en_core_web_sm{X}")
            print(f"{D}    Start: python3 -m mlx_audio.server --port {port}{X}")
        else:
            print(f"\n{G}  ✓ Kokoro detected on port {port}{X}")

    elif "macOS" in tts_choice:
        config["tts_engine"] = "macos_say"
        config["tts_url"] = ""
        config["tts_model"] = ""
        config["tts_voice"] = ask_text("macOS voice name", "Alex")

    else:
        config["tts_engine"] = "disabled"
        config["tts_url"] = ""
        config["tts_model"] = ""
        config["tts_voice"] = ""

    print(f"\n{G}  ✓ TTS configured: {config['tts_engine']}{X}")

    # ── STEP 3: STT ──────────────────────────────────────────────────────────
    clear()
    banner()
    section("Speech-to-Text (Voice Input)", 3, total_steps)

    stt_choice = ask("Select your STT engine:", [
        "Whisper Local — mlx-whisper server (recommended)",
        "Whisper In-Process — loads model in CODEC (uses more RAM)",
        "Disable — Text-only mode, no voice input"
    ], default="Whisper Local — mlx-whisper server (recommended)")

    if "Local" in stt_choice:
        config["stt_engine"] = "whisper_http"
        port = ask_text("Whisper server port", "8084")
        config["stt_url"] = f"http://localhost:{port}/v1/audio/transcriptions"
        config["stt_model"] = ask_text("Whisper model", "mlx-community/whisper-large-v3-turbo")
        if check_port(int(port)):
            print(f"\n{G}  ✓ Whisper server detected on port {port}{X}")
        else:
            print(f"\n{R}  ⚠ Whisper not running on port {port}.{X}")

    elif "In-Process" in stt_choice:
        config["stt_engine"] = "whisper_local"
        config["stt_url"] = ""
        config["stt_model"] = ask_text("Whisper model size", "base")

    else:
        config["stt_engine"] = "disabled"
        config["stt_url"] = ""
        config["stt_model"] = ""

    print(f"\n{G}  ✓ STT configured: {config['stt_engine']}{X}")

    # ── STEP 4: KEYBOARD SHORTCUTS ────────────────────────────────────────────
    clear()
    banner()
    section("Keyboard Shortcuts", 4, total_steps)

    print(f"\n{W}  Default keyboard shortcuts:{X}")
    print(f"  {O}F13{X}  →  Toggle CODEC ON/OFF")
    print(f"  {O}F18{X}  →  Hold to record voice, release to send")
    print(f"  {O}F16{X}  →  Text input dialog")
    print(f"  {O}**{X}   →  Double-tap star for screenshot + ask")
    print(f"  {O}++{X}   →  Double-tap plus for document analysis")

    if ask_yn("Use default shortcuts?", True):
        config["key_toggle"] = "f13"
        config["key_voice"] = "f18"
        config["key_text"] = "f16"
        config["key_screenshot"] = "*"
        config["key_document"] = "+"
    else:
        print(f"\n{D}  Enter key names as pynput recognizes them (f1-f20, or single characters){X}")
        config["key_toggle"] = ask_text("Toggle ON/OFF key", "f13")
        config["key_voice"] = ask_text("Voice record key", "f18")
        config["key_text"] = ask_text("Text input key", "f16")
        config["key_screenshot"] = ask_text("Screenshot key", "*")
        config["key_document"] = ask_text("Document key", "+")

    print(f"\n{G}  ✓ Shortcuts configured{X}")

    # ── STEP 5: WAKE WORD ─────────────────────────────────────────────────────
    clear()
    banner()
    section("Wake Word", 5, total_steps)

    print(f"\n{W}  Wake word lets you activate CODEC hands-free.{X}")
    print(f"{D}  Say your wake phrase, then speak your command.{X}")
    print(f"{D}  Requires: sounddevice, soundfile, numpy (pip install){X}")

    config["wake_word_enabled"] = ask_yn("Enable wake word?", True)

    if config["wake_word_enabled"]:
        print(f"\n{W}  Choose wake phrases (what Whisper might hear):{X}")
        print(f"{D}  Default: 'hey mike' — Whisper may transcribe as 'hey', 'aq', 'eq', etc.{X}")
        default_phrases = "hey,aq,eq,iq,okay q,a q,hey mike,hey mikeueue"
        phrases = ask_text("Wake phrases (comma-separated)", default_phrases)
        config["wake_phrases"] = [p.strip() for p in phrases.split(",")]
        config["wake_energy"] = 200
        config["wake_chunk_sec"] = 3.0
    else:
        config["wake_phrases"] = []
        config["wake_energy"] = 200
        config["wake_chunk_sec"] = 3.0

    print(f"\n{G}  ✓ Wake word: {'ON' if config['wake_word_enabled'] else 'OFF'}{X}")

    # ── STEP 6: FEATURES ──────────────────────────────────────────────────────
    clear()
    banner()
    section("Features", 6, total_steps)

    config["streaming"] = ask_yn("Enable streaming responses? (see words as they generate)", True)

    print(f"\n{W}  Draft keywords trigger auto-reply mode.{X}")
    print(f"{D}  When you say these words, CODEC drafts a message and pastes it.{X}")
    default_drafts = "draft,reply,rephrase,rewrite,respond,compose,tell them,tell him,tell her"
    if ask_yn("Use default draft keywords?", True):
        config["draft_keywords"] = default_drafts.split(",")
    else:
        custom = ask_text("Draft keywords (comma-separated)", default_drafts)
        config["draft_keywords"] = [k.strip() for k in custom.split(",")]

    print(f"\n{G}  ✓ Streaming: {'ON' if config['streaming'] else 'OFF'}{X}")
    print(f"{G}  ✓ Draft keywords: {len(config['draft_keywords'])} configured{X}")

    # ── STEP 7: SKILLS ────────────────────────────────────────────────────────
    clear()
    banner()
    section("Skills", 7, total_steps)

    all_skills = {
        "calculator": "Quick math (25 times 47)",
        "weather": "Current weather by city",
        "time_date": "Current time and date",
        "system_info": "CPU, disk, memory stats",
        "web_search": "DuckDuckGo instant answers",
        "translate": "French ↔ English translation",
        "notes": "Save/read notes via Apple Notes",
        "timer": "Set timers with voice alerts",
        "volume": "Volume up/down/mute by voice",
        "reminders": "Add to Apple Reminders",
        "music": "Control Spotify/Apple Music",
        "clipboard": "Clipboard history",
        "app_switch": "Switch apps by name",
        "create_skill": "Create new skills with natural language",
        "lucy": "Delegate tasks to Lucy VPA (requires n8n)",
        "google_calendar": "Check Google Calendar events",
        "google_gmail": "Check Gmail inbox and search emails",
        "google_drive": "Search Google Drive files",
        "google_docs": "Create and read Google Docs",
        "google_sheets": "Read and write Google Sheets",
        "google_slides": "Create Google Slides presentations",
        "google_tasks": "Manage Google Tasks",
        "google_keep": "Create Google Keep notes",
    }

    print(f"\n{W}  Available skills:{X}")
    for name, desc in all_skills.items():
        print(f"  {O}•{X} {name:15s} {D}{desc}{X}")

    if ask_yn("Install all skills?", True):
        config["skills"] = list(all_skills.keys())
    else:
        config["skills"] = []
        for name, desc in all_skills.items():
            if ask_yn(f"  Enable {name} ({desc})?", True):
                config["skills"].append(name)

    print(f"\n{G}  ✓ {len(config['skills'])} skills selected{X}")

    # ── STEP 8: PHONE DASHBOARD ────────────────────────────────────────────────
    clear()
    banner()
    section("Phone Dashboard", 8, total_steps)
    print(f"\n{W}  CODEC includes a phone dashboard (PWA) that lets you{X}")
    print(f"{W}  control your Mac from your phone — text, voice, files.{X}")
    print(f"\n{D}  It runs as a lightweight web server on port 8090.{X}")
    print(f"{D}  Access locally at http://localhost:8090{X}")
    print(f"{D}  Or remotely via Cloudflare Tunnel for secure access anywhere.{X}")
    config["dashboard_enabled"] = ask_yn("\nEnable phone dashboard?", True)
    if config["dashboard_enabled"]:
        config["dashboard_port"] = int(ask_text("Dashboard port", "8090"))
        print(f"\n{W}  Remote access options:{X}")
        print(f"  {O}1.{X} Local only (http://localhost:{config['dashboard_port']})")
        print(f"  {O}2.{X} Cloudflare Tunnel (recommended for remote access)")
        print(f"  {O}3.{X} I'll set up remote access myself")
        remote = ask("Remote access:", [
            "Local only",
            "Cloudflare Tunnel (I'll set it up later)",
            "I'll handle it myself"
        ], default="Local only")
        if "Cloudflare" in remote:
            print(f"\n{D}  To set up Cloudflare Tunnel:{X}")
            print(f"  {W}1.{X} Install: brew install cloudflared")
            print(f"  {W}2.{X} Login: cloudflared tunnel login")
            print(f"  {W}3.{X} Create tunnel: cloudflared tunnel create my-codec")
            print(f"  {W}4.{X} Route: cloudflared tunnel route dns my-codec codec.yourdomain.com")
            print(f"  {W}5.{X} Add to config.yml: hostname: codec.yourdomain.com → http://localhost:{config['dashboard_port']}")
            print(f"  {W}6.{X} Add Zero Trust email auth in Cloudflare dashboard")
        print(f"\n{W}  To start the dashboard:{X}")
        print(f"  {D}python3 codec_dashboard.py{X}")
        print(f"  {D}Or: pm2 start python3 -- -u codec_dashboard.py --name codec-dashboard{X}")
        print(f"\n{W}  On your phone:{X}")
        print(f"  {D}Open the URL in Chrome → Add to Home Screen → PWA installed{X}")
    print(f"\n{G}  ✓ Dashboard: {'ON' if config['dashboard_enabled'] else 'OFF'}{X}")
    # ── SAVE CONFIG ───────────────────────────────────────────────────────────
    clear()
    banner()
    print(f"\n{O}  ┌─ Configuration Summary")
    print(f"  └{'─'*45}{X}")
    print(f"""
  {W}LLM:{X}        {config['llm_provider']} / {config['llm_model']}
  {W}TTS:{X}        {config.get('tts_engine','disabled')} / {config.get('tts_voice','')}
  {W}STT:{X}        {config.get('stt_engine','disabled')}
  {W}Shortcuts:{X}  {config['key_toggle']}=toggle  {config['key_voice']}=voice  {config['key_text']}=text
  {W}Wake word:{X}  {'ON' if config['wake_word_enabled'] else 'OFF'}
  {W}Streaming:{X}  {'ON' if config['streaming'] else 'OFF'}
  {W}Skills:{X}     {len(config['skills'])} enabled
  {W}Config:{X}     {CONFIG_PATH}
""")

    if not ask_yn("Save this configuration?", True):
        print(f"\n{R}  Setup cancelled.{X}")
        sys.exit(0)

    # Create directories
    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.makedirs(SKILLS_DIR, exist_ok=True)

    # Save config
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)
    print(f"\n{G}  ✓ Config saved to {CONFIG_PATH}{X}")

    # Copy skills
    script_dir = os.path.dirname(os.path.abspath(__file__))
    skills_src = os.path.join(script_dir, "skills")
    if os.path.isdir(skills_src):
        for skill_name in config["skills"]:
            src = os.path.join(skills_src, f"{skill_name}.py")
            dst = os.path.join(SKILLS_DIR, f"{skill_name}.py")
            if os.path.exists(src):
                shutil.copy2(src, dst)
        # Always copy template
        template_src = os.path.join(skills_src, "_template.py")
        if os.path.exists(template_src):
            shutil.copy2(template_src, os.path.join(SKILLS_DIR, "_template.py"))
        print(f"{G}  ✓ {len(config['skills'])} skills installed to {SKILLS_DIR}{X}")
    else:
        print(f"{D}  Skills directory not found in repo — copy manually to {SKILLS_DIR}{X}")

    # ── DEPENDENCY CHECK ──────────────────────────────────────────────────────
    print(f"\n{O}  ┌─ Dependency Check")
    print(f"  └{'─'*45}{X}\n")

    deps = [
        ("Python 3.10+", sys.version_info >= (3, 10)),
        ("pynput", _check_import("pynput")),
        ("sounddevice", _check_import("sounddevice")),
        ("soundfile", _check_import("soundfile")),
        ("numpy", _check_import("numpy")),
        ("requests", _check_import("requests")),
        ("sox (brew)", check_command("sox")),
    ]

    all_ok = True
    missing = []
    for name, ok in deps:
        if ok:
            print(f"  {G}✓{X} {name}")
        else:
            print(f"  {R}✗{X} {name}")
            missing.append(name)
            all_ok = False

    if missing:
        print(f"\n{R}  Missing dependencies:{X}")
        pip_deps = [d for d in missing if d not in ["Python 3.10+", "sox (brew)"]]
        if pip_deps:
            print(f"  {W}pip install {' '.join(pip_deps)}{X}")
        if "sox (brew)" in missing:
            print(f"  {W}brew install sox{X}")
    else:
        print(f"\n{G}  All dependencies installed!{X}")

    # ── FINAL ─────────────────────────────────────────────────────────────────
    print(f"""
{O}  ╔══════════════════════════════════════════════════╗
  ║                                                  ║
  ║   {G}CODEC is ready!{O}                                ║
  ║                                                  ║
  ║   {W}Start CODEC:{O}                                   ║
  ║   {D}python3 codec.py{O}                               ║
  ║                                                  ║
  ║   {W}Or with PM2 (auto-restart):{O}                    ║
  ║   {D}pm2 start python3 -- -u codec.py{O}               ║
  ║                                                  ║
  ║   {W}Press F13 to activate, then:{O}                   ║
  ║   {D}F18 = voice  |  F16 = text  |  Hey Mike{O}          ║
  ║                                                  ║
  ╚══════════════════════════════════════════════════╝{X}
""")

def _check_import(module):
    try:
        __import__(module)
        return True
    except ImportError:
        return False

if __name__ == "__main__":
    main()
