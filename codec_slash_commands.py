"""CODEC Slash Commands — first-class CLI controls in chat.

Type `/<command>` in chat to invoke meta-commands without an LLM round-trip.
This module is fully ADDITIVE: if no slash is detected, the regular chat flow
runs untouched.

Architecture
------------
- One registry: SLASH_COMMANDS (list of SlashCommand objects)
- One parser: parse_slash() — returns (cmd, args) or None
- One dispatcher: dispatch() — runs the command, returns markdown reply

The dashboard chat handler calls parse_slash() before LLM dispatch. If a
slash is matched, dispatch() runs synchronously and the markdown reply is
streamed back as if it were an LLM response (same SSE shape).

Adding new commands
-------------------
Just append to SLASH_COMMANDS at the bottom of this file. Each handler
takes the parsed args list, returns markdown.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import sqlite3
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

# ── Paths ──
CONFIG_PATH = Path(os.path.expanduser("~/.codec/config.json"))
LICENSE_DB = Path(os.path.expanduser("~/ava-stack/license-server/licenses.db"))


# ── Data model ──

@dataclass
class SlashCommand:
    """A single slash command registration."""
    name: str                                   # e.g. "skills"
    handler: Callable[[list[str]], str]         # takes args, returns markdown
    summary: str                                # one-line help
    usage: str = ""                             # e.g. "/skills [enable|disable] <name>"
    aliases: list[str] = field(default_factory=list)


# ── Parser ──

def parse_slash(text: str) -> Optional[tuple[str, list[str]]]:
    """Detect and parse a slash command. Returns (cmd_name, args) or None.

    Rules:
        - Must start with `/` (after leading whitespace)
        - Command name is `[a-zA-Z0-9_-]+`
        - Args are space-separated; quoted args preserved
        - Backslash-escaped slash `\\/` is NOT treated as slash (passes through)
        - Empty `/` or `/  ` is None (not a command)
    """
    if not text:
        return None
    s = text.strip()
    if not s.startswith("/"):
        return None
    if s.startswith("\\/"):
        return None
    body = s[1:].strip()
    if not body:
        return None

    # Split on whitespace, preserve quoted args
    import shlex
    try:
        parts = shlex.split(body)
    except ValueError:
        # Unbalanced quote — fall back to simple split
        parts = body.split()
    if not parts:
        return None

    name = parts[0].lower().strip()
    # Validate the command name (don't false-positive on URLs or paths).
    # Allow `?` as a special-case for the /? help alias.
    if not all(c.isalnum() or c in "_-?" for c in name):
        return None

    return name, parts[1:]


# ── Dispatcher ──

def find_command(name: str) -> Optional[SlashCommand]:
    """Look up by primary name or alias."""
    name = name.lower()
    for cmd in SLASH_COMMANDS:
        if cmd.name == name or name in cmd.aliases:
            return cmd
    return None


def dispatch(name: str, args: list[str]) -> str:
    """Run a slash command. Returns markdown reply."""
    cmd = find_command(name)
    if not cmd:
        return _unknown_command(name)
    try:
        return cmd.handler(args)
    except Exception as e:
        return f"⚠️ `/{name}` failed: `{type(e).__name__}: {e}`"


def _unknown_command(name: str) -> str:
    suggestions = [c.name for c in SLASH_COMMANDS if c.name.startswith(name[:2])]
    body = [f"❓ Unknown slash command: `/{name}`"]
    if suggestions:
        body.append(f"Did you mean: {', '.join(f'`/{s}`' for s in suggestions[:5])}?")
    body.append("Type `/help` for the full list.")
    return "\n\n".join(body)


# ── Helpers ──

def _load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


def _save_config(cfg: dict) -> None:
    """Atomic write to avoid half-written JSON."""
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2))
    tmp.replace(CONFIG_PATH)


def _table(headers: list[str], rows: list[list[str]]) -> str:
    """Render a markdown table."""
    out = ["| " + " | ".join(headers) + " |"]
    out.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        out.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(out)


# ── Built-in command handlers ──

def _cmd_help(args: list[str]) -> str:
    rows = [[f"`/{c.name}`", c.summary] for c in SLASH_COMMANDS]
    return "## CODEC Slash Commands\n\n" + _table(["Command", "Description"], rows) + (
        "\n\nType `/help <command>` for usage details on a specific command."
        if args else ""
    )


def _cmd_skills(args: list[str]) -> str:
    """List, enable, disable, or describe skills."""
    # Lazy import to avoid pulling skill registry at module load
    try:
        from codec_skill_registry import SkillRegistry
        from codec_config import SKILLS_DIR
    except Exception as e:
        return f"⚠️ skill registry unavailable: {e}"

    reg = SkillRegistry(SKILLS_DIR)
    reg.scan()
    cfg = _load_config()
    enabled = set(cfg.get("skills", []))

    if not args or args[0].lower() in ("list", "ls"):
        names = sorted(reg.names())
        rows = []
        for n in names:
            meta = reg.get_meta(n) or {}
            on = "✅" if n in enabled or len(enabled) == 0 else "⚪"
            desc = (meta.get("SKILL_DESCRIPTION") or "")[:60]
            rows.append([on, f"`{n}`", desc])
        return f"## Skills ({len(names)} total)\n\n" + _table(
            ["", "Name", "Description"], rows
        )

    sub = args[0].lower()
    if sub in ("enable", "on") and len(args) >= 2:
        target = args[1]
        if target not in reg.names():
            return f"⚠️ unknown skill `{target}`"
        if "skills" not in cfg or not isinstance(cfg["skills"], list):
            cfg["skills"] = list(reg.names())
        if target not in cfg["skills"]:
            cfg["skills"].append(target)
            _save_config(cfg)
        return f"✅ Skill `{target}` enabled."

    if sub in ("disable", "off") and len(args) >= 2:
        target = args[1]
        cfg["skills"] = [s for s in cfg.get("skills", []) if s != target]
        _save_config(cfg)
        return f"🚫 Skill `{target}` disabled."

    if sub == "info" and len(args) >= 2:
        target = args[1]
        meta = reg.get_meta(target)
        if not meta:
            return f"⚠️ unknown skill `{target}`"
        triggers = meta.get("SKILL_TRIGGERS", [])
        return (
            f"## `{target}`\n\n"
            f"**Description:** {meta.get('SKILL_DESCRIPTION', '(none)')}\n\n"
            f"**MCP exposed:** {meta.get('SKILL_MCP_EXPOSE', False)}\n\n"
            f"**Triggers:** {', '.join(f'`{t}`' for t in triggers[:10]) or '(none)'}"
        )

    return "Usage: `/skills [list|enable <name>|disable <name>|info <name>]`"


def _cmd_plugins(args: list[str]) -> str:
    """For now an alias of /skills until the plugin lifecycle ships."""
    return _cmd_skills(args)


def _cmd_clear(args: list[str]) -> str:
    return (
        "🧹 Chat cleared.\n\n"
        "*(The dashboard frontend should hide all prior messages on receiving "
        "this command. If they're still visible, refresh the page — the "
        "frontend hook for `/clear` is being added.)*"
    )


def _cmd_version(args: list[str]) -> str:
    cfg = _load_config()
    rows = [
        ["CODEC", _git_short_sha(Path(os.path.expanduser("~/codec-repo")))],
        ["Python", platform.python_version()],
        ["macOS", platform.mac_ver()[0] or "(unknown)"],
        ["LLM model", cfg.get("llm_model", "(not set)")],
        ["Vision model", cfg.get("vision_model", "(not set)")],
        ["TTS engine", cfg.get("tts_engine", "(not set)")],
        ["AVA proxy", (cfg.get("ava") or {}).get("proxy_url", "(disabled)")],
        ["AVA license", _ava_license_status(cfg)],
    ]
    return "## CODEC Version\n\n" + _table(["Component", "Value"], rows)


def _git_short_sha(repo_dir: Path) -> str:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo_dir), "rev-parse", "--short", "HEAD"],
            timeout=2, stderr=subprocess.DEVNULL,
        )
        return out.decode().strip()
    except Exception:
        return "(no git)"


def _ava_license_status(cfg: dict) -> str:
    ava = cfg.get("ava") or {}
    if not ava.get("enabled"):
        return "(disabled)"
    key = ava.get("license_key", "")
    if not key:
        return "(no key)"
    # Decode JWT payload to show tier+expiry without verification
    try:
        import base64
        parts = key.split(".")
        if len(parts) != 3:
            return "(malformed)"
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        exp = datetime.fromtimestamp(data.get("exp", 0), tz=timezone.utc)
        return f"{data.get('tier', '?')} • expires {exp.date().isoformat()}"
    except Exception:
        return "(unreadable)"


def _cmd_cost(args: list[str]) -> str:
    """Today's spend from the AVA proxy usage table."""
    if not LICENSE_DB.exists():
        return "⚠️ AVA usage DB not found at `~/ava-stack/license-server/licenses.db`."
    today_utc = datetime.now(timezone.utc).date().isoformat()
    try:
        with sqlite3.connect(str(LICENSE_DB)) as c:
            c.row_factory = sqlite3.Row
            r = c.execute(
                "SELECT COUNT(*) as n, COALESCE(SUM(input_tokens), 0) as in_tok, "
                "COALESCE(SUM(output_tokens), 0) as out_tok, "
                "COALESCE(SUM(billed_usd_cents), 0) as cents "
                "FROM usage WHERE substr(ts,1,10)=?", (today_utc,)
            ).fetchone()
            month_start = datetime.now(timezone.utc).replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            ).isoformat()
            m = c.execute(
                "SELECT COUNT(*) as n, COALESCE(SUM(billed_usd_cents), 0) as cents "
                "FROM usage WHERE ts >= ?", (month_start,)
            ).fetchone()
    except sqlite3.OperationalError as e:
        return f"⚠️ usage DB query failed: {e}"

    return (
        f"## Today's spend ({today_utc})\n\n"
        f"- Requests: **{r['n']}**\n"
        f"- Input tokens: **{r['in_tok']:,}**\n"
        f"- Output tokens: **{r['out_tok']:,}**\n"
        f"- Billed: **${r['cents']/100:.2f}**\n\n"
        f"## Month to date\n\n"
        f"- Requests: **{m['n']}**\n"
        f"- Billed: **${m['cents']/100:.2f}**"
    )


def _cmd_status(args: list[str]) -> str:
    """Quick green/red dot for each major service."""
    import requests
    services = [
        ("Local Qwen", "http://localhost:8083/v1/models"),
        ("Whisper STT", "http://localhost:8084/v1/models"),
        ("Kokoro TTS", "http://localhost:8085/v1/models"),
        ("AVA proxy", "https://ava-proxy.lucyvpa.com/health"),
        ("AVA license", "https://ava-license.lucyvpa.com/health"),
    ]
    rows = []
    for name, url in services:
        t0 = time.monotonic()
        try:
            r = requests.get(url, timeout=2)
            ok = r.status_code < 500
            ms = int((time.monotonic() - t0) * 1000)
            rows.append([("🟢" if ok else "🔴"), name, f"HTTP {r.status_code}", f"{ms}ms"])
        except Exception:
            rows.append(["🔴", name, "(no response)", "—"])
    return "## Service status\n\n" + _table(["", "Service", "HTTP", "Latency"], rows)


def _cmd_who(args: list[str]) -> str:
    cfg = _load_config()
    return (
        f"## CODEC identity\n\n"
        f"- Agent name: **{cfg.get('agent_name', 'CODEC')}**\n"
        f"- Nickname: **{cfg.get('agent_nickname', '(none)')}**\n"
        f"- Wake phrases: {', '.join(f'`{p}`' for p in cfg.get('wake_phrases', []))}\n"
        f"- License email: **{(cfg.get('ava') or {}).get('license_key', 'n/a')[:16]}…**"
    )


# ── Registry (add new commands here) ──

SLASH_COMMANDS: list[SlashCommand] = [
    SlashCommand(
        name="help",
        handler=_cmd_help,
        summary="List all slash commands",
        aliases=["?", "commands"],
    ),
    SlashCommand(
        name="skills",
        handler=_cmd_skills,
        summary="List, enable, disable, or describe skills",
        usage="/skills [list|enable <name>|disable <name>|info <name>]",
    ),
    SlashCommand(
        name="plugins",
        handler=_cmd_plugins,
        summary="(alias of /skills until plugin lifecycle ships)",
        aliases=["plugin"],
    ),
    SlashCommand(
        name="version",
        handler=_cmd_version,
        summary="Show CODEC version + active models + license",
        aliases=["v"],
    ),
    SlashCommand(
        name="cost",
        handler=_cmd_cost,
        summary="Today's and month-to-date AVA proxy spend",
        aliases=["spend", "usage"],
    ),
    SlashCommand(
        name="status",
        handler=_cmd_status,
        summary="Quick health check of CODEC's local services",
    ),
    SlashCommand(
        name="who",
        handler=_cmd_who,
        summary="Show CODEC's current persona settings",
    ),
    SlashCommand(
        name="clear",
        handler=_cmd_clear,
        summary="Clear the chat session (frontend hides messages)",
        aliases=["cls"],
    ),
]


# ── CLI smoke test entry-point ──
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print(_cmd_help([]))
        sys.exit(0)
    line = " ".join(sys.argv[1:])
    parsed = parse_slash(line if line.startswith("/") else "/" + line)
    if not parsed:
        print(f"Not a slash command: {line!r}")
        sys.exit(1)
    name, args = parsed
    print(dispatch(name, args))
