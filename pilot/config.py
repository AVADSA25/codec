"""
CODEC Pilot — Configuration
============================
All constants for the Pilot subsystem. Import from here instead of
scattering magic values across modules.
"""

from pathlib import Path

# ── Version ───────────────────────────────────────────────────────────────────
PILOT_VERSION = "2.0.0"  # bumped to 2.x when Phase 2 snapshot lands

# ── Paths ─────────────────────────────────────────────────────────────────────
PILOT_DIR = Path.home() / ".codec" / "pilot_chrome_profile"
PILOT_TRACES_DIR = Path.home() / ".codec" / "pilot_traces"
PILOT_SCRIPTS_DIR = Path.home() / ".codec" / "pilot_scripts"

# ── Ports ─────────────────────────────────────────────────────────────────────
CDP_PORT = 9223          # dedicated Pilot CDP port (user Chrome stays on 9222)
PILOT_API_PORT = 8094    # HTTP API served by Phase 3 pilot-runner
# PP-1 (audit P-1): bind loopback by default — NOT 0.0.0.0 — so the API isn't
# directly LAN-reachable. (Auth via x-pilot-token is the gate that also covers the
# Cloudflare tunnel, which connects from localhost.) Override only if you know why.
PILOT_API_HOST = "127.0.0.1"

# ── Snapshot ──────────────────────────────────────────────────────────────────
# Elements with these roles are considered "interactive" and indexed.
INTERACTIVE_ROLES = frozenset({
    "button",
    "link",
    "textbox",
    "searchbox",
    "combobox",
    "listbox",
    "checkbox",
    "radio",
    "switch",
    "tab",
    "menuitem",
    "menuitemcheckbox",
    "menuitemradio",
    "option",
    "slider",
    "spinbutton",
    "treeitem",
    "gridcell",
    "columnheader",
    "rowheader",
    "scrollbar",
})

# HTML tags that are always interactive regardless of ARIA role
ALWAYS_INTERACTIVE_TAGS = frozenset({
    "button",
    "a",
    "input",
    "select",
    "textarea",
})

# When True, only elements whose bounding box overlaps the viewport are indexed.
# Disable during testing on pages with important off-screen content.
SNAPSHOT_VIEWPORT_ONLY = True

# Maximum number of elements to include in a single snapshot (prevents context bloat).
SNAPSHOT_MAX_ELEMENTS = 150

# ── Agent loop ────────────────────────────────────────────────────────────────
DEFAULT_STEP_BUDGET = 40   # max actions per agent run before requesting continuation
DEFAULT_TIMEOUT_MS = 10_000  # Playwright action timeout

# ── Viewport ──────────────────────────────────────────────────────────────────
DEFAULT_VIEWPORT = (1280, 800)

# ── Async robustness (PP-12, audit P-14) ────────────────────────────────────────
# Max seconds a HITL-paused agent waits for resume before the run ends gracefully
# (status="paused_timeout") instead of pinning the browser + run slot forever.
HITL_PAUSE_TIMEOUT_S = 600.0
# Max consecutive screenshot failures on the /screenshot/stream MJPEG feed before the
# stream closes (a dead browser would otherwise spin the loop forever yielding nothing).
# At ~0.25s/frame, 20 ≈ 5s of dead frames before the client is told to reconnect.
MJPEG_MAX_CONSECUTIVE_FAILURES = 20
