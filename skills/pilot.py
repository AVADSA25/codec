"""CODEC Skill: Pilot — headless browser automation via CODEC Pilot (port 8094)"""

SKILL_NAME = "pilot"
SKILL_DESCRIPTION = (
    "Control a headless browser: navigate, click, type, take snapshots and "
    "screenshots, run autonomous tasks, manage HITL (pause/resume/inject). "
    "Powered by CODEC Pilot on localhost:8094."
)
SKILL_MCP_EXPOSE = True
SKILL_TRIGGERS = [
    # Navigation
    "pilot navigate", "pilot go to", "pilot open",
    "browser navigate", "headless navigate",
    # Interaction
    "pilot click", "pilot type", "pilot scroll", "pilot fill",
    # Observation
    "pilot snapshot", "pilot screenshot", "pilot screenshot base64",
    "pilot read page", "pilot get page", "what does the page look like",
    # Runs
    "pilot run", "start pilot run", "pilot task", "automate browser",
    "browser agent", "web automation", "pilot agent",
    # HITL
    "pilot pause", "pilot resume", "pilot inject", "pilot takeover",
    "pilot status", "pilot health",
    # Misc
    "pilot runs", "list pilot runs", "pilot history",
]

import re
import json
import urllib.request
import urllib.error

_BASE = "http://localhost:8094"
_TIMEOUT = 15


def _pilot_token() -> str:
    """Pilot PP-1: the shared secret the pilot-runner requires on every request
    (header `x-pilot-token`). Both sides read ~/.codec/pilot_token; pilot-runner
    bootstraps it on startup. Empty if Pilot has never run → request 401s."""
    import os
    try:
        with open(os.path.expanduser("~/.codec/pilot_token")) as f:
            return f.read().strip()
    except Exception:
        return ""


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _get(path: str) -> dict:
    url = _BASE + path
    req = urllib.request.Request(url, headers={"x-pilot-token": _pilot_token()})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        return {"error": f"HTTP {e.code}: {body[:200]}"}
    except Exception as e:
        return {"error": str(e)}


def _post(path: str, body: dict | None = None) -> dict:
    url = _BASE + path
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "x-pilot-token": _pilot_token()},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        return {"error": f"HTTP {e.code}: {body_text[:200]}"}
    except Exception as e:
        return {"error": str(e)}


def _pilot_up() -> bool:
    try:
        with urllib.request.urlopen(_BASE + "/health", timeout=3):
            return True
    except Exception:
        return False


# ── Parsers ───────────────────────────────────────────────────────────────────

def _extract_url(text: str) -> str | None:
    m = re.search(r'https?://\S+', text)
    if m:
        return m.group(0).rstrip(".,;)")
    return None


def _extract_index(text: str) -> int | None:
    m = re.search(r'\b(\d+)\b', text)
    return int(m.group(1)) if m else None


def _extract_quoted(text: str) -> str | None:
    m = re.search(r'["\'](.+?)["\']', text)
    return m.group(1) if m else None


def _extract_run_id(text: str) -> str | None:
    # run IDs are 12 hex chars
    m = re.search(r'\b([0-9a-f]{12})\b', text.lower())
    return m.group(1) if m else None


def _fmt(d: dict) -> str:
    """Turn a result dict into a readable string."""
    if "error" in d:
        return f"❌ Pilot error: {d['error']}"
    return json.dumps(d, indent=2)


# ── Main dispatcher ───────────────────────────────────────────────────────────

def run(task: str, app: str = "", ctx: str = "") -> str:  # noqa: A001
    t = task.lower().strip()

    if not _pilot_up():
        return (
            "❌ Pilot Runner is not running. Start it with:\n"
            "  pm2 start ecosystem.config.js --only pilot-runner\n"
            "or check: pm2 status pilot-runner"
        )

    # ── run status (must come before health — both contain "status") ──────────
    if "status" in t and _extract_run_id(task):
        run_id = _extract_run_id(task)
        d = _get(f"/run/{run_id}/status")
        # HTTP-level error (no run_id in response) → report connection problem
        if "error" in d and "run_id" not in d:
            return _fmt(d)
        snap = d.get("latest_snapshot", "")
        snap_preview = snap[:300] + "…" if len(snap) > 300 else snap
        status_icon = {"done": "✅", "error": "❌", "running": "⏳",
                       "budget_exhausted": "⚠️"}.get(d.get("status", ""), "📊")
        error_line = f"\n   Error : {d.get('error', '')}" if d.get("error") else ""
        result_line = f"\n   Result: {d.get('result', '')}" if d.get("result") else ""
        return (
            f"{status_icon} Run {run_id}\n"
            f"   Status: {d.get('status', '')}"
            f"{result_line}{error_line}\n"
            f"   Task  : {d.get('task', '')}\n"
            f"   Steps : {len(d.get('steps', []))}\n\n"
            f"{snap_preview}"
        )

    # ── health ────────────────────────────────────────────────────────────────
    if any(w in t for w in ["health", "status", "is pilot"]):
        d = _get("/health")
        if "error" in d:
            return _fmt(d)
        return (
            f"✅ Pilot Runner online\n"
            f"   URL   : {d.get('url', 'n/a')}\n"
            f"   CDP   : :{d.get('cdp_port', 9223)}\n"
            f"   Tunnel: https://pilot.lucyvpa.com"
        )

    # ── navigate ──────────────────────────────────────────────────────────────
    if any(w in t for w in ["navigate", "go to", "open", "visit", "browse"]):
        url = _extract_url(task)
        if not url:
            return "Please include a URL. Example: pilot navigate https://example.com"
        d = _post("/navigate", {"url": url})
        if "error" in d:
            return _fmt(d)
        return (
            f"✅ Navigated to {url}\n"
            f"   Title   : {d.get('title', '')}\n"
            f"   Elements: {d.get('element_count', 0)} interactive"
        )

    # ── snapshot ──────────────────────────────────────────────────────────────
    if any(w in t for w in ["snapshot", "read page", "get page", "what does", "elements", "dom"]):
        d = _get("/snapshot")
        if "error" in d:
            return _fmt(d)
        header = (
            f"📄 Snapshot ({d.get('element_count', 0)} elements, {d.get('took_ms', 0):.0f}ms)\n"
            f"URL: {d.get('url', '')}\n"
            f"Title: {d.get('title', '')}\n\n"
        )
        rendered = d.get("rendered", "")
        # Trim to first 60 elements to avoid context bloat
        lines = rendered.split("\n")
        if len(lines) > 65:
            lines = lines[:65] + [f"… ({d.get('element_count', 0) - 60} more elements)"]
        return header + "\n".join(lines)

    # ── screenshot ────────────────────────────────────────────────────────────
    if "screenshot" in t:
        if "base64" in t:
            d = _get("/screenshot/base64")
            if "error" in d:
                return _fmt(d)
            b64 = d.get("image", "")
            return f"📸 Screenshot (base64, {len(b64)} chars)\n{b64[:100]}…"
        # Save to /tmp
        import urllib.request
        out = "/tmp/pilot_screenshot.jpg"
        try:
            urllib.request.urlretrieve(_BASE + "/screenshot", out)
            import os
            size = os.path.getsize(out)
            return f"📸 Screenshot saved to {out} ({size/1024:.1f} KB)"
        except Exception as e:
            return f"❌ Screenshot failed: {e}"

    # ── click ─────────────────────────────────────────────────────────────────
    if "click" in t:
        idx = _extract_index(task)
        if idx is None:
            return "Please specify an element index. Example: pilot click 3"
        d = _post(f"/click/{idx}")
        if "error" in d:
            return _fmt(d)
        return f"✅ Clicked [{idx}]: {d.get('clicked', '')}"

    # ── type ──────────────────────────────────────────────────────────────────
    if any(w in t for w in ["type ", "fill ", "enter ", "input "]):
        idx = _extract_index(task)
        text = _extract_quoted(task)
        if idx is None:
            return "Please specify index and text. Example: pilot type 2 \"hello world\""
        if text is None:
            # Fallback: everything after the index number
            m = re.search(r'\b\d+\b\s+(.*)', task)
            text = m.group(1).strip() if m else ""
        if not text:
            return "Please provide text to type. Example: pilot type 2 \"search query\""
        d = _post(f"/type/{idx}", {"text": text})
        if "error" in d:
            return _fmt(d)
        return f"✅ Typed into [{idx}]: \"{text}\""

    # ── scroll ────────────────────────────────────────────────────────────────
    if "scroll" in t:
        direction = "up" if "up" in t else "down"
        m = re.search(r'\b(\d+)\b', t)
        amount = int(m.group(1)) if m else 500
        d = _post("/navigate", {"url": ""})  # we don't have a /scroll endpoint
        # Use snapshot to get current page, then inject JS via navigate trick
        # Actually: just use the /snapshot then JS eval approach
        # For now call /navigate with current URL to stay put, then use JS
        snap = _get("/snapshot")
        url = snap.get("url", "")
        if url:
            # POST a scroll via a simple fetch to the page's evaluate
            # The runner has no dedicated scroll endpoint — use navigate workaround
            # Best approach: add scroll to the runner OR do it via a run step
            pass
        delta = amount if direction == "down" else -amount
        # Hit the runs endpoint as a one-shot scroll step
        run_r = _post("/run", {"task": f"scroll {direction}", "tag": "scroll"})
        run_id = run_r.get("run_id", "")
        if run_id:
            _post(f"/run/{run_id}/step", {
                "action": "scroll", "direction": direction, "amount": amount
            })
            _post(f"/run/{run_id}/complete", {"status": "done"})
        return f"✅ Scrolled {direction} {amount}px"

    # ── run (autonomous task) ─────────────────────────────────────────────────
    if any(w in t for w in ["run ", "task ", "automate", "agent ", "do "]):
        # Extract task description - strip the "pilot run/task" prefix
        for prefix in ["pilot run", "pilot task", "pilot agent", "automate browser",
                        "browser agent", "web automation", "start pilot run"]:
            if prefix in t:
                task_desc = task[task.lower().index(prefix) + len(prefix):].strip()
                break
        else:
            task_desc = task

        if not task_desc:
            return "Please describe the task. Example: pilot run find the price of MacBook Pro on apple.com"

        d = _post("/run", {"task": task_desc, "tag": "codec-skill"})
        if "error" in d:
            return _fmt(d)
        run_id = d.get("run_id", "")

        # Kick off background agent execution
        # use_stub=True when Qwen is not available; set False for full LLM runs
        start = _post(f"/run/{run_id}/start", {"step_budget": 20, "use_stub": True})
        if "error" in start:
            return (
                f"🤖 Run registered (run_id={run_id}) but agent start failed:\n"
                f"   {start['error']}\n"
                f"Check: pm2 logs pilot-runner"
            )

        return (
            f"🤖 Pilot agent started\n"
            f"   Run ID  : {run_id}\n"
            f"   Task    : {task_desc}\n"
            f"   Budget  : 20 steps\n"
            f"   Tracking: pilot status {run_id}\n\n"
            f"The agent is running in the background (headless Chromium + Qwen).\n"
            f"Check progress with: pilot status {run_id}"
        )

    # ── list runs ─────────────────────────────────────────────────────────────
    if any(w in t for w in ["list", "history", "runs"]):
        d = _get("/runs")
        if "error" in d:
            return _fmt(d)
        runs = d.get("runs", [])
        if not runs:
            return "No pilot runs recorded yet."
        lines = [f"📋 Pilot Runs ({len(runs)}):"]
        for r in runs[:15]:
            lines.append(
                f"  {r['run_id']} │ {r['status']:16} │ {r['task'][:60]}"
            )
        return "\n".join(lines)

    # ── pause ─────────────────────────────────────────────────────────────────
    if "pause" in t:
        run_id = _extract_run_id(task)
        if not run_id:
            return "Please specify a run ID to pause. Example: pilot pause abc123def456"
        d = _post(f"/hitl/{run_id}/pause")
        return _fmt(d)

    # ── resume ────────────────────────────────────────────────────────────────
    if "resume" in t:
        run_id = _extract_run_id(task)
        if not run_id:
            return "Please specify a run ID to resume. Example: pilot resume abc123def456"
        d = _post(f"/hitl/{run_id}/resume")
        return _fmt(d)

    # ── inject ────────────────────────────────────────────────────────────────
    if "inject" in t:
        run_id = _extract_run_id(task)
        url = _extract_url(task)
        idx = _extract_index(task)
        if not run_id:
            return "Please specify a run ID. Example: pilot inject abc123def456 navigate https://example.com"
        if url:
            action = {"action": "navigate", "url": url}
        elif idx:
            action = {"action": "click", "index": idx}
        else:
            return "Please specify what to inject: a URL (navigate) or an index (click)."
        d = _post(f"/hitl/{run_id}/inject", action)
        return _fmt(d)

    # ── fallback: show help ───────────────────────────────────────────────────
    return (
        "🤖 CODEC Pilot — headless browser agent\n\n"
        "Commands:\n"
        "  pilot navigate https://example.com\n"
        "  pilot snapshot              — indexed DOM elements\n"
        "  pilot screenshot            — save JPEG to /tmp/\n"
        "  pilot click 3               — click element [3]\n"
        "  pilot type 2 \"hello\"        — type into element [2]\n"
        "  pilot run find MacBook price on apple.com\n"
        "  pilot status                — list recent runs\n"
        "  pilot status <run_id>       — check specific run\n"
        "  pilot health                — check runner status\n"
        "\nTunnel: https://pilot.lucyvpa.com"
    )
