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
        except Exception:
            pass
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
TASK_QUEUE_FILE   = "/tmp/q_task_queue.txt"
DRAFT_TASK_FILE   = "/tmp/q_draft_task.json"
SESSION_ALIVE     = "/tmp/q_session_alive"
SKILLS_DIR        = os.path.expanduser("~/.codec/skills")
AUDIT_LOG         = os.path.expanduser("~/.codec/audit.log")

# Features
STREAMING         = cfg.get("streaming", True)
WAKE_WORD         = cfg.get("wake_word_enabled", True)
WAKE_PHRASES      = cfg.get("wake_phrases", ['hey', 'aq', 'eq', 'iq', 'okay q', 'a q', 'hey c', 'hey cueue'])
WAKE_ENERGY       = cfg.get("wake_energy", 200)
WAKE_CHUNK_SEC    = cfg.get("wake_chunk_sec", 3.0)
DRAFT_KEYWORDS_CFG = cfg.get("draft_keywords", [])
REQUIRE_CONFIRM   = cfg.get("require_confirmation", True)

# Safety
DANGEROUS_PATTERNS = [
    "rm -rf", "rm -r /", "rm -rf /", "rm -rf ~", "rm -rf /*",
    "rmdir", "sudo rm", "mkfs", "dd if=",
    "shutdown", "reboot", "halt", "killall", "pkill",
    "sudo", "chmod 777", "chmod -R 777 /", "chown", "chown -R",
    "> /dev/", "echo > /dev/sda", "mv / /dev/null",
    ":(){ :|:& };:", "xattr -cr /",
    "curl | bash", "wget | bash", "curl | sh", "wget | sh",
    "defaults delete", "diskutil erase",
    "networksetup", "networksetup -setv6",
    "launchctl unload", "csrutil disable", "nvram", "bless",
    "scutil --set", "pmset",
    "osascript -e \'tell application \"System Events\"",
]


def is_dangerous(cmd):
    cmd_lower = cmd.lower()
    return any(p in cmd_lower for p in DANGEROUS_PATTERNS)


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
