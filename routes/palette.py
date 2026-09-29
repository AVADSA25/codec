"""What the '/' popover, the '@' picker and the Cmd+K palette offer (UI phase 2, P2.3;
docs/P2.3-DESIGN.md). Read-only; behind the dashboard login like every /api route."""
from __future__ import annotations

import json
import logging
import os

from fastapi import APIRouter

import routes._shared as shared

log = logging.getLogger("codec")
router = APIRouter()

# Plain groups for the '@' picker; a skill not named here goes under "Other".
SKILL_GROUPS = (
    ("Google", lambda n: n.startswith("google_")),
    ("Browser", lambda n: n.startswith("chrome_") or n == "prompt_feeder"),
    ("Mac", lambda n: n in {"system", "network_info", "app_switch", "brightness", "volume_brightness",
                             "process_manager", "pm2_control", "ax_control", "screenshot_text", "clipboard",
                             "tailscale", "terminal", "file_ops", "file_search"}),
    ("Memory and notes", lambda n: n in {"memory_search", "notes", "reminders", "thread_note", "standing_rules",
                                          "observer_recall"}),
    ("Reports", lambda n: n in {"shift_report", "daily_kickoff", "AI News Digest", "scheduler", "delegate"}),
    ("Home and media", lambda n: n in {"philips_hue", "music"}),
    ("Tools", lambda n: n in {"calculator", "weather", "web_search", "bitcoin_price", "time", "timer", "translate",
                               "password_generator", "qr_generator", "json_formatter", "pomodoro",
                               "clipboard_url_fetch"}),
)
# Has its own brief-and-confirm flow in chat ("create a skill for …"), so it is not a direct pick.
NOT_PICKABLE = {"create_skill"}


def skill_group(name: str) -> str:
    for group, test in SKILL_GROUPS:
        if test(name):
            return group
    return "Other"


def pickable_skills() -> set[str]:
    """The skills '@' may pick and the chat `skill` field may run: the chat allowlist (the same set
    automatic routing uses) minus NOT_PICKABLE, limited to what the registry knows."""
    from codec_dispatch import registry
    from routes.chat import CHAT_SKILL_ALLOWLIST
    if not registry.names():  # metadata only (AST); no skill code runs
        registry.scan()
    return (set(CHAT_SKILL_ALLOWLIST) - NOT_PICKABLE) & set(registry.names())


def _short(text: str, limit: int = 110) -> str:
    text = " ".join(str(text or "").split())
    first = text.split(". ")[0].rstrip(".")
    return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"


@router.get("/api/slash_commands")
def slash_commands():
    from codec_slash_commands import SLASH_COMMANDS
    return {"commands": [{"name": c.name, "summary": c.summary, "usage": c.usage or f"/{c.name}",
                          "aliases": list(c.aliases)} for c in SLASH_COMMANDS]}


def _agents() -> list[dict]:
    out = []
    try:
        names = sorted(os.listdir(shared._AGENTS_DIR))
    except OSError:
        return out
    for f in names:
        if not f.endswith(".json"):
            continue
        try:
            with open(os.path.join(shared._AGENTS_DIR, f), encoding="utf-8") as fh:
                a = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(a, dict) and a.get("name"):
            # The whole role: a picked agent runs as saved (the list trims it on screen).
            out.append({"id": str(a.get("id") or f[:-5]), "name": str(a["name"]),
                        "role": str(a.get("role") or "")[:4000], "tools": list(a.get("tools") or []),
                        "max_iterations": int(a.get("max_iterations") or 8)})
    return out


def _mcp_servers() -> list[dict]:
    try:
        from routes.mcp import _read_servers
        servers = _read_servers()
    except Exception:
        return []
    return [{"name": str(s["name"]), "description": _short(s.get("description") or s.get("url") or "", 90)}
            for s in servers if isinstance(s, dict) and s.get("name") and s.get("enabled")]


@router.get("/api/mentions")
def mentions():
    from codec_agents import CREW_REGISTRY
    from codec_dispatch import registry
    skills = [{"name": n, "description": _short(registry.get_description(n)), "group": skill_group(n)}
              for n in sorted(pickable_skills(), key=str.lower)]
    crews = [{"name": k, "label": k.replace("_", " ").title(), "description": _short(v.get("description", ""), 90),
              "arg": (v.get("args") or [None])[0]} for k, v in CREW_REGISTRY.items()]
    return {"skills": skills, "crews": crews, "agents": _agents(), "mcp": _mcp_servers()}
