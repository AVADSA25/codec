"""Phase 1 Step 4 — self_improve as a plugin.

Replaces the nightly polling cycle in codec_self_improve.py with
event-driven gap detection via plugin lifecycle hooks. Drafted
proposals continue to land in ~/.codec/skill_proposals/YYYY-MM-DD/
exactly as they did before — only the trigger changes.

────────────────────────────────────────────────────────────────────────
HOW IT WORKS
────────────────────────────────────────────────────────────────────────

post_tool, on_error           In-memory ring buffer (last 200 signals)
       │                       — captures (tool, outcome, error_type)
       │                       — observe-only, never mutates result
       ▼
on_operation_end              Snapshot buffer → run codec_self_improve
       │                       ._find_gaps(snapshot, existing_skills)
       │                       — same threshold logic as nightly run
       │                       — three gap kinds: missing_tool,
       │                         unreliable_tool, timeout_prone
       ▼
   Per-tool throttle           Skip gaps whose tool was drafted in the
       │                       last 30 minutes (prevents Qwen spam if
       │                       the same gap fires repeatedly)
       ▼
   threading.Thread(daemon)    LLM draft + write run in background.
       │                       on_operation_end returns immediately so
       │                       the user's operation isn't blocked by
       │                       the ~2-min Qwen call.
       ▼
codec_self_improve.            Same _draft_skill + _validate +
   _draft_skill                 _write_proposal flow as nightly run.
   _validate                    Same dangerous-pattern gate. Same
   _write_proposal              MAX_PROPOSALS_PER_RUN=3 cap (per
                                analysis pass, not per session).

────────────────────────────────────────────────────────────────────────
INSTALL
────────────────────────────────────────────────────────────────────────

    cp ~/codec-repo/plugins/self_improve.py ~/.codec/plugins/self_improve.py
    pm2 restart codec-dashboard codec-mcp-http open-codec
    # (the plugin loads on the next operation in any process that uses
    # codec_hooks; the AST scan runs at process startup)

────────────────────────────────────────────────────────────────────────
KILL SWITCH
────────────────────────────────────────────────────────────────────────

    SELF_IMPROVE_PLUGIN_ENABLED=false   (default: true)
    # All hooks become no-ops. Buffer stops growing. No drafts.
    # Kill via env var on PM2 restart — does NOT require uninstalling
    # the plugin file.

────────────────────────────────────────────────────────────────────────
SAFETY / RECURSION GUARDS
────────────────────────────────────────────────────────────────────────

1. skips tool_name in _SELF_TOOLS — never analyzes signals from the
   self_improve skill itself OR from empty tool_name (operation hooks).
2. dangerous-code gate identical to codec_self_improve._validate.
3. all I/O wrapped in try/except — plugin failures NEVER bubble up.
   codec_hooks emits hook_error level=warning if we raise.
4. background drafter thread is daemon=True — process exit kills it.
5. throttle entry written BEFORE the LLM call (reserve-then-attempt)
   so a slow Qwen response doesn't permit a parallel duplicate draft.
6. NO osascript, NO subprocess, NO Apple Reminders / Notes / Calendar —
   per the 2026-05-01 incident contract.

────────────────────────────────────────────────────────────────────────
COEXISTENCE WITH NIGHTLY POLLING
────────────────────────────────────────────────────────────────────────

codec_self_improve.run_once() is unchanged. It can still be invoked
directly (CLI: `python3 codec_self_improve.py`, skill: `self_improve`,
or a future autopilot trigger). The plugin path is ADDITIVE — both
paths share the same _find_gaps / _draft_skill / _write_proposal
helpers and write to the same ~/.codec/skill_proposals/YYYY-MM-DD/.

If you set SELF_IMPROVE_PLUGIN_ENABLED=false you get the legacy
nightly-only behavior unchanged. If you remove the plugin file from
~/.codec/plugins/ you also revert to legacy.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone

# Plugin metadata (per codec_hooks _extract_metadata via AST).
PLUGIN_NAME = "self_improve"
PLUGIN_DESCRIPTION = (
    "Event-driven skill-gap proposal drafter. Replaces the nightly "
    "polling cycle in codec_self_improve.py — same proposals, "
    "different trigger."
)
PLUGIN_PRIORITY = 200          # low priority — observe-only mostly,
                               # other plugins should run first
PLUGIN_TOOL_FILTER = None      # apply to all tools

# ── Resolve codec-repo on sys.path so we can import codec_self_improve ────────
# The plugin runs from ~/.codec/plugins/. codec_hooks loads via
# importlib.spec_from_file_location which puts the plugin's parent dir
# on sys.path; we add the repo root explicitly.
_CODEC_REPO_CANDIDATES = (
    os.path.expanduser("~/codec-repo"),
    "/Users/mickaelfarina/codec-repo",   # explicit fallback for installed env
)
for _candidate in _CODEC_REPO_CANDIDATES:
    if os.path.isdir(_candidate) and _candidate not in sys.path:
        sys.path.insert(0, _candidate)
        break

# Lazy-imported on first hook fire to keep startup cheap and to avoid
# crashing the AST scan if codec_self_improve is moved.
_helpers_loaded = False
_find_gaps = None
_draft_skill = None
_validate = None
_write_proposal = None
_existing_skill_names = None
_PROPOSALS_ROOT = None
_GAP_KIND_TO_SIGNAL = None
_log_event = None


def _load_helpers() -> bool:
    """Lazy-load codec_self_improve helpers. Returns True on success.

    Idempotent — sets module-level globals on first call.
    """
    global _helpers_loaded, _find_gaps, _draft_skill, _validate
    global _write_proposal, _existing_skill_names, _PROPOSALS_ROOT
    global _GAP_KIND_TO_SIGNAL, _log_event
    if _helpers_loaded:
        return True
    try:
        from codec_self_improve import (
            _find_gaps as _fg,
            _draft_skill as _ds,
            _validate as _v,
            _write_proposal as _wp,
            _existing_skill_names as _esn,
            _PROPOSALS_ROOT as _pr,
            _GAP_KIND_TO_SIGNAL as _gks,
        )
        from codec_audit import log_event as _le
    except Exception:
        return False
    _find_gaps = _fg
    _draft_skill = _ds
    _validate = _v
    _write_proposal = _wp
    _existing_skill_names = _esn
    _PROPOSALS_ROOT = _pr
    _GAP_KIND_TO_SIGNAL = _gks
    _log_event = _le
    _helpers_loaded = True
    return True


# ── In-memory ring buffer ────────────────────────────────────────────────────
_BUFFER_SIZE = 200
_signals: "deque[dict]" = deque(maxlen=_BUFFER_SIZE)
_signals_lock = threading.Lock()

# ── Per-tool throttle ────────────────────────────────────────────────────────
# Maps tool_name → epoch seconds of last drafted proposal. A tool whose
# last draft was less than _THROTTLE_SECONDS ago is skipped on the next
# on_operation_end pass.
_THROTTLE_SECONDS = 30 * 60      # 30 min per tool
_last_draft: "dict[str, float]" = {}
_throttle_lock = threading.Lock()

# ── Self-recursion guard ─────────────────────────────────────────────────────
# Tools whose post_tool / on_error fires we IGNORE. self_improve is the
# obvious one; "" is the operation-hook empty case.
_SELF_TOOLS = frozenset({"self_improve", ""})


# ── Feature flag ─────────────────────────────────────────────────────────────
def _enabled() -> bool:
    """Read SELF_IMPROVE_PLUGIN_ENABLED env var. Default true. Read each
    call so PM2 restart with a different env value takes effect without
    reinstalling the plugin."""
    val = (os.environ.get("SELF_IMPROVE_PLUGIN_ENABLED") or "true").strip().lower()
    return val not in ("false", "0", "no", "off")


# ── Hook: post_tool ──────────────────────────────────────────────────────────
def post_tool(ctx, result):
    """Capture every successful tool fire as a signal. Returns None
    (observe-only — never mutates result).

    Heuristic outcome detection: post_tool only fires on success path
    (raises go through on_error), so default outcome="ok". If the
    result string starts with a clear failure marker, mark as "error"
    so codec_self_improve._find_gaps can count it toward the
    unreliable_tool threshold.
    """
    if not _enabled():
        return None
    tool = (ctx.tool_name or "")
    if tool in _SELF_TOOLS:
        return None
    sig = {
        "ts": ctx.timestamp_utc,
        "tool": tool,
        "outcome": "ok",
        "error_type": None,
        "error": None,
    }
    if isinstance(result, str):
        low = result.lower()[:80]
        if "failed:" in low or low.startswith(("error", "failed", "could not")):
            sig["outcome"] = "error"
            sig["error_type"] = "ResultStringError"
    with _signals_lock:
        _signals.append(sig)
    return None


# ── Hook: on_error ───────────────────────────────────────────────────────────
def on_error(ctx, exc):
    """Capture exceptions raised by the wrapped invoke. Stronger error
    signal than a result string — codec_self_improve treats outcome=
    "error" as evidence for unreliable_tool gap detection."""
    if not _enabled():
        return
    tool = (ctx.tool_name or "")
    if tool in _SELF_TOOLS:
        return
    err_type = type(exc).__name__
    err_msg = str(exc)[:500]
    # Map specific exception types to richer signals.
    outcome = "timeout" if "timeout" in err_type.lower() else "error"
    with _signals_lock:
        _signals.append({
            "ts": ctx.timestamp_utc,
            "tool": tool,
            "outcome": outcome,
            "error_type": err_type,
            "error": err_msg,
        })


# ── Hook: on_operation_end ───────────────────────────────────────────────────
def on_operation_end(ctx):
    """Operation finished. Snapshot buffer → analyze → spawn draft thread
    if any threshold breached. Returns immediately; the LLM call runs in
    a daemon thread.

    Throttle: a tool is eligible for re-drafting only after
    _THROTTLE_SECONDS has elapsed since its last draft (prevents Qwen
    from being hammered if the same gap fires every operation).

    Spawns at most one drafter thread per call. The drafter handles all
    eligible gaps sequentially (matches codec_self_improve.run_once's
    sequential draft pattern).
    """
    if not _enabled():
        return
    if not _load_helpers():
        return
    # Snapshot buffer (avoid holding lock during analysis).
    with _signals_lock:
        snapshot = list(_signals)
    if not snapshot:
        return
    try:
        existing = _existing_skill_names()
    except Exception:
        existing = set()
    try:
        gaps = _find_gaps(snapshot, existing)
    except Exception:
        return
    if not gaps:
        return
    # Throttle filter: keep only gaps whose tool hasn't been drafted in
    # the last _THROTTLE_SECONDS. Reserve the slot now (set _last_draft
    # before the LLM call) so a parallel on_operation_end can't race a
    # duplicate draft for the same tool.
    now = time.time()
    eligible = []
    with _throttle_lock:
        for g in gaps:
            tool = g.get("tool", "")
            last = _last_draft.get(tool, 0)
            if now - last >= _THROTTLE_SECONDS:
                eligible.append(g)
                _last_draft[tool] = now
    if not eligible:
        return
    t = threading.Thread(
        target=_draft_and_write,
        args=(eligible, ctx.correlation_id),
        daemon=True,
        name="self_improve_drafter",
    )
    t.start()


# ── Background drafter ───────────────────────────────────────────────────────
def _draft_and_write(gaps, correlation_id):
    """Run codec_self_improve._draft_skill + _write_proposal for each gap.

    Identical to the loop body inside codec_self_improve.run_once. Emits
    one skill_proposal_staged audit event per drafted proposal. Trigger
    field "plugin_hook" distinguishes from the legacy "nightly_run"
    trigger so audit_report can break them out by source.

    Never raises — wraps each iteration in try/except. Failure to draft
    one gap does not block the next.
    """
    if not _load_helpers():
        return
    target_date = datetime.now(timezone.utc).date().isoformat()
    out_dir = _PROPOSALS_ROOT / target_date
    try:
        existing = _existing_skill_names()
    except Exception:
        existing = set()
    for g in gaps:
        try:
            drafted = _draft_skill(g)
            if drafted is None:
                continue
            name, code, raw = drafted
            if name == "__unparseable__":
                try:
                    out_dir.mkdir(parents=True, exist_ok=True)
                    dbg = out_dir / f"__unparseable_plugin_{int(time.time())}.txt"
                    dbg.write_text(f"Gap: {g}\n\n--- RAW ---\n{raw}\n")
                except Exception:
                    pass
                continue
            if name in existing:
                name = f"{name}_v2"
            ok, why = _validate(code)
            _write_proposal(out_dir, name, code, g, ok, why)
            try:
                signal_type = _GAP_KIND_TO_SIGNAL.get(g.get("kind", ""), "unknown")
                _log_event(
                    "skill_proposal_staged", "codec-self-improve",
                    f"Proposal staged via plugin: {name}",
                    outcome="ok" if ok else "warning",
                    level="info" if ok else "warning",
                    extra={
                        "proposal_path": str((out_dir / f"{name}.md").resolve()),
                        "skill_name": name,
                        "signal_type": signal_type,
                        "validation_passed": ok,
                        "validation_reason": (why or "clean")[:200],
                        "target_date": target_date,
                        "trigger": "plugin_hook",
                    },
                    correlation_id=correlation_id,
                )
            except Exception:
                pass
        except Exception:
            # NEVER let a single gap's failure break the loop.
            continue


# ── Test surface ─────────────────────────────────────────────────────────────
# Internal helpers exposed for tests/test_self_improve_plugin.py. Not
# part of the codec_hooks contract — codec_hooks only calls the named
# hook functions above.

def _reset_state_for_test():
    """Clear the in-memory buffer + throttle. Used only by tests."""
    with _signals_lock:
        _signals.clear()
    with _throttle_lock:
        _last_draft.clear()


def _set_throttle_seconds_for_test(seconds: float):
    """Override _THROTTLE_SECONDS for a test. Restores via the
    fixture's monkeypatch teardown."""
    global _THROTTLE_SECONDS
    _THROTTLE_SECONDS = seconds


def _get_signals_snapshot_for_test():
    """Return a copy of the buffer for assertion."""
    with _signals_lock:
        return list(_signals)
