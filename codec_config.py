"""CODEC Configuration — loads ~/.codec/config.json and exposes all constants"""
import os
import json

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

# ── Config schema versioning + migration (A-15, PR-3) ────────────────────────
# Before this, ~/.codec/config.json was a flat untracked dict — upgrading CODEC
# (which added step_budget / stuck / observer / shift_report / ask_user / … over
# Phase 1-3) had no version stamp or migration story. Now load_config() stamps a
# `config_version` and runs an ordered migration ladder on first load after an
# upgrade. All Phase 1-3 keys were ADDITIVE with safe `.get(k, default)`
# fallbacks, so v0→v1 just stamps the version; future schema changes append a
# `_migrate_vN_to_vN+1` step to the ladder.
CONFIG_SCHEMA_VERSION = 1


def _migrate_v0_to_v1(cfg: dict) -> dict:
    """v0 (no `config_version`) → v1. No field rewrites needed (all historical
    additions used safe defaults); this just stamps the version so future
    migrations have a known baseline."""
    cfg["config_version"] = 1
    return cfg


# Ordered ladder: _CONFIG_MIGRATIONS[i] migrates v(i) → v(i+1).
_CONFIG_MIGRATIONS = [
    _migrate_v0_to_v1,
]


def _migrate_config(cfg: dict) -> tuple:
    """Run the migration ladder from the config's current version up to
    CONFIG_SCHEMA_VERSION. Returns (cfg, changed: bool). Never raises."""
    try:
        current = cfg.get("config_version", 0)
        if not isinstance(current, int) or current < 0:
            current = 0
    except AttributeError:
        return cfg, False  # cfg isn't a dict — leave it alone
    changed = False
    while current < CONFIG_SCHEMA_VERSION and current < len(_CONFIG_MIGRATIONS):
        cfg = _CONFIG_MIGRATIONS[current](cfg)
        nxt = cfg.get("config_version", current + 1)
        # Guard against a migration that forgets to bump the version (avoid
        # an infinite loop): force monotonic progress.
        current = nxt if nxt > current else current + 1
        changed = True
    return cfg, changed


def _write_config_atomic(cfg: dict) -> bool:
    """Atomic tmp+rename write of the full config at 0600. Returns success.
    Mirrors `_blank_config_field`'s write discipline."""
    try:
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, CONFIG_PATH)
        try:
            os.chmod(CONFIG_PATH, 0o600)
        except Exception:
            pass
        return True
    except Exception as e:
        print(f"[CODEC] Warning: failed to write migrated config: {e}")
        return False


def load_config():
    """Load config from ~/.codec/config.json, return dict.

    A-15: stamps `config_version` and runs the migration ladder. Writes back
    ONLY when the file already exists AND a migration changed something —
    idempotent (subsequent loads see the current version and skip), atomic,
    and never:
      - creates a config file just to stamp a version (fresh installs / CI
        get the version in-memory only), nor
      - overwrites an unparseable config (leaves it for the user to fix).
    """
    cfg = {}
    existed = os.path.exists(CONFIG_PATH)
    if existed:
        try:
            with open(CONFIG_PATH) as f:
                cfg = json.load(f)
        except Exception as e:
            # Don't migrate/overwrite a corrupt file — preserve it for repair.
            print(f"[CODEC] Warning: failed to parse {CONFIG_PATH}: {e}")
            return cfg
    cfg, changed = _migrate_config(cfg)
    if existed and changed:
        _write_config_atomic(cfg)
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
# A-21 (PR-3C): AGENT_NAME removed — it was declared but never read anywhere.
# Consumers read `cfg.get('agent_name', ...)` inline (codec_agent.py,
# codec_slash_commands.py, codec_dashboard.py). ASSISTANT_NAME + USER_NAME ARE used.
ASSISTANT_NAME    = cfg.get('assistant_name', 'CODEC')
USER_NAME         = cfg.get('user_name', '')

# LLM
QWEN_BASE_URL     = cfg.get("llm_base_url", "http://localhost:8081/v1")
QWEN_MODEL        = cfg.get("llm_model", "mlx-community/Qwen3.6-35B-A3B-4bit")


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


# ── PR-2B-2: remaining provider secrets (closes D-15 fully) ───────────────────
#
# Four more secrets move cfg-plaintext → Keychain, reusing the PR-2B
# `_migrate_and_get` / `_cached_secret` machinery. Three are top-level config
# keys; `telegram.bot_token` is NESTED under the `telegram` dict, so it gets
# the `_migrate_and_get_nested` variant. All four keep an env-var fallback
# (read AFTER Keychain/cfg) to preserve the pre-PR-2B-2 call-site behavior
# (`cfg.get("X", os.environ.get("X_ENV", ""))`).

def _blank_nested_config_field(parent_key: str, child_key: str) -> None:
    """Atomically blank a NESTED field in ~/.codec/config.json
    (e.g. telegram.bot_token). Mirrors `_blank_config_field` but descends
    one level. Keeps the key (set to "") so the schema stays stable."""
    try:
        current = load_config()
        parent = current.get(parent_key)
        if not isinstance(parent, dict) or child_key not in parent:
            return
        parent[child_key] = ""
        current[parent_key] = parent
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(current, f, indent=2)
        os.replace(tmp, CONFIG_PATH)
        try:
            os.chmod(CONFIG_PATH, 0o600)
        except Exception:
            pass
    except Exception as e:
        print(f"[CODEC] Warning: _blank_nested_config_field("
              f"{parent_key!r}, {child_key!r}) failed: {e}")


def _migrate_and_get_nested(kc_key: str, parent_key: str, child_key: str) -> str:
    """Like `_migrate_and_get` but for a nested cfg key. Reads Keychain first;
    on miss migrates `cfg[parent_key][child_key]` plaintext → Keychain and
    blanks the nested field on disk."""
    try:
        from codec_keychain import keychain_get, migrate_from_plaintext
    except Exception:
        parent = cfg.get(parent_key, {})
        return parent.get(child_key, "") if isinstance(parent, dict) else ""
    k = keychain_get(kc_key)
    if k:
        return k
    parent = cfg.get(parent_key, {})
    plaintext = parent.get(child_key, "") if isinstance(parent, dict) else ""
    if not plaintext:
        return ""

    def _blank():
        _blank_nested_config_field(parent_key, child_key)
        if isinstance(cfg.get(parent_key), dict):
            cfg[parent_key][child_key] = ""  # in-process dict mirror

    if migrate_from_plaintext(kc_key, plaintext, _blank):
        return keychain_get(kc_key) or plaintext
    return plaintext


def get_gemini_api_key() -> str:
    """Gemini API key. Keychain → cfg (migrate on first call) → GEMINI_API_KEY
    env fallback. Closes D-15 (PR-2B-2)."""
    v = _cached_secret("gemini_api_key",
                       lambda: _migrate_and_get("gemini_api_key", "gemini_api_key"))
    return v or os.environ.get("GEMINI_API_KEY", "")


def get_pexels_api_key() -> str:
    """Pexels API key. Keychain → cfg (migrate) → PEXELS_API_KEY env."""
    v = _cached_secret("pexels_api_key",
                       lambda: _migrate_and_get("pexels_api_key", "pexels_api_key"))
    return v or os.environ.get("PEXELS_API_KEY", "")


def get_serper_api_key() -> str:
    """Serper API key. Keychain → cfg (migrate) → SERPER_API_KEY env."""
    v = _cached_secret("serper_api_key",
                       lambda: _migrate_and_get("serper_api_key", "serper_api_key"))
    return v or os.environ.get("SERPER_API_KEY", "")


def get_telegram_bot_token() -> str:
    """Telegram bot token (nested cfg key telegram.bot_token).
    Keychain → cfg (migrate) → TELEGRAM_BOT_TOKEN env."""
    v = _cached_secret("telegram_bot_token",
                       lambda: _migrate_and_get_nested(
                           "telegram_bot_token", "telegram", "bot_token"))
    return v or os.environ.get("TELEGRAM_BOT_TOKEN", "")


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


# ── D-6 closure (PR-2G): hardened dangerous-command detection ─────────────────
#
# `is_dangerous` is a CONFIRMATION-TRIGGER heuristic / typo-catcher — it is NOT
# a complete security boundary. The audit (D-6) found the old fixed-pattern
# blocker had a 45% bypass rate (19/42 red-team variants). This rewrite closes
# all 19 by (1) normalizing the command first and (2) running layered category
# checks instead of exact-string matching.
#
# The REAL security boundaries remain:
#   - `_HTTP_BLOCKED` / `_STDIO_BLOCKED` (terminal never reachable over MCP)
#   - the Step 3 strict-consent gate (literal-verb confirmation for destructive)
#   - `terminal` skill `SKILL_MCP_EXPOSE=False`
# A pattern matcher is at best a typo-catcher; do NOT rely on it as the only gate.

# Sensitive path fragments — reading, writing, moving, or exfiltrating any of
# these warrants confirmation regardless of which binary is used. Closes the
# info-disclosure / exfil / audit-tamper bypasses (variants 14, 33, 34, 36,
# 37, 38, 41).
_SENSITIVE_PATH_FRAGMENTS = (
    "/etc/passwd", "/etc/shadow", "/etc/sudoers", "/etc/master.passwd",
    "/.ssh", "id_rsa", "id_ed25519", "id_dsa", "id_ecdsa",
    "/.aws/credentials", "/.aws/config", "/.gnupg", "/.config/gh",
    "/library/keychains", "/.codec/", "oauth_state", "audit.log",
    "secrets.enc", "secret.key", "plugins.allowlist", "memory.db",
    "/.bash_history", "/.zsh_history",
)

# Binaries that should always require confirmation when they LEAD the command.
# Closes binary-level bypasses (diskutil variants, chflags, ln -s, etc.).
_DANGEROUS_LEAD_BINARIES = frozenset({
    "rm", "rmdir", "unlink", "shred", "srm", "trash",
    "dd", "mkfs", "newfs", "diskutil", "fdisk", "format", "hdiutil",
    "kill", "killall", "pkill", "shutdown", "reboot", "halt", "init",
    "sudo", "su", "doas",
    "chmod", "chown", "chgrp", "chflags", "xattr",
    "launchctl", "csrutil", "nvram", "bless", "scutil", "pmset", "spctl",
    "defaults", "networksetup", "systemsetup", "dscl", "diskutil",
    "ln",  # symlink redirection (variant 14)
})

# Leading wrappers stripped before identifying the "real" lead binary, so
# `sudo rm`, `env rm`, `nohup rm`, `time rm`, `nice rm` all resolve to `rm`.
_LEAD_WRAPPERS = frozenset({
    "sudo", "doas", "env", "command", "builtin", "exec", "nohup",
    "time", "nice", "ionice", "stdbuf", "setsid", "caffeinate",
})

# Network-fetch binaries — flagged only when combined with a pipe-to-shell,
# a sensitive path, or an upload/POST flag (plain GETs stay allowed for UX).
_NETWORK_BINARIES = ("curl", "wget", "nc", "ncat", "telnet", "ftp", "scp",
                     "rsync", "tftp")
_UPLOAD_FLAGS = ("-d @", "--data @", "-d@", "--data-binary @", "-t ",
                 "--upload-file", "-f @", "--form")

# Inline interpreter exec strings — running code from a string, not a file.
_INLINE_EXEC = ("python -c", "python3 -c", "python2 -c",
                "perl -e", "perl -n", "ruby -e", "node -e", "node --eval",
                "bash -c", "sh -c", "zsh -c", "ruby -ropen-uri")

# Encoding / eval evasion — decode-then-run chains (variants 13, 20).
_ENCODING_EVAL = ("base64 -d", "base64 --decode", "base64 -di", "eval ",
                  "$(", "`", "| base64", "|base64", "xxd -r")

# Pipe-to-interpreter (checked AFTER pipe-spacing normalization → `|bash`).
_PIPE_TO_INTERP = ("|bash", "|sh", "|zsh", "|python", "|python3", "|perl",
                   "|ruby", "|node", "|php", "|osascript")

# Destructive flags that are dangerous regardless of the (possibly hidden)
# binary — `-rf /`, `-rf ~`, `-rf *` (variant 11, shell-expansion-hidden rm),
# `-delete` (find primary, variant 4), `--remove-files` (tar, variant 6).
_DESTRUCTIVE_FLAGS = ("-rf /", "-rf ~", "-rf *", "-rf .", "-fr /", "-fr ~",
                      "-fr *", "-rf --no-preserve-root", "--no-preserve-root",
                      "--remove-files", "-delete")

# osascript driving a destructive app action — broadened beyond the legacy
# hardcoded "System Events" pattern (variant 24: Finder delete).
_OSASCRIPT_DESTRUCTIVE_VERBS = ("delete", "to delete", "remove", "erase",
                                "move to trash", "empty trash", "quit app",
                                "rm ", "do shell script")

# Env-var secret disclosure — `echo $SECRET_KEY` and friends (variant 35).
_SECRET_ENV_HINTS = ("secret", "token", "password", "passwd", "api_key",
                     "apikey", "private_key", "access_key", "credential")


def _normalize_command(cmd: str) -> str:
    """Normalize a command for matching. Closes whitespace / backslash /
    pipe-spacing bypasses (variants 4, 11, 18, 27, 42):
      - lowercase
      - strip backslash-escapes (`\\ ` → ` `, `\\t` → ` `, stray `\\`)
      - collapse all whitespace runs (incl. tabs/newlines) to single spaces
      - remove spaces immediately around pipes so `x | bash` == `x|bash`
    """
    import re
    s = cmd.lower()
    # Drop backslash-escapes: `\<char>` → `<char>` (so `rm\ -rf` → `rm -rf`),
    # and bare trailing backslashes vanish.
    s = re.sub(r"\\(.)", r"\1", s)
    s = s.replace("\\", "")
    # Collapse all whitespace (tabs, newlines, multiple spaces) to one space.
    s = re.sub(r"\s+", " ", s)
    # Remove spaces around pipes so `curl x | bash` and `curl x|bash` match alike.
    s = re.sub(r"\s*\|\s*", "|", s)
    return s.strip()


def _lead_binary(normalized: str) -> str:
    """Return the effective leading binary, stripping wrappers (sudo/env/...),
    leading `VAR=value` assignments, and a leading backslash (alias bypass)."""
    # Split on common command separators; take the first segment.
    import re
    first = re.split(r"[;&|]", normalized, maxsplit=1)[0].strip()
    tokens = first.split()
    while tokens:
        tok = tokens[0].lstrip("\\")  # `\rm` → `rm`
        # Skip leading VAR=value env assignments (e.g. `rm=rm` after lowercase)
        if "=" in tok and not tok.startswith("-") and "/" not in tok.split("=", 1)[0]:
            tokens = tokens[1:]
            continue
        if tok in _LEAD_WRAPPERS:
            tokens = tokens[1:]
            continue
        # Strip a leading path so `/bin/rm` → `rm`
        return tok.rsplit("/", 1)[-1]
    return ""


def is_dangerous(cmd):
    """Heuristic check: should this command require user confirmation (or be
    blocked, in terminal.py)? Returns True for anything that destroys data,
    tampers with the system, touches a sensitive path, exfiltrates over the
    network, or runs code from an encoded/inline string.

    Hardened in PR-2G (D-6 closure) — see the module comment above. NOT a
    complete security boundary; it's a confirmation-trigger heuristic.

    Never raises — malformed input returns False rather than erroring.
    """
    import re
    try:
        if not cmd or not isinstance(cmd, str) or not cmd.strip():
            return False
        norm = _normalize_command(cmd)
        if not norm:
            return False

        # ── Layer A: legacy exact patterns (kept; now run on normalized text) ──
        for p in DANGEROUS_PATTERNS:
            p_lower = _normalize_command(p) if (" " in p or "|" in p) else p.lower()
            if not p_lower:
                continue
            if p_lower[0].isalnum() and p_lower[-1].isalnum():
                if re.search(r"\b" + re.escape(p_lower) + r"\b", norm):
                    return True
            else:
                if p_lower in norm:
                    return True

        # ── Layer B: dangerous leading binary (after wrapper/alias stripping) ──
        lead = _lead_binary(norm)
        if lead in _DANGEROUS_LEAD_BINARIES:
            return True

        # ── Layer C: sensitive path access (read / write / move / exfil) ──
        for frag in _SENSITIVE_PATH_FRAGMENTS:
            if frag in norm:
                return True

        # ── Layer D: pipe-to-interpreter (pipe spacing already normalized) ──
        for p in _PIPE_TO_INTERP:
            if p in norm:
                return True

        # ── Layer E: encoding / eval evasion ──
        for p in _ENCODING_EVAL:
            if p in norm:
                return True

        # ── Layer F: inline interpreter exec strings ──
        for p in _INLINE_EXEC:
            if p in norm:
                return True

        # ── Layer G: destructive flags regardless of (hidden) binary ──
        for p in _DESTRUCTIVE_FLAGS:
            if p in norm:
                return True

        # ── Layer H: network exfil (curl/wget + sensitive path | pipe | upload) ──
        # (plain GETs without those signals stay allowed — UX guard)
        first_tok = norm.split(" ", 1)[0].lstrip("\\").rsplit("/", 1)[-1]
        if first_tok in _NETWORK_BINARIES:
            if any(uf in norm for uf in _UPLOAD_FLAGS):
                return True
            # sensitive path / pipe already covered by Layers C/D, but a
            # network binary writing to a redirect or fetching a script is
            # still worth confirming when piped — covered by D. Plain GET → ok.

        # ── Layer I: env-var secret disclosure (echo $SECRET_KEY) ──
        if first_tok in ("echo", "printenv", "env"):
            # Look for $VAR or ${VAR} references whose name hints at a secret.
            for m in re.findall(r"\$\{?([a-z_][a-z0-9_]*)", norm):
                if any(h in m for h in _SECRET_ENV_HINTS):
                    return True

        # ── Layer J: kill with a negative pid (kill -9 -1 = whole process grp) ──
        if lead in ("kill",) or first_tok == "kill":
            if re.search(r"kill\b.*\s-\d", norm):
                return True

        # ── Layer K: osascript driving a destructive app action ──
        if "osascript" in norm:
            for verb in _OSASCRIPT_DESTRUCTIVE_VERBS:
                if verb in norm:
                    return True

        return False
    except Exception:
        # Fail-safe: never let the blocker itself crash the caller.
        return False


# ── D-17 closure (PR-2H): reflection-aware AST module/call/attr sets ──────────
#
# The pre-PR-2H validator caught direct imports + named dangerous calls + a few
# attribute calls, but missed RUNTIME REFLECTION sandbox-escapes that build the
# dangerous call dynamically (no matching Name/Attribute-with-Name-base at the
# top level). PR-2H adds:
#   - `_DANGEROUS_REFLECTION_ATTRS`: dunder attributes used in escape chains
#     (`__class__`, `__bases__`, `__subclasses__`, `__mro__`, `__globals__`,
#     `__dict__`, `__code__`, ...). Any ast.Attribute with these `.attr` →
#     refused, regardless of the base object.
#   - bare `__builtins__` Name reference → refused (no legit skill touches it).
#   - `vars`, `dir` added to DANGEROUS_CALLS (audit D-17 + the python_exec
#     placeholder test explicitly call for these).
#   - network modules in DANGEROUS_MODULES (exfil vector — urllib.request,
#     http, ftplib, smtplib, requests, ...). `urllib.parse` stays SAFE.
#
# `open` is intentionally NOT blocked here — legitimate file skills need it and
# runtime file access is constrained elsewhere (python_exec sandbox-exec
# profile, file_ops/file_write path blocklists). Documented residual.

# NOTE: network modules (urllib.request, requests, httpx, http.client, ...)
# are deliberately NOT blocked here. The audit's `urllib.request` exfil example
# is a real concern, but (1) blocking only urllib while allowing `requests`
# would be security theater, (2) blocking ALL network breaks the existing
# contract that skills may legitimately make HTTP calls (weather, web_search,
# self_improve-drafted API skills), and (3) network for UNTRUSTED python_exec
# snippets is already blocked at runtime by PR-2C's sandbox-exec profile.
# Comprehensive network gating belongs in the audit's "Eventually: positive
# allowlist of permitted modules" rewrite, not this reflection-focused PR.
_SKILL_DANGEROUS_MODULES = {
    "os", "subprocess", "ctypes", "shutil", "importlib", "signal", "pty",
    "socket",
}
_SKILL_SAFE_MODULES = {
    "json", "re", "math", "datetime", "collections", "itertools", "functools",
    "hashlib", "base64", "urllib.parse", "time", "random", "string", "textwrap",
    "difflib",
}
_SKILL_DANGEROUS_CALLS = {
    "eval", "exec", "compile", "__import__", "globals", "locals", "getattr",
    "setattr", "delattr", "vars", "dir",  # vars/dir added per D-17
}
_SKILL_DANGEROUS_ATTRS = {
    "os.system", "os.popen", "os.execl", "os.execle", "os.execlp", "os.execlpe",
    "os.execv", "os.execve", "os.execvp", "os.execvpe",
    "subprocess.run", "subprocess.Popen", "subprocess.call",
    "shutil.rmtree",
}
# Cross-cutting audit follow-up: EXTRA denylist applied only in strict mode (opt-in),
# for AUTONOMOUSLY LLM-generated skills (codec_self_improve nightly drafter). These are the
# rarely-legit-in-a-utility-skill primitives the base gate misses: deserialization-RCE /
# persistence (pickle/marshal/shelve) and legacy exfil protocols (smtplib/ftplib/telnetlib).
# Deliberately NOT included: HTTP (requests/urllib/httpx) and open() — those are common +
# legitimate, and self_improve proposals are human-reviewed before promotion, so the review
# gate (not a blanket block) is the right control for them. User-invoked generation
# (skill_forge / create_skill→approve) stays on the default gate entirely.
_SKILL_STRICT_EXTRA_MODULES = {
    "pickle", "marshal", "shelve", "smtplib", "ftplib", "telnetlib",
}
# Reflection dunder attributes — dangerous regardless of the base object.
# These are the building blocks of every CPython sandbox-escape chain.
_DANGEROUS_REFLECTION_ATTRS = {
    "__class__", "__bases__", "__base__", "__subclasses__", "__mro__",
    "__globals__", "__dict__", "__builtins__", "__code__", "__closure__",
    "__func__", "__self__", "__getattribute__", "__subclasshook__",
    "__init_subclass__", "__reduce__", "__reduce_ex__", "__getattr__",
    "__import__", "__loader__", "__spec__",
}
# Names that should never appear in a normal skill (reflection roots).
_DANGEROUS_REFLECTION_NAMES = {"__builtins__", "__loader__", "__spec__"}


def is_dangerous_skill_code(code: str, strict: bool = False) -> tuple[bool, str]:
    """Return (dangerous, reason) for a skill source string.

    strict=False (default): the established gate — UNCHANGED. Used for user-curated skill
    load, python_exec, and user-invoked generation (skill_forge / create_skill→approve).
    strict=True: additionally blocks the rarely-legit serialization / legacy-exfil modules
    in _SKILL_STRICT_EXTRA_MODULES — for AUTONOMOUSLY LLM-generated skills (codec_self_improve).
    HTTP (requests/urllib/httpx) and open() are deliberately NOT blocked (common + legitimate,
    and those proposals are human-reviewed). SAFE_MODULES stay allowed in both modes.
    """
    import ast

    DANGEROUS_MODULES = (_SKILL_DANGEROUS_MODULES | _SKILL_STRICT_EXTRA_MODULES) if strict else _SKILL_DANGEROUS_MODULES
    SAFE_MODULES = _SKILL_SAFE_MODULES
    DANGEROUS_CALLS = _SKILL_DANGEROUS_CALLS
    DANGEROUS_ATTRS = _SKILL_DANGEROUS_ATTRS

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return (True, f"Syntax error: {e}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                # Mirror ImportFrom: a dangerous top-level module is allowed
                # only if the FULL dotted name is explicitly safe (urllib.parse).
                if top in DANGEROUS_MODULES and alias.name not in SAFE_MODULES:
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

        # ── D-17: reflection attribute access (any base object) ──
        elif isinstance(node, ast.Attribute):
            if node.attr in _DANGEROUS_REFLECTION_ATTRS:
                return (True, f"Dangerous reflection attribute: .{node.attr}")

        # ── D-17: bare reflection-root Name (e.g. `__builtins__`) ──
        elif isinstance(node, ast.Name):
            if node.id in _DANGEROUS_REFLECTION_NAMES:
                return (True, f"Dangerous reflection name: {node.id}")

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
