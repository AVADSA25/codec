"""CODEC Configuration — loads ~/.codec/config.json and exposes all constants"""
import os, json
from pynput import keyboard

CONFIG_PATH = os.path.expanduser("~/.codec/config.json")
DRY_RUN = False


def load_config():
    """Load config from ~/.codec/config.json, return dict"""
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                cfg = json.load(f)
        except Exception as e:
            print(f"[CODEC] Warning: failed to parse {CONFIG_PATH}: {e}")
    return cfg


# Load on import
cfg = load_config()

# Identity
AGENT_NAME        = cfg.get('agent_name', 'C')

# LLM
QWEN_BASE_URL     = cfg.get("llm_base_url", "http://localhost:8081/v1")
QWEN_MODEL        = cfg.get("llm_model", "mlx-community/Qwen3.5-35B-A3B-4bit")
LLM_API_KEY       = cfg.get("llm_api_key", "")
LLM_KWARGS        = cfg.get("llm_kwargs", {})
LLM_PROVIDER      = cfg.get("llm_provider", "mlx")

# Vision
QWEN_VISION_URL   = cfg.get("vision_base_url", "http://localhost:8082/v1")
QWEN_VISION_MODEL = cfg.get("vision_model", "mlx-community/Qwen2.5-VL-7B-Instruct-4bit")

# TTS
TTS_ENGINE        = cfg.get("tts_engine", "kokoro")
KOKORO_URL        = cfg.get("tts_url", "http://localhost:8085/v1/audio/speech")
KOKORO_MODEL      = cfg.get("tts_model", "mlx-community/Kokoro-82M-bf16")
TTS_VOICE         = cfg.get("tts_voice", "am_adam")

# STT
STT_ENGINE        = cfg.get("stt_engine", "whisper_http")
WHISPER_URL       = cfg.get("stt_url", "http://localhost:8084/v1/audio/transcriptions")

# Paths
DB_PATH           = os.path.expanduser("~/.q_memory.db")
Q_TERMINAL_TITLE  = "Q -- CODEC Session"
_CODEC_TMP = os.path.expanduser("~/.codec")
TASK_QUEUE_FILE   = os.path.join(_CODEC_TMP, "task_queue.txt")
DRAFT_TASK_FILE   = os.path.join(_CODEC_TMP, "draft_task.json")
SESSION_ALIVE     = os.path.join(_CODEC_TMP, "session_alive")
SKILLS_DIR        = os.path.expanduser("~/.codec/skills")
AUDIT_LOG         = os.path.expanduser("~/.codec/audit.log")

# Features
STREAMING         = cfg.get("streaming", True)
WAKE_WORD         = cfg.get("wake_word_enabled", True)
WAKE_PHRASES      = cfg.get("wake_phrases", ['hey', 'aq', 'eq', 'iq', 'okay q', 'a q', 'hey c', 'hey cueue'])
WAKE_ENERGY       = cfg.get("wake_energy", 200)
WAKE_CHUNK_SEC    = cfg.get("wake_chunk_sec", 3.0)
REQUIRE_CONFIRM   = cfg.get("require_confirmation", True)

# Dashboard auth — set in ~/.codec/config.json as "dashboard_token": "your-secret-token"
# When empty/missing, dashboard runs without auth (local use)
DASHBOARD_TOKEN   = cfg.get("dashboard_token", "")

# Biometric (Touch ID) auth — requires compiled Swift binary in codec_auth/
# Set "auth_enabled": true in config.json to activate
AUTH_ENABLED      = cfg.get("auth_enabled", False)
AUTH_SESSION_HOURS = cfg.get("auth_session_hours", 24)
# PIN code auth — alternative to Touch ID, stored as SHA-256 hash
AUTH_PIN_HASH     = cfg.get("auth_pin_hash", "")  # SHA-256 of the PIN

# MCP tool exposure — opt-in by default (new tools blocked unless explicitly allowed)
# Set "mcp_default_allow": true in config.json to revert to opt-out behaviour
MCP_DEFAULT_ALLOW = cfg.get("mcp_default_allow", False)
# Explicit allowlist of skill names exposed via MCP (used when mcp_default_allow is false)
MCP_ALLOWED_TOOLS = cfg.get("mcp_allowed_tools", [])

# Safety
DANGEROUS_PATTERNS = [
    "rm -rf", "rm -r /", "rm -rf /", "rm -rf ~", "rm -rf /*",
    "rmdir", "sudo rm", "mkfs", "dd if=",
    "shutdown", "reboot", "halt", "killall", "pkill",
    "sudo", "chmod 777", "chmod -R 777 /", "chown", "chown -R",
    "> /dev/", "echo > /dev/sda", "> /dev/sda", "mv / /dev/null",
    ":(){ :|:& };:", ":(){:|:&};:", "xattr -cr /",
    "curl | bash", "wget | bash", "curl | sh", "wget | sh",
    "| bash", "| sh",
    "defaults delete", "diskutil erase",
    "networksetup", "networksetup -setv6",
    "launchctl unload", "csrutil disable", "nvram", "bless",
    "scutil --set", "pmset",
    "osascript -e \'tell application \"System Events\"",
    "init 0", "kill -9 1", "format", "fdisk",
]


def is_dangerous(cmd):
    """Check if a command matches any dangerous pattern.
    Uses simple substring matching (case-insensitive) — many patterns contain
    special characters (e.g. ':(){ :|:& };:') that break regex word boundaries.
    """
    cmd_lower = cmd.lower()
    return any(p.lower() in cmd_lower for p in DANGEROUS_PATTERNS)


# Draft / screen detection keywords
DRAFT_KEYWORDS = [
    "draft", "reply", "rephrase", "rewrite", "fix my", "say that", "respond",
    "write a", "write an", "compose", "tell them", "tell him", "tell her",
    "say i", "say we", "message saying", "email saying", "correct my",
    "fix this", "improve this", "polish this", "type in", "type this",
    "please say", "please write", "please type", "write reply",
    "post saying", "comment saying", "tweet saying", "and say", "to say"
]
SCREEN_KEYWORDS = [
    "look at my screen", "look at the screen", "what's on my screen",
    "whats on my screen", "read my screen", "see my screen", "screen",
    "what am i looking at", "what do you see", "look at this"
]


def is_draft(t):
    return any(k in t.lower() for k in DRAFT_KEYWORDS)


def needs_screen(t):
    return any(k in t.lower() for k in SCREEN_KEYWORDS)


# Key resolution
def _resolve_key(name):
    name = name.lower().strip()
    if name.startswith('f') and name[1:].isdigit():
        return getattr(keyboard.Key, name, None)
    if len(name) == 1:
        return name
    return getattr(keyboard.Key, name, None)


KEY_TOGGLE = _resolve_key(cfg.get("key_toggle", "f13"))
KEY_VOICE  = _resolve_key(cfg.get("key_voice", "f18"))
KEY_TEXT   = _resolve_key(cfg.get("key_text", "f16"))


def clean_transcript(text):
    """Post-process Whisper transcription — strip hallucinations, stutters, misheard words."""
    if not text:
        return text
    import re

    # 1. Strip common Whisper hallucinations (exact match)
    hallucinations = [
        "thank you for watching", "thanks for watching", "subscribe to my channel",
        "please subscribe", "like and subscribe", "thank you for listening",
        "thanks for listening", "see you next time", "bye bye",
        "the end", "music playing", "(music)", "[music]",
        "you", "thank you.", "thanks.", "bye.", "okay.",
        "(silence)", "[silence]", "...",
    ]
    if text.lower().strip() in hallucinations:
        return ""

    # 2. Remove leading filler words
    text = re.sub(r'^(um|uh|erm|hmm|ah|oh|like|so|well|okay so|right so|you know)\s+',
                  '', text, flags=re.IGNORECASE)

    # 3. Remove repeated words (Whisper stutter)
    text = re.sub(r'\b(\w+)(\s+\1\b)+', r'\1', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(\w+\s+\w+)(\s+\1\b)+', r'\1', text, flags=re.IGNORECASE)

    # 4. Fix common misheard words
    replacements = {
        "kodak": "CODEC", "kodek": "CODEC", "co deck": "CODEC",
        "kodex": "CODEC", "codex": "CODEC", "codec": "CODEC",
        "hey cue": "Hey CODEC", "hey queue": "Hey CODEC",
        "hey que": "Hey CODEC", "hey cu": "Hey CODEC",
    }
    for wrong, right in replacements.items():
        text = re.sub(re.escape(wrong), right, text, flags=re.IGNORECASE)

    # 5. Capitalize first letter
    if text and text[0].islower():
        text = text[0].upper() + text[1:]

    # 6. Ensure ends with punctuation
    text = text.rstrip()
    if text and text[-1] not in '.!?':
        text += '.'

    return text.strip()
