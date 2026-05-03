"""CODEC Skill: Clipboard URL Auto-Fetch.

First real declarative `SKILL_OBSERVATION_TRIGGER` shipped after Phase 2
Step 6 (Trigger System). Demonstrates the full pipeline:

  codec_observer.poll() → snapshot["clipboard"]["preview"]
        → codec_triggers.evaluate() matches `clipboard_pattern`
        → require_confirmation=True → codec_ask_user.ask("Fetch?")
        → user yes → codec_dispatch.run_skill(...) → this skill's run()
        → audit emit: trigger_fired

Runtime behavior:
  - Trigger fires when an HTTP/HTTPS URL appears in clipboard.
  - 600 s cooldown per (skill, trigger) prevents repeated fires
    when the user keeps the same URL on clipboard.
  - Consent gate via codec_ask_user.ask is non-destructive
    (read-only fetch, no side effects beyond the network request).
  - On approval, this skill reads pbpaste at runtime to get the
    current URL (handles the case where clipboard changed between
    trigger fire and skill execution), then delegates to the
    existing `web_fetch` skill.
  - Output truncated to 2000 chars to keep the notification readable.

Manual paths (chat / voice / MCP) ALSO work — the skill reads the
clipboard or extracts a URL from the user's task string. So you can
say "fetch the link I just copied" or "summarize https://..." and the
same code path handles it.
"""
SKILL_NAME = "clipboard_url_fetch"
SKILL_DESCRIPTION = (
    "Fetch and return the content of an HTTP/HTTPS URL on the clipboard. "
    "Auto-triggers when a URL is copied (with consent prompt)."
)
SKILL_TRIGGERS = [
    "fetch clipboard url", "fetch the link", "fetch this url",
    "summarize clipboard", "summarize this link", "what's at this url",
    "what's at this link", "open this url",
]
SKILL_MCP_EXPOSE = False  # local-only; no value over MCP since clipboard isn't shared

# ──────────────────────────────────────────────────────────────────────────────
# Phase 2 Step 6 declarative trigger.
# Fires when the clipboard preview contains an http(s) URL.
# Pattern is conservative: bounded character class, no whitespace,
# no quote chars (avoids accidentally matching JSON-encoded URLs as part
# of a larger blob).
# ──────────────────────────────────────────────────────────────────────────────
SKILL_OBSERVATION_TRIGGER = {
    "type": "clipboard_pattern",
    "pattern": r"https?://[^\s<>'\"]+",
    "cooldown_seconds": 600,        # 10-min per-trigger cooldown
    "require_confirmation": True,   # ask user before fetch
    "destructive": False,           # read-only operation
}


import re
import subprocess


_URL_RE = re.compile(r"https?://[^\s<>'\"]+")


def _read_clipboard() -> str:
    """Read the system clipboard via `pbpaste` (macOS).

    Returns empty string on any failure (no pbpaste, timeout, non-zero
    exit, or non-text content). The skill handles empty-clipboard
    gracefully by falling back to URLs in the task string.
    """
    try:
        result = subprocess.run(
            ["pbpaste"], capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0:
            return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        pass
    return ""


def _extract_url(text: str) -> str | None:
    """Extract the first HTTP/HTTPS URL from `text`. Returns None if none."""
    if not text:
        return None
    m = _URL_RE.search(text)
    if not m:
        return None
    # Strip common trailing punctuation that's almost never part of a URL.
    return m.group(0).rstrip(").,;'\"")


def run(task: str = "", context: str = "") -> str:
    """Fetch the URL from clipboard (or task text), return truncated content.

    Parameters
    ----------
    task: str
        For trigger auto-fires this is the rendered context string from
        codec_triggers._render_task. For chat / voice / MCP invocations
        this is the user's natural-language request, possibly containing
        a URL inline.
    context: str
        Unused; accepted for skill-API uniformity.

    Returns
    -------
    str
        Either an error message ("...: no URL...") or
        f"Fetched {url}:\n\n{content...}" where content is truncated
        to 2000 chars with a tail summary.
    """
    # Try clipboard first (the trigger path), then fall back to URL in task.
    text = _read_clipboard()
    url = _extract_url(text)
    if not url:
        url = _extract_url(task)
    if not url:
        return "clipboard_url_fetch: no URL in clipboard or task"

    # Delegate to the existing web_fetch skill for the actual HTTP work.
    # Lazy import keeps this skill loadable even if web_fetch is missing
    # from the runtime skills directory.
    try:
        import sys
        from pathlib import Path
        skills_dir = Path(__file__).resolve().parent
        if str(skills_dir) not in sys.path:
            sys.path.insert(0, str(skills_dir))
        import web_fetch  # type: ignore
        content = web_fetch.run(url)
    except ImportError:
        return f"clipboard_url_fetch: web_fetch skill not available; URL was {url}"
    except Exception as e:
        return f"clipboard_url_fetch: web_fetch failed for {url}: {e}"

    if not isinstance(content, str):
        content = str(content)

    full_len = len(content)
    if full_len > 2000:
        content = content[:2000] + f"\n... [truncated, full length: {full_len} chars]"

    return f"Fetched {url}:\n\n{content}"
