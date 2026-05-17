"""CODEC Configuration — loads ~/.codec/config.json and exposes all constants"""
import os, json

# pynput requires a display (X11 / AppKit / win32). On headless CI runners
# (Linux GitHub Actions, Docker) the import raises ImportError. Other modules
# import codec_config for is_dangerous_skill_code, DANGEROUS_PATTERNS, etc.
# without needing the keyboard subsystem — fail gracefully so those imports work.
try:
    from pynput import keyboard
except ImportError:
    keyboard = None

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


def _blank_config_field(key: str) -> None:
    """Atomically blank a single field in ~/.codec/config.json.

    Used by PR-2B Keychain migration: after a secret (llm_api_key,
    dashboard_token) is copied to Keychain, the on-disk config field is
    set to "" (kept as a key so the schema stays stable). Atomic
    tmp+rename so a crash mid-write doesn't leave a half-baked config.
    """
    try:
        current = load_config()
        current[key] = ""
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(current, f, indent=2)
        os.replace(tmp, CONFIG_PATH)
        try:
            os.chmod(CONFIG_PATH, 0o600)
        except Exception:
            pass
    except Exception as e:
        print(f"[CODEC] Warning: _blank_config_field({key!r}) failed: {e}")


# Load on import
cfg = load_config()

# Identity
AGENT_NAME        = cfg.get('agent_name', 'C')
ASSISTANT_NAME    = cfg.get('assistant_name', 'CODEC')
USER_NAME         = cfg.get('user_name', '')

# LLM
QWEN_BASE_URL     = cfg.get("llm_base_url", "http://localhost:8081/v1")
QWEN_MODEL        = cfg.get("llm_model", "mlx-community/Qwen3.5-35B-A3B-4bit")


# In-memory cache for the Keychain-backed secret helpers. PR-2B failure-mode
# handling: each `security find-generic-password` shellout is 50-100ms; that
# latency on every AuthMiddleware.dispatch is unacceptable. A 30s TTL is
# short enough that secret rotations propagate quickly but long enough that
# request-path overhead disappears. OAuth state is loaded once at provider
# init and doesn't go through this cache.
_SECRET_CACHE: dict[str, tuple[float, str]] = {}
_SECRET_CACHE_TTL = 30.0  # seconds


def _cached_secret(name: str, fetcher) -> str:
    """Return a fresh-enough secret, fetching via `fetcher` only when the
    cache entry is missing or older than _SECRET_CACHE_TTL."""
    import time as _time
    now = _time.time()
    entry = _SECRET_CACHE.get(name)
    if entry and (now - entry[0]) < _SECRET_CACHE_TTL:
        return entry[1]
    value = fetcher() or ""
    _SECRET_CACHE[name] = (now, value)
    return value


def _invalidate_secret_cache(name: str | None = None) -> None:
    """Wipe one cache entry, or the whole cache when name is None.
    Called by tests and by migration paths to force a re-read."""
    if name is None:
        _SECRET_CACHE.clear()
    else:
        _SECRET_CACHE.pop(name, None)


def _migrate_and_get(kc_key: str, cfg_key: str) -> str:
    """Read a secret, migrating plaintext → Keychain on first call.

    1. Read Keychain.
    2. If empty AND cfg has a plaintext value, migrate it (write Keychain,
       blank cfg on disk, invalidate the in-process cfg dict's field).
    3. Return the now-current value (Keychain or cfg fallback).

    Idempotent: the migrate_from_plaintext helper returns False
    quickly when there's nothing to migrate.
    """
    try:
        from codec_keychain import keychain_get, migrate_from_plaintext
    except Exception:
        return cfg.get(cfg_key, "")
    k = keychain_get(kc_key)
    if k:
        return k
    plaintext = cfg.get(cfg_key, "")
    if not plaintext:
        return ""
    # First-call migration.
    def _blank():
        _blank_config_field(cfg_key)
        cfg[cfg_key] = ""  # in-process dict; matches the on-disk write
    if migrate_from_plaintext(kc_key, plaintext, _blank):
        return keychain_get(kc_key) or plaintext
    # Migration failed (Keychain unavailable / locked) — return the
    # plaintext so the daemon keeps working. The audit event from
    # keychain_set already logged the failure.
    return plaintext


def get_llm_api_key() -> str:
    """Return the LLM provider API key. Prefers Keychain over plaintext
    config (closes audit D-15 partial — PR-2B).

    On first startup, the legacy `~/.codec/config.json:llm_api_key`
    plaintext is migrated to Keychain via `codec_keychain.migrate_from_plaintext`
    and the on-disk field is blanked. Subsequent reads come from Keychain.
    30s in-memory cache to avoid repeated `security` shellouts on hot paths.
    """
    return _cached_secret("llm_api_key", lambda: _migrate_and_get("llm_api_key", "llm_api_key"))


def get_dashboard_token() -> str:
    """Return the dashboard bearer token. Same migration story as
    `get_llm_api_key()`. Returns "" when no token is set."""
    return _cached_secret("dashboard_token", lambda: _migrate_and_get("dashboard_token", "dashboard_token"))


# Module-level constants kept for back-compat with callers that import them
# as `from codec_config import LLM_API_KEY`. Eager-evaluated at import time;
# the getter functions above are the canonical accessors for runtime
# Keychain-aware reads.
LLM_API_KEY       = get_llm_api_key()
LLM_KWARGS        = cfg.get("llm_kwargs", {})
LLM_PROVIDER      = cfg.get("llm_provider", "mlx")

# Vision (general — images, documents, screen reading)
QWEN_VISION_URL   = cfg.get("vision_base_url", "http://localhost:8082/v1")
QWEN_VISION_MODEL = cfg.get("vision_model", "mlx-community/Qwen2.5-VL-7B-Instruct-4bit")

# UI-TARS (UI-specialist — mouse control coordinate extraction)
UI_TARS_URL       = cfg.get("ui_tars_base_url", "http://localhost:8083/v1")
UI_TARS_MODEL     = cfg.get("ui_tars_model", "mlx-community/UI-TARS-1.5-7B-4bit")

# TTS
TTS_ENGINE        = cfg.get("tts_engine", "kokoro")
KOKORO_URL        = cfg.get("tts_url", "http://localhost:8085/v1/audio/speech")
KOKORO_MODEL      = cfg.get("tts_model", "mlx-community/Kokoro-82M-bf16")
TTS_VOICE         = cfg.get("tts_voice", "am_adam")

# STT
STT_ENGINE        = cfg.get("stt_engine", "whisper_http")
WHISPER_URL       = cfg.get("stt_url", "http://localhost:8084/v1/audio/transcriptions")

# Paths
# Migrate from legacy ~/.q_memory.db to ~/.codec/memory.db
_LEGACY_DB = os.path.expanduser("~/.q_memory.db")
_NEW_DB = os.path.expanduser("~/.codec/memory.db")
if os.path.exists(_LEGACY_DB) and not os.path.exists(_NEW_DB):
    os.makedirs(os.path.dirname(_NEW_DB), exist_ok=True)
    import shutil
    shutil.move(_LEGACY_DB, _NEW_DB)
DB_PATH = _NEW_DB
Q_TERMINAL_TITLE  = "CODEC Session"
_CODEC_TMP = os.path.expanduser("~/.codec")
TASK_QUEUE_FILE   = os.path.join(_CODEC_TMP, "task_queue.txt")
DRAFT_TASK_FILE   = os.path.join(_CODEC_TMP, "draft_task.json")
SESSION_ALIVE     = os.path.join(_CODEC_TMP, "session_alive")
# Skills load from the repo directly — single source of truth
_REPO_DIR         = os.path.dirname(os.path.abspath(__file__))
SKILLS_DIR        = cfg.get("skills_dir", os.path.join(_REPO_DIR, "skills"))
AUDIT_LOG         = os.path.expanduser("~/.codec/audit.log")

# Features
STREAMING         = cfg.get("streaming", True)
WAKE_WORD         = cfg.get("wake_word_enabled", True)
WAKE_PHRASES      = cfg.get("wake_phrases", ['hey codec', 'hey', 'okay codec', 'hey codex', 'hey coda', 'hey queue'])
_raw_wake_energy   = cfg.get("wake_energy", 200)
WAKE_ENERGY       = max(50, min(int(_raw_wake_energy), 1500))  # clamp 50-1500; >1500 silences mic
WAKE_CHUNK_SEC    = cfg.get("wake_chunk_sec", 3.0)
REQUIRE_CONFIRM   = cfg.get("require_confirmation", True)

# Dashboard binding — Closes audit finding D-7. Default is loopback-only
# (127.0.0.1) so LAN devices can't reach the dashboard with no auth. Set
# "dashboard_host" in ~/.codec/config.json to "0.0.0.0" to bind on all
# interfaces — but only when paired with dashboard_token OR auth_enabled
# (codec_dashboard._check_dashboard_start_safety refuses to start an
# unauthenticated, public-binding dashboard).
DASHBOARD_HOST    = cfg.get("dashboard_host", "127.0.0.1")
# Dashboard auth — set in ~/.codec/config.json as "dashboard_token": "your-secret-token"
# When empty/missing, dashboard runs without auth (local use).
# PR-2B closure D-15 partial: post-migration, the canonical source is
# macOS Keychain via codec_config.get_dashboard_token(). The module-level
# constant below is eager-evaluated at import for back-compat with
# `from codec_config import DASHBOARD_TOKEN` callers. Runtime auth
# checks should call get_dashboard_token() for the live value.
DASHBOARD_TOKEN   = get_dashboard_token()

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
# Explicit blocklist — skills here are NEVER exposed via MCP, even in opt-out mode.
# Use for skills that arbitrary-execute code or could cause system damage.
#
# Transport-aware: HTTP/remote gets the strict set (anything that arbitrary-executes
# or modifies system state). Stdio (local Claude Desktop / Code) gets the lighter
# set — those clients have their own per-tool approval dialog as the first gate.
import os as _os
_TRANSPORT = _os.environ.get("CODEC_MCP_TRANSPORT", "stdio").lower()

_STDIO_BLOCKED = ["terminal", "process_manager", "pm2_control"]
# 2026-04-17: file_ops unblocked on HTTP so claude.ai can save files to the Mac
# (paired with the new, narrower `file_write` skill). The skill still enforces
# its own path/filename blocklist — /System, /Library, /etc, .ssh, .env, etc.
_HTTP_BLOCKED = ["python_exec", "terminal", "process_manager", "pm2_control",
                  "ax_control"]

if _TRANSPORT == "http":
    # HTTP/remote always uses the strict set — user config cannot soften it.
    # Merges user-configured entries on top (additive only).
    _user = cfg.get("mcp_blocked_tools", [])
    MCP_BLOCKED_TOOLS = sorted(set(_HTTP_BLOCKED) | set(_user))
else:
    # Stdio trusts the client's approval dialog. User config wins; default lighter.
    MCP_BLOCKED_TOOLS = cfg.get("mcp_blocked_tools_stdio",
                                  cfg.get("mcp_blocked_tools", _STDIO_BLOCKED))

# Safety
DANGEROUS_PATTERNS = [
    # File deletion — catch ALL rm variants, not just rm -rf
    "rm ", "rm\t", "rm\n",  # plain rm with any argument
    "rm -rf", "rm -r /", "rm -rf /", "rm -rf ~", "rm -rf /*",
    "rmdir", "sudo rm", "unlink ", "shred ", "trash ",
    "find -delete", "-exec rm", "-exec shred",
    # Filesystem destruction
    "mkfs", "dd if=", "diskutil erase", "diskutil eraseDisk",
    # System control
    "shutdown", "reboot", "halt", "killall", "pkill",
    "sudo", "chmod 777", "chmod -R 777 /", "chown", "chown -R",
    # Device writes
    "> /dev/", "echo > /dev/sda", "> /dev/sda", "mv / /dev/null",
    # Fork bombs
    ":(){ :|:& };:", ":(){:|:&};:", "xattr -cr /",
    # Remote code execution
    "curl | bash", "wget | bash", "curl | sh", "wget | sh",
    "| bash", "| sh", "| python", "| perl", "| ruby", "| node",
    "wget |", "curl |",
    # Output redirection to sensitive paths
    "> ~/", ">> ~/", "> /etc/", ">> /etc/", "> /System/", ">> /System/",
    "> /Users/", ">> /Users/",
    # macOS system tampering
    "defaults delete", "defaults write",
    "networksetup", "networksetup -setv6",
    "launchctl unload", "csrutil disable", "nvram", "bless",
    "scutil --set", "pmset",
    "osascript -e \'tell application \"System Events\"",
    # Low-level
    "init 0", "kill -9 1", "format", "fdisk",
    # Move/overwrite destructive patterns
    "mv / ", "> /etc/", "> /System/",
]


def is_dangerous(cmd):
    """Check if a command matches any dangerous pattern.
    Uses word-boundary regex for alphanumeric patterns and substring
    matching for patterns with special characters.
    """
    import re
    cmd_lower = cmd.lower()
    for p in DANGEROUS_PATTERNS:
        p_lower = p.lower()
        # Patterns that start/end with word characters can use word boundaries
        if p_lower[0].isalnum() and p_lower[-1].isalnum():
            if re.search(r'\b' + re.escape(p_lower) + r'\b', cmd_lower):
                return True
        else:
            # Special-char patterns (fork bombs, pipes, etc.) use substring match
            if p_lower in cmd_lower:
                return True
    return False


def is_dangerous_skill_code(code: str) -> tuple[bool, str]:
    import ast

    DANGEROUS_MODULES = {
        "os", "subprocess", "ctypes", "shutil", "importlib", "signal", "pty", "socket",
    }
    SAFE_MODULES = {
        "json", "re", "math", "datetime", "collections", "itertools", "functools",
        "hashlib", "base64", "urllib.parse", "time", "random", "string", "textwrap",
        "difflib",
    }
    DANGEROUS_CALLS = {
        "eval", "exec", "compile", "__import__", "globals", "locals", "getattr",
    }
    DANGEROUS_ATTRS = {
        "os.system", "os.popen", "os.execl", "os.execle", "os.execlp", "os.execlpe",
        "os.execv", "os.execve", "os.execvp", "os.execvpe",
        "subprocess.run", "subprocess.Popen", "subprocess.call",
        "shutil.rmtree",
    }

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return (True, f"Syntax error: {e}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in DANGEROUS_MODULES:
                    return (True, f"Dangerous import: {alias.name}")

        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                if top in DANGEROUS_MODULES and node.module not in SAFE_MODULES:
                    return (True, f"Dangerous import: from {node.module}")

        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in DANGEROUS_CALLS:
                return (True, f"Dangerous call: {func.id}()")
            if isinstance(func, ast.Attribute):
                if isinstance(func.value, ast.Name):
                    full = f"{func.value.id}.{func.attr}"
                    if full in DANGEROUS_ATTRS:
                        return (True, f"Dangerous call: {full}()")
                    if full.rsplit(".", 1)[0] in DANGEROUS_MODULES:
                        return (True, f"Dangerous call: {full}()")

    return (False, "")


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
    if keyboard is None:
        # Headless: codec_config is being imported for its constants only,
        # not for live keyboard listening. Skip key resolution.
        return None
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
    stripped = text.lower().strip().rstrip(".,!?;:")
    if stripped in hallucinations:
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
