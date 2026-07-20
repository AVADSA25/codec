"""CODEC Skill: Prompt Feeder — paste a list of prompts into an AI tool, one at a time.

The chore this removes: you have a shot list (or a set of questions) and you sit
there pasting each one into Google Flow / Gemini, waiting, pasting the next.
Hand CODEC the list and it drives the Pilot browser for you while you watch it
happen in the live view.

Drives the same Pilot browser (port 8094) the Pilot tab shows, so every step is
visible live, and Record can compile the whole run into a reusable skill.
"""

import json
import os
import re
import time
import urllib.error
import urllib.request

SKILL_NAME = "prompt_feeder"
SKILL_DESCRIPTION = (
    "Feed a list of prompts one at a time into an AI tool (Google Flow, Gemini, "
    "ChatGPT, Claude) in the Pilot browser, waiting for each answer before "
    "sending the next. Watch it happen in the Pilot tab's live view."
)
SKILL_MCP_EXPOSE = True
SKILL_TRIGGERS = [
    "feed these prompts",
    "feed prompts",
    "paste these prompts",
    "run these prompts",
    "send these prompts",
    "one prompt at a time",
    "prompt feeder",
]

_BASE = "http://localhost:8094"
_TIMEOUT = 30

# Where to send them. Aliases are what a person actually says out loud.
_TARGETS = {
    "flow":    ("https://labs.google/fx/tools/flow", ["google flow", "flow", "labs.google"]),
    "gemini":  ("https://gemini.google.com/app",     ["gemini", "google gemini", "bard"]),
    "chatgpt": ("https://chatgpt.com/",              ["chatgpt", "chat gpt", "openai"]),
    "claude":  ("https://claude.ai/new",             ["claude", "claude.ai"]),
}

# Roles that can accept typed text, best-first.
_INPUT_ROLES = ("textbox", "searchbox", "combobox")
# An input whose accessible name reads like a prompt box beats a stray search field.
_PROMPT_WORDS = ("prompt", "ask", "message", "type", "chat", "describe", "idea", "search")

_MAX_PROMPTS = 12          # a runaway list would drive the browser for an hour
_SETTLE_POLL_S = 1.5
_STABLE_READS = 2          # consecutive identical reads = the tool has gone quiet
_INPUT_WAIT_S = 12         # SPA chat inputs mount seconds after first paint
_DEFAULT_WAIT_S = 30       # per-prompt ceiling waiting for the tool to answer


# ── Pilot runner client ───────────────────────────────────────────────────────

def _pilot_token() -> str:
    """Pilot PP-1 shared secret. Both sides read ~/.codec/pilot_token."""
    try:
        with open(os.path.expanduser("~/.codec/pilot_token")) as f:
            return f.read().strip()
    except Exception:
        return ""


def _req(path: str, body: dict | None = None, timeout: int = _TIMEOUT) -> dict:
    url = _BASE + path
    headers = {"x-pilot-token": _pilot_token()}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return {"error": json.loads(raw).get("error", raw[:200])}
        except Exception:
            return {"error": f"HTTP {e.code}: {raw[:200]}"}
    except Exception as e:
        return {"error": str(e)}


# ── Parsing what the user said ────────────────────────────────────────────────

_URL_RE = re.compile(r"https?://[^\s]+")


def _strip_url(text: str) -> str:
    """Remove the target URL but KEEP the punctuation that followed it.

    The URL pattern swallows a trailing colon, and that colon is what marks
    where the prompt list starts — drop it and "feed these prompts into
    https://claude.ai/new: first one" parses the instruction text as prompt #1.
    """
    def repl(m: re.Match) -> str:
        u = m.group(0)
        return " " + u[len(u.rstrip(".,;:!?)")):]
    return _URL_RE.sub(repl, text)


def _resolve_target(task: str) -> tuple[str, str]:
    """(label, url). An explicit URL in the task wins over an alias."""
    low = task.lower()
    m = _URL_RE.search(task)
    if m:
        # Trailing punctuation is sentence structure, not part of the URL:
        # "...into https://claude.ai/new: first prompt" must not target ".../new:".
        url = m.group(0).rstrip(".,;:!?)")
        return url, url
    for key, (url, aliases) in _TARGETS.items():
        if any(a in low for a in aliases):
            return key, url
    return "gemini", _TARGETS["gemini"][0]


def _parse_prompts(task: str) -> list[str]:
    """Pull the prompt list out of free text.

    Handles the shapes people actually produce, most-explicit first so a quoted
    list isn't shredded by the line splitter:
      • quoted strings
      • a numbered/bulleted list — on separate lines OR run together on one line,
        because dictation produces "1. do this 2. then that" as a single line
      • one per line after a colon

    The target URL is stripped first: it contains the "://" and any trailing
    colon that the list splitter would otherwise treat as structure.
    """
    task = _strip_url(task)

    quoted = re.findall(r'"([^"]{4,})"', task) or re.findall(r"“([^”]{4,})”", task)
    if len(quoted) >= 2:
        return [q.strip() for q in quoted]

    body = task
    m = re.search(r":\s*(.+)", task, re.S)
    if m:
        body = m.group(1)
    elif ":" in task:
        # "feed these prompts into gemini:" with nothing after the colon — the
        # preamble is a command, not a prompt. Without this the whole command
        # sentence gets typed into the tool as prompt #1.
        return []

    # Line-anchored list first (keeps "3.5 stars" inside a line intact).
    numbered = re.findall(r"(?:^|\n)\s*(?:\d+[.)]|[-•*])\s*(.+)", body)
    if len(numbered) >= 2:
        return [n.strip() for n in numbered if n.strip()]

    # Run-together numbering: "1. shot one 2. shot two". Require the marker to
    # follow whitespace and precede a space, so decimals and "v2." survive.
    inline = re.split(r"(?:(?<=\s)|^)\d+[.)]\s+", body)
    inline = [p.strip(" -•*\t\n") for p in inline]
    inline = [p for p in inline if len(p) >= 4]
    if len(inline) >= 2:
        return inline

    lines = [ln.strip(" -•*\t") for ln in body.splitlines()]
    lines = [ln for ln in lines if len(ln) >= 4]
    if len(lines) >= 2:
        return lines

    if len(quoted) == 1:
        return [quoted[0].strip()]
    # A single trailing instruction is still a valid one-item run.
    cleaned = body.strip()
    return [cleaned] if len(cleaned) >= 4 else []


# ── Finding the box to type into ──────────────────────────────────────────────

def _find_input(elements: list[dict]) -> dict | None:
    """The prompt box: an input-ish element, preferring one whose name sounds
    like a prompt field, then the largest (prompt boxes are the big one)."""
    def area(e):
        b = e.get("bbox") or {}
        return (b.get("width") or 0) * (b.get("height") or 0)

    cands = [e for e in elements
             if e.get("role") in _INPUT_ROLES and area(e) > 0
             and not (e.get("attrs") or {}).get("disabled")]
    if not cands:
        return None
    named = [e for e in cands
             if any(w in str(e.get("name", "")).lower() for w in _PROMPT_WORDS)]
    pool = named or cands
    return max(pool, key=area)


def _center(el: dict) -> tuple[float, float]:
    b = el.get("bbox") or {}
    return (b.get("left", 0) + b.get("width", 0) / 2,
            b.get("top", 0) + b.get("height", 0) / 2)


def _element_count(snap: dict) -> int:
    """Cheap 'has the page changed?' signal without scraping content: these
    tools all add elements (copy/regenerate/share controls) as a reply lands."""
    return len(snap.get("elements") or [])


def _wait_for_reply(before: int, wait_s: float) -> bool:
    """Wait until the tool has both CHANGED the page and gone quiet again.

    The first change is not the answer — it fires the instant the send button
    swaps to a stop button. Returning then meant typing the next prompt into a
    box that hadn't cleared yet, and prompts interleaved mid-sentence:
      "now name one in Nice, onenow one in Ibiza, one word only word only"
    So: wait for a change, THEN for two consecutive identical reads.
    """
    deadline = time.time() + wait_s
    changed = False
    last = before
    stable = 0
    while time.time() < deadline:
        time.sleep(_SETTLE_POLL_S)
        now = _element_count(_req("/snapshot"))
        if not changed:
            if now != before:
                changed = True
                last = now
            continue
        if now == last:
            stable += 1
            if stable >= _STABLE_READS:
                return True
        else:
            stable = 0
            last = now
    return changed


# ── The run ───────────────────────────────────────────────────────────────────

def _wait_for_input(timeout_s: float = _INPUT_WAIT_S) -> dict | None:
    """Poll until the prompt box exists.

    First paint is not readiness. Pilot's navigate returns as soon as the page
    has ANY interactive element — on Gemini that's the nav header, while the
    chat input mounts a couple of seconds later. Checking once meant all the
    prompts failed instantly with "no prompt box", within a second of loading.
    """
    deadline = time.time() + timeout_s
    while True:
        snap = _req("/snapshot")
        if not snap.get("error"):
            box = _find_input(snap.get("elements") or [])
            if box:
                return box
        if time.time() >= deadline:
            return None
        time.sleep(_SETTLE_POLL_S)


def _send_one(prompt: str, wait_s: float) -> str:
    box = _wait_for_input()
    if not box:
        return "no prompt box appeared (sign-in needed?)"

    x, y = _center(box)
    r = _req("/click_xy", {"x": x, "y": y})
    if r.get("error"):
        return f"couldn't click the prompt box ({r['error']})"

    # Clear whatever is in the box before typing. Never trust it to be empty:
    # if the previous prompt hasn't been consumed yet, typing appends at the
    # caret and the two prompts fuse into one line of nonsense. Select-all +
    # Backspace is the one move that works for both <textarea> and the
    # contenteditable divs these tools actually use.
    _req("/key", {"key": "Meta+A"})
    _req("/key", {"key": "Backspace"})

    before = _element_count(_req("/snapshot"))

    r = _req("/key", {"text": prompt})
    if r.get("error"):
        return f"couldn't type ({r['error']})"

    r = _req("/key", {"key": "Enter"})
    if r.get("error"):
        return f"couldn't submit ({r['error']})"

    return "sent, answered" if _wait_for_reply(before, wait_s) \
        else "sent (still working when we moved on)"


def run(task: str, app: str = "", ctx: str = "") -> str:
    task = (task or "").strip()
    if not task:
        return ('Give me the prompts. e.g. "feed these prompts into Google Flow: '
                '1. a drone shot over Marbella at golden hour 2. the same shot at night"')

    health = _req("/health", timeout=8)
    if health.get("error"):
        return (f"Pilot isn't reachable on :8094 ({health['error']}). "
                f"Start it with: pm2 restart pilot-runner")

    label, url = _resolve_target(task)
    prompts = _parse_prompts(task)
    if not prompts:
        return "I couldn't find any prompts in that. Put them on separate lines, or number them."

    dropped = 0
    if len(prompts) > _MAX_PROMPTS:
        dropped = len(prompts) - _MAX_PROMPTS
        prompts = prompts[:_MAX_PROMPTS]

    nav = _req("/navigate", {"url": url}, timeout=60)
    if nav.get("error"):
        return f"Couldn't open {label} ({nav['error']})."
    if not nav.get("element_count"):
        return (f"{label} opened but rendered nothing to type into. "
                f"It may need a sign-in — open the Pilot tab and sign in once.")

    lines = [f"Feeding {len(prompts)} prompt(s) into {label}, one at a time. "
             f"Watch it in the Pilot tab."]
    for i, p in enumerate(prompts, 1):
        outcome = _send_one(p, _DEFAULT_WAIT_S)
        short = p if len(p) <= 58 else p[:55] + "…"
        lines.append(f"  {i}. {short} — {outcome}")

    if dropped:
        lines.append(f"  (stopped at {_MAX_PROMPTS}; {dropped} more not sent — "
                     f"send them in a second batch)")
    lines.append(f"\nAll of it happened in the Pilot browser at {url} — "
                 f"hit Record before you run it again and CODEC compiles the "
                 f"whole sequence into a reusable skill.")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    print(run(" ".join(sys.argv[1:]) or "feed these prompts into gemini: hello there\nand goodbye"))
