"""CODEC Continuous Observation Loop (Phase 2 Step 5).

Background process that polls four cheap signals — frontmost window,
screenshot OCR, clipboard delta, recent file changes — and keeps the
last 10 minutes of state in a RAM-only ring buffer. On every chat /
voice request, an injection helper decides whether to prepend a
≤200-token summary to the LLM's system prompt, gated per the
"Observation injection contract" in docs/PHASE2-BLUEPRINT.md §X.

────────────────────────────────────────────────────────────────────────
Architecture
────────────────────────────────────────────────────────────────────────

  PM2 service `codec-observer`
        │
        ▼
   run_daemon()  ─loop──→ poll() ─append→ _GLOBAL_BUFFER (deque maxlen=N)
        │                                       │
        │                                       ▼
        │                              audit: observation_tick
        │                                  (METADATA-ONLY)
        │
        ▼  (concurrent reader)
   chat_completion / voice _pipeline
        │
        ▼
   maybe_inject_observation_summary(prompt, transport, skill_name)
        │
        ▼ (Q5 gating)
   summary_or_None  ─→  prepended to system prompt  +  audit:
                                                       observation_
                                                       summary_injected

────────────────────────────────────────────────────────────────────────
Privacy & safety contract
────────────────────────────────────────────────────────────────────────

1. RAM only. Buffer is `collections.deque(maxlen=N)`. Process restart
   (PM2 SIGTERM, crash, boot) wipes it. By design.
2. Audit emits are METADATA-ONLY — `observation_tick` carries lengths,
   counts, and content_type tags but NEVER the raw window title / OCR
   text / clipboard content / file path.
3. Injection is GATED for cloud transports (claude.ai, cloud-routed
   voice, cloud-routed chat). Local-Qwen transport always injects.
   MCP transport never injects (the MCP client brings its own context).
4. No new system permissions. Reads via existing skills (active_window,
   screenshot_text) and existing primitives (pbpaste, getmtime, Quartz).
5. Module import has NO side effects — no thread spawn, no poll fire.
   PM2 entry calls run_daemon() explicitly.

────────────────────────────────────────────────────────────────────────
Configuration (~/.codec/config.json: observer.{...})
────────────────────────────────────────────────────────────────────────

  cadence_active_s        60       Poll interval when user input < 60s ago
  cadence_idle_s          300      Poll interval when idle ≥ 60s
  idle_threshold_s        60       active vs idle classifier
  buffer_depth_min        10       Ring buffer time-depth in minutes
  ocr_timeout_ms          100      First OCR timeout
  ocr_retry_timeout_ms    200      Q5.1 — single retry timeout
  reset_on_long_idle      true     Wipe buffer after long idle resume
  reset_idle_threshold_s  1800     "Long idle" threshold (= 30 min)
  summary_max_tokens      200      Cap on injected summary
  poll_slow_threshold_ms  150      Q5.5 — emit observation_tick_slow above this
  stop_nouns              [...]    Q5.3 — stop-noun list for possessive regex

Kill switch: env var `OBSERVER_ENABLED` (default `true`). Setting `false`
disables polling AND injection. No separate injection kill switch.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# ── Audit emit ────────────────────────────────────────────────────────────────
# Lazy-import so module import doesn't pull in codec_audit at startup time.
# We DO want to fail loudly if audit is unavailable when we try to emit, but
# import-time failure is unfriendly for tests that monkeypatch.
from codec_audit import (
    OBSERVATION_TICK,
    OBSERVATION_TICK_SLOW,
    OBSERVATION_SUMMARY_INJECTED,
    OBSERVER_BUFFER_INSPECTED,
    log_event as _log_event,
)

log = logging.getLogger("codec_observer")

# ── Quartz import (idle detection) ────────────────────────────────────────────
# Idle seconds via CGEventSourceSecondsSinceLastEventType. Quartz lives in
# pyobjc and may not be available in test / CI environments — degrade
# gracefully (return 0.0 = "always active") so cadence stays at the active
# interval and we don't break tests on non-mac runners.
try:
    from Quartz import (  # type: ignore[import-not-found]
        CGEventSourceSecondsSinceLastEventType,
        kCGEventSourceStateHIDSystemState,
    )
    _HAS_QUARTZ = True
except ImportError:  # pragma: no cover — non-mac CI path
    _HAS_QUARTZ = False
    CGEventSourceSecondsSinceLastEventType = None  # type: ignore[assignment]
    kCGEventSourceStateHIDSystemState = None  # type: ignore[assignment]

# ── Config defaults ───────────────────────────────────────────────────────────
_DEFAULT_CONFIG: Dict[str, Any] = {
    "cadence_active_s": 60,
    "cadence_idle_s": 300,
    "idle_threshold_s": 60,
    "buffer_depth_min": 10,
    # OCR enable flag (2026-05-02 hotfix). Default True preserves Step 5
    # design behavior on machines where Screen Recording permission is
    # granted to the python3.13 process running codec-observer. Set false
    # to skip the screencapture+Vision call entirely — buffer still gets
    # active_window + clipboard + recent_files signals, just no OCR.
    # Flipping this to false is the recommended workaround for the macOS
    # popup-storm bug when permissions aren't granted to the PM2 child:
    # screencapture blocks subprocess.run waiting for the popup AND the
    # ThreadPoolExecutor's `with` exit waits for the thread to finish,
    # so each poll generates ~2 popups + ~5s of blocking until dismissal.
    # See ~/.codec/config.json:observer.ocr_enabled to override.
    "ocr_enabled": True,
    "ocr_timeout_ms": 100,
    "ocr_retry_timeout_ms": 200,         # Q5.1
    "reset_on_long_idle": True,
    "reset_idle_threshold_s": 1800,
    "summary_max_tokens": 200,
    "poll_slow_threshold_ms": 150,        # Q5.5
    # Q5.3 — possessive-without-context stop-noun list. Filters out generic
    # nouns that would create false positives for "my X" / "this Y" patterns.
    "stop_nouns": [
        "question", "time", "day", "week", "month", "year", "thing",
        "stuff", "way", "point", "idea", "problem", "issue", "plan",
        "file", "line", "error", "bug", "code", "function", "variable",
        "name", "list", "item", "value",
    ],
}

_CODEC_CONFIG_PATH = Path(os.path.expanduser("~/.codec/config.json"))


def _load_config() -> Dict[str, Any]:
    """Read ~/.codec/config.json:observer.{...}; merge over _DEFAULT_CONFIG."""
    cfg = dict(_DEFAULT_CONFIG)
    try:
        with open(_CODEC_CONFIG_PATH) as f:
            user = json.load(f).get("observer", {})
        if isinstance(user, dict):
            cfg.update(user)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return cfg


# ── Kill switch ───────────────────────────────────────────────────────────────
def _enabled() -> bool:
    """Read OBSERVER_ENABLED env var (default true). Read each call so PM2
    restart with a different env value takes effect."""
    val = (os.environ.get("OBSERVER_ENABLED") or "true").strip().lower()
    return val not in ("false", "0", "no", "off")


# ── Idle classifier (Q4) ──────────────────────────────────────────────────────
def _idle_seconds() -> float:
    """CGEventSourceSecondsSinceLastEventType for HID system state.
    Covers keyboard + mouse + trackpad + Apple Pencil. Returns 0.0 on
    non-mac platforms (degrades gracefully, treats as always-active)."""
    if not _HAS_QUARTZ:
        return 0.0
    try:
        # 4294967295 = ~kCGAnyInputEventType (all event types)
        return float(CGEventSourceSecondsSinceLastEventType(
            kCGEventSourceStateHIDSystemState, 4294967295))
    except Exception:
        return 0.0


# ── Polling primitives ────────────────────────────────────────────────────────
# Each primitive is independently testable + monkeypatchable. Bypasses
# run_with_hooks so observer's own polls don't trigger plugin cascades
# (especially the Step 4 self_improve plugin's post_tool capture).

def _get_active_window() -> Dict[str, Any]:
    """Returns {app, title, pid} or {} on failure."""
    try:
        # AppleScript via osascript — same primitive skills/active_window.py
        # uses, but we call directly to avoid wrapping in run_with_hooks.
        result = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to set frontApp to '
             'first application process whose frontmost is true\n'
             'tell frontApp to set winTitle to ""\n'
             'try\n'
             '    tell frontApp to set winTitle to name of front window\n'
             'end try\n'
             'tell frontApp to return (name as string) & "|" & '
             '(unix id as string) & "|" & winTitle'],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode != 0:
            return {}
        parts = (result.stdout or "").strip().split("|", 2)
        if len(parts) < 3:
            return {}
        return {
            "app": parts[0],
            "pid": int(parts[1]) if parts[1].isdigit() else 0,
            "title": parts[2],
        }
    except Exception as e:
        log.debug("active_window probe failed: %s", e)
        return {}


def _get_clipboard_now() -> str:
    """Current clipboard content via pbpaste. Returns "" on failure."""
    try:
        result = subprocess.run(
            ["pbpaste"], capture_output=True, text=True, timeout=2)
        return (result.stdout or "")
    except Exception as e:
        log.debug("pbpaste probe failed: %s", e)
        return ""


def _classify_clipboard_kind(text: str) -> str:
    """Classify clipboard content. Returns one of:
    "url" | "json" | "code" | "text" | "image_blob_redacted" | "empty".
    Q5.2: image clipboards are redacted, never OCR'd."""
    if not text:
        return "empty"
    # macOS pbpaste returns "" for image clipboards — but if we ever get
    # binary-like content sneaking through, redact.
    if any(ord(c) < 9 or (13 < ord(c) < 32) for c in text[:200]):
        return "image_blob_redacted"
    s = text.strip()
    if re.match(r"^https?://[^\s]+$", s.split("\n")[0]):
        return "url"
    try:
        json.loads(s)
        return "json"
    except (json.JSONDecodeError, ValueError):
        pass
    # Code heuristic: contains common syntax markers
    if any(marker in s for marker in ("def ", "function ", "import ", "class ",
                                       "const ", "{\n", "};", "</")):
        return "code"
    return "text"


def _get_screenshot_ocr(timeout_ms: int, retry_timeout_ms: int) -> Tuple[str, bool]:
    """OCR the current screen. Returns (ocr_text, was_skipped).
    Q5.1: retry once with longer timeout if first attempt times out.
    On non-mac or vision-unavailable env: returns ("", True)."""
    def _ocr_call(timeout_s: float) -> Optional[str]:
        try:
            # Lazy-import skills.screenshot_text — it pulls in vision libs.
            # Fail soft if unavailable.
            sys.path.insert(0, os.path.expanduser("~/.codec/skills"))
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/skills")
            # Note: we don't actually use a Python import here because the
            # skill triggers heavy imports. Instead, use the same primitive
            # the skill uses (screencapture + Vision via osascript), but
            # subject to a hard timeout.
            with ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(_screencapture_and_ocr_blocking)
                return future.result(timeout=timeout_s)
        except (FuturesTimeoutError, TimeoutError):
            return None
        except Exception as e:
            log.debug("OCR call failed: %s", e)
            return None

    text = _ocr_call(timeout_ms / 1000.0)
    if text is not None:
        return (text[:500], False)
    # Q5.1 retry once
    text = _ocr_call(retry_timeout_ms / 1000.0)
    if text is not None:
        return (text[:500], False)
    return ("", True)


def _screencapture_and_ocr_blocking() -> str:
    """The actual OCR primitive — screencapture to a tempfile + Vision OCR
    via osascript. Returns the recognized text or "" on failure.
    Separated so it can be monkeypatched in tests."""
    import tempfile
    try:
        # H-1 (PR-4A-2): distinctive prefix so a SIGTERM that interrupts this
        # function (before the os.unlink below) leaves a file the shutdown
        # cleanup can safely glob-purge without touching other apps' temp pngs.
        with tempfile.NamedTemporaryFile(prefix="codec_obs_", suffix=".png", delete=False) as f:
            tmp_png = f.name
        # silent screen capture, primary display only, no sound
        subprocess.run(
            ["screencapture", "-x", "-C", "-t", "png", tmp_png],
            capture_output=True, timeout=2,
        )
        # OCR via Vision framework
        ocr_script = f'''
on run
    set img to current application's NSImage's alloc()'s initWithContentsOfFile:"{tmp_png}"
    set req to current application's VNRecognizeTextRequest's alloc()'s init()
    set hdl to current application's VNImageRequestHandler's alloc()'s initWithCIImage:(current application's CIImage's imageWithData:(img's TIFFRepresentation())) options:(missing value)
    hdl's performRequests:(current application's NSArray's arrayWithObject:req) |error|:(missing value)
    set out to ""
    repeat with res in (req's results())
        set out to out & ((res's topCandidates:1)'s objectAtIndex:0)'s |string|() & linefeed
    end repeat
    return out as text
end run
'''
        result = subprocess.run(
            ["osascript", "-e", ocr_script],
            capture_output=True, text=True, timeout=3,
        )
        try:
            os.unlink(tmp_png)
        except OSError:
            pass
        return (result.stdout or "")[:500]
    except Exception:
        return ""


def _get_recent_files(window_seconds: int = 300) -> List[Dict[str, Any]]:
    """Recently-modified files in the user's most likely working dirs.
    Returns up to 5 entries, mtime within window_seconds."""
    candidates = [
        os.path.expanduser("~/Documents"),
        os.path.expanduser("~/Downloads"),
        os.path.expanduser("~/Desktop"),
        os.path.expanduser("~/codec-repo"),
    ]
    cutoff = time.time() - window_seconds
    out: List[Dict[str, Any]] = []
    for root in candidates:
        if not os.path.isdir(root):
            continue
        try:
            for entry in os.listdir(root):
                if entry.startswith("."):
                    continue
                full = os.path.join(root, entry)
                try:
                    mtime = os.path.getmtime(full)
                    if mtime >= cutoff:
                        out.append({"path": full,
                                    "mtime": datetime.fromtimestamp(
                                        mtime, timezone.utc).isoformat(timespec="seconds")})
                        if len(out) >= 5:
                            return out
                except OSError:
                    continue
        except OSError:
            continue
    return out


# ── Ring buffer ───────────────────────────────────────────────────────────────
class RingBuffer:
    """Bounded snapshot buffer. Threadsafe-friendly: appends and snapshots
    are individual deque operations (atomic in CPython); the snapshot()
    return is a list copy so callers can mutate freely."""

    __slots__ = ("_dq", "_lock", "_last_clipboard_hash")

    def __init__(self, maxlen: int):
        if maxlen < 1:
            maxlen = 1
        self._dq: deque = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._last_clipboard_hash: Optional[str] = None

    def append(self, snapshot: dict) -> None:
        with self._lock:
            self._dq.append(snapshot)

    def snapshot(self) -> List[dict]:
        with self._lock:
            return list(self._dq)

    def clear(self) -> None:
        with self._lock:
            self._dq.clear()
            self._last_clipboard_hash = None

    def __len__(self) -> int:
        with self._lock:
            return len(self._dq)

    def render_summary(self, max_tokens: int = 200) -> str:
        """Render a ≤max_tokens summary string. Most-recent-first order.
        Approximation: 4 chars ≈ 1 token. Hard char cap at max_tokens*4."""
        max_chars = max_tokens * 4
        snapshots = self.snapshot()
        if not snapshots:
            return ""
        snapshots = list(reversed(snapshots))   # most recent first

        lines: List[str] = ["[CODEC observation, last 10 min]"]
        latest = snapshots[0]
        # Active window line
        win = latest.get("active_window") or {}
        if win.get("app"):
            ago = self._fmt_age_seconds(latest.get("idle_seconds", 0))
            title = win.get("title", "")
            lines.append(f"Active: {win['app']}"
                         + (f" — {title}" if title else "")
                         + f" ({ago} ago)")

        # Recent files (across the buffer, deduped)
        seen_files = set()
        recent_lines = []
        for snap in snapshots:
            for rf in (snap.get("recent_files") or []):
                p = rf.get("path", "")
                if p and p not in seen_files:
                    seen_files.add(p)
                    recent_lines.append(os.path.basename(p))
                    if len(recent_lines) >= 5:
                        break
            if len(recent_lines) >= 5:
                break
        if recent_lines:
            lines.append(f"Recent files: {', '.join(recent_lines)}")

        # Clipboard — most recent change in window
        for snap in snapshots:
            cb = snap.get("clipboard")
            if cb:
                preview = (cb.get("preview") or "")[:80]
                kind = cb.get("content_type", "text")
                lines.append(f"Clipboard ({kind}): {preview}")
                break

        # Screen text — most recent OCR
        for snap in snapshots:
            ocr = (snap.get("screenshot_ocr") or "").strip()
            if ocr:
                lines.append(f"Screen text: \"{ocr[:200]}\"")
                break

        rendered = "\n".join(lines)
        if len(rendered) <= max_chars:
            return rendered
        # Truncate middle if over budget
        head = rendered[: max_chars // 2 - 5]
        tail = rendered[-(max_chars // 2 - 5):]
        return head + "\n[...]\n" + tail

    @staticmethod
    def _fmt_age_seconds(idle_s: float) -> str:
        s = int(idle_s)
        if s < 60:
            return f"{s}s"
        m = s // 60
        if m < 60:
            return f"{m}min"
        h = m // 60
        return f"{h}h"


# ── Module-level singletons ───────────────────────────────────────────────────
_GLOBAL_BUFFER: Optional[RingBuffer] = None
_GLOBAL_BUFFER_LOCK = threading.Lock()


def _get_or_init_buffer(cfg: Dict[str, Any]) -> RingBuffer:
    """Lazy-init the module-level singleton. Step 6 + Step 7 import this
    accessor (Q5.7 forward-compat API)."""
    global _GLOBAL_BUFFER
    with _GLOBAL_BUFFER_LOCK:
        if _GLOBAL_BUFFER is None:
            cadence = min(int(cfg["cadence_active_s"]), int(cfg["cadence_idle_s"]))
            maxlen = max(1, int(cfg["buffer_depth_min"]) * 60 // cadence)
            _GLOBAL_BUFFER = RingBuffer(maxlen=maxlen)
        return _GLOBAL_BUFFER


# ── poll() — single poll cycle ────────────────────────────────────────────────
def poll(buffer: Optional[RingBuffer] = None,
         cfg: Optional[Dict[str, Any]] = None,
         emit_audit: bool = True) -> Dict[str, Any]:
    """Run one poll cycle. Appends to `buffer` (or the module singleton).
    Returns the snapshot dict written. Emits observation_tick (or
    observation_tick_slow if poll exceeds threshold) when emit_audit is True.

    Caller can pass emit_audit=False for tests that want to assert on the
    snapshot without dirtying the audit log.
    """
    if cfg is None:
        cfg = _load_config()
    if buffer is None:
        buffer = _get_or_init_buffer(cfg)

    t0 = time.monotonic()
    idle = _idle_seconds()
    cadence = (int(cfg["cadence_active_s"])
               if idle < float(cfg["idle_threshold_s"])
               else int(cfg["cadence_idle_s"]))

    # 1. Active window
    active_window = _get_active_window()

    # 2. Clipboard delta
    cb_now = _get_clipboard_now()
    import hashlib
    cb_hash = hashlib.sha1(cb_now.encode("utf-8", errors="replace")).hexdigest()
    cb_changed = cb_hash != buffer._last_clipboard_hash
    buffer._last_clipboard_hash = cb_hash
    clipboard_block: Optional[Dict[str, Any]] = None
    if cb_changed and cb_now:
        clipboard_block = {
            "preview": cb_now[:200],
            "content_type": _classify_clipboard_kind(cb_now),
        }

    # 3. Screenshot OCR (with retry per Q5.1).
    # 2026-05-02 hotfix: bypass entirely when ocr_enabled=False to avoid
    # the screencapture popup storm on machines without Screen Recording
    # permission granted to the PM2 child process. Buffer still gets
    # active_window + clipboard + recent_files signals.
    if cfg.get("ocr_enabled", True):
        ocr_text, ocr_skipped = _get_screenshot_ocr(
            int(cfg["ocr_timeout_ms"]), int(cfg["ocr_retry_timeout_ms"]))
    else:
        ocr_text, ocr_skipped = ("", True)

    # 4. Recent files
    recent_files = _get_recent_files(window_seconds=300)

    snapshot = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "active_window": active_window,
        "screenshot_ocr": ocr_text,
        "ocr_skipped": ocr_skipped,
        "clipboard": clipboard_block,
        "recent_files": recent_files,
        "idle_seconds": idle,
    }
    buffer.append(snapshot)

    poll_duration_ms = (time.monotonic() - t0) * 1000.0

    if emit_audit:
        _emit_observation_tick(snapshot, cadence, poll_duration_ms,
                               len(buffer), float(cfg["poll_slow_threshold_ms"]))

    # Phase 2 Step 6 — evaluate registered triggers against this snapshot.
    # Inline (not a separate PM2 service) — observer poll is the only event
    # source, so triggers piggyback on the same cadence. Try/except so
    # trigger failures NEVER break observer polling.
    if emit_audit:   # only fire triggers from real polls, not test polls
        try:
            from codec_triggers import evaluate as _eval_triggers
            _eval_triggers(snapshot)
        except Exception as e:
            log.debug("[observer] trigger evaluation failed (non-fatal): %s", e)

        # Phase 3.5 — proactive intelligence overlay. After Step 6 triggers,
        # check declarative patterns (long-form dwell, multi-tab research, ...).
        # OFF by default (PROACTIVE_OVERLAY_ENABLED env var). Defensive: if
        # codec_proactive import fails OR a pattern raises, observer keeps
        # running.
        try:
            from codec_proactive import check_for_proactive
            history = []
            try:
                history = list(get_global_buffer().snapshot())
            except Exception:
                pass
            suggestion = check_for_proactive(snapshot, history=history)
            if suggestion is not None:
                try:
                    from codec_agent_messaging import post_message
                    post_message(
                        agent_id="proactive",
                        type="agent_question",   # reuses Step 10 message-type frozen vocab
                        title=suggestion.title,
                        body=suggestion.body,
                        actions=suggestion.actions,
                        correlation_id=f"proactive_{suggestion.pattern_id}",
                    )
                except Exception as e:
                    log.debug("[observer] proactive post_message failed: %s", e)
        except Exception as e:
            log.debug("[observer] proactive check failed (non-fatal): %s", e)

    return snapshot


def _emit_observation_tick(snapshot: Dict[str, Any], cadence_used_s: int,
                           poll_duration_ms: float, buffer_depth: int,
                           slow_threshold_ms: float) -> None:
    """Emit observation_tick (or observation_tick_slow). METADATA-ONLY —
    no titles, no OCR text, no clipboard content, no file paths."""
    win = snapshot.get("active_window") or {}
    cb = snapshot.get("clipboard") or {}
    extra = {
        "active_app": win.get("app", ""),
        "active_title_len": len(win.get("title", "")),
        "ocr_chars": len(snapshot.get("screenshot_ocr") or ""),
        "ocr_skipped": bool(snapshot.get("ocr_skipped")),
        "clipboard_changed": cb is not None and len(cb) > 0,
        "clipboard_kind": cb.get("content_type", "") if cb else "",
        "recent_files_count": len(snapshot.get("recent_files") or []),
        "idle_seconds": float(snapshot.get("idle_seconds", 0)),
        "cadence_used_s": cadence_used_s,
        "buffer_depth": buffer_depth,
        "poll_duration_ms": round(poll_duration_ms, 2),
    }
    cid = secrets.token_hex(6)   # per-tick correlation_id (operations are per-poll)
    if poll_duration_ms > slow_threshold_ms:
        # Q5.5 — emit observation_tick_slow as a warning-level signal.
        try:
            _log_event(OBSERVATION_TICK_SLOW, "codec-observer",
                       f"poll exceeded {slow_threshold_ms:.0f}ms ({poll_duration_ms:.1f}ms)",
                       extra=extra, outcome="warning", level="warning",
                       correlation_id=cid)
        except Exception as e:
            log.debug("observation_tick_slow emit failed: %s", e)
    else:
        try:
            _log_event(OBSERVATION_TICK, "codec-observer", "",
                       extra=extra, outcome="ok", level="info",
                       correlation_id=cid)
        except Exception as e:
            log.debug("observation_tick emit failed: %s", e)


# ── §X Observation injection contract ─────────────────────────────────────────
# Q5 override: gate cloud-transport injection on cheap text patterns.
# Local transport always injects; MCP transport never injects.

# Possessive-without-context. Match "(my|this|that|these|those|the) <noun>"
# where <noun> is NOT in the stop-noun list. The negative-lookup is in
# Python (regex captures the noun, then we check the stop-list).
_POSSESSIVE_RE = re.compile(
    r"\b(my|this|that|these|those|the)\s+([a-zA-Z][a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)

# Continuation language. Covers: continue, resume, next, where was I,
# pick up, keep going, finish, what next.
_CONTINUATION_RE = re.compile(
    r"\b(continue|resume|next|pick\s+up|keep\s+going|finish|what(?:'?s|\s+is)\s+next|"
    r"where\s+was\s+i|carry\s+on|press\s+on)\b",
    re.IGNORECASE,
)


def _should_inject_for_cloud_transport(prompt: str,
                                       stop_nouns: List[str]) -> Tuple[bool, str]:
    """Apply the §X.1 pattern checks. Returns (should_inject, reason).
    reason ∈ {"possessive_match", "continuation_match", "skipped_no_match"}."""
    if not prompt:
        return (False, "skipped_no_match")

    # Continuation check (cheaper, simpler)
    if _CONTINUATION_RE.search(prompt):
        return (True, "continuation_match")

    # Possessive-with-non-stop-noun
    stop_set = {n.lower() for n in stop_nouns}
    for match in _POSSESSIVE_RE.finditer(prompt):
        noun = match.group(2).lower()
        if noun not in stop_set:
            return (True, "possessive_match")

    return (False, "skipped_no_match")


def maybe_inject_observation_summary(
    user_prompt: str,
    transport: str,
    skill_name: Optional[str] = None,
    skill_module: Optional[Any] = None,
) -> Tuple[Optional[str], str]:
    """The chat / voice handler's single integration point.

    Returns (summary_or_None, reason).

    reason ∈ {
        "always_local",
        "possessive_match",
        "continuation_match",
        "skill_flag",
        "skipped_no_match",
        "skipped_disabled",
        "skipped_empty_buffer",
    }

    Audit emit (observation_summary_injected) is fired ONLY when summary
    is non-None — skipped paths are silent (no audit-log spam).

    Caller must pass `transport` (the transport tag the caller would put
    on its own audit emits — "local", "chat", "voice", "http", "mcp").
    Caller may pass `skill_name` if the prompt resolved to a skill;
    `skill_module` is the loaded skill module (so we can read its
    SKILL_NEEDS_OBSERVATION attribute without a re-import).
    """
    # 1. Kill switch
    if not _enabled():
        return (None, "skipped_disabled")

    cfg = _load_config()
    buffer = _get_or_init_buffer(cfg)

    # 2. Empty buffer (process just started)
    if len(buffer) == 0:
        return (None, "skipped_empty_buffer")

    # 3. Transport-based gating
    transport_low = (transport or "").lower()
    reason: Optional[str] = None

    if transport_low == "local":
        reason = "always_local"
    elif transport_low == "mcp":
        return (None, "skipped_no_match")
    elif transport_low in ("chat", "voice", "http"):
        # Skill-flag override (highest priority among gated transports)
        if skill_module is not None and getattr(
                skill_module, "SKILL_NEEDS_OBSERVATION", False):
            reason = "skill_flag"
        else:
            should_inject, gate_reason = _should_inject_for_cloud_transport(
                user_prompt, list(cfg.get("stop_nouns", [])))
            if should_inject:
                reason = gate_reason
            else:
                return (None, gate_reason)
    else:
        # Unknown transport — be conservative, don't inject.
        return (None, "skipped_no_match")

    # 4. Render and emit
    summary = buffer.render_summary(max_tokens=int(cfg["summary_max_tokens"]))
    if not summary:
        return (None, "skipped_empty_buffer")

    # Token estimation (~4 chars per token — same approximation used in
    # render_summary's char cap). Caller's audit op can correlate via
    # correlation_id passed elsewhere; here we just emit our event.
    tokens_used = max(1, len(summary) // 4)

    try:
        _log_event(
            OBSERVATION_SUMMARY_INJECTED, "codec-observer",
            f"injected observer summary ({tokens_used} tokens, reason={reason})",
            extra={
                "tokens_used": tokens_used,
                "injection_reason": reason,
                "buffer_entries_summarized": len(buffer),
                "transport": transport_low,
            },
            outcome="ok", level="info",
            transport=transport_low,
            # NB: we don't have the wrapping op's correlation_id here.
            # Caller can pass via a contextvar in a future refinement;
            # for now this emit's cid is fresh per inject.
            correlation_id=secrets.token_hex(6),
        )
    except Exception as e:
        log.debug("observation_summary_injected emit failed: %s", e)

    return (summary, reason)


# ── Q5.7 forward-compat API for Steps 6 + 7 ──────────────────────────────────
# Step 6 (Triggers) reads buffer.snapshot() to evaluate trigger candidates.
# Step 7 (Shift Report) reads ~/.codec/observation_summaries/*.md (only
# populated by an explicit persist call).

_SUMMARIES_ROOT = Path(os.path.expanduser("~/.codec/observation_summaries"))


def get_global_buffer() -> RingBuffer:
    """Step 6 / Step 7 accessor for the live ring buffer. Lazy-inits."""
    return _get_or_init_buffer(_load_config())


def persist_for_shift_report() -> Optional[Path]:
    """Step 7 calls this at shift-report assembly time. Renders the live
    buffer summary to ~/.codec/observation_summaries/YYYY-MM-DDThh-mm.md
    and returns the path. Returns None if buffer is empty or observer
    is disabled."""
    if not _enabled():
        return None
    cfg = _load_config()
    buffer = _get_or_init_buffer(cfg)
    if len(buffer) == 0:
        return None
    summary = buffer.render_summary(max_tokens=int(cfg["summary_max_tokens"]) * 4)
    if not summary:
        return None
    _SUMMARIES_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    out = _SUMMARIES_ROOT / f"{stamp}.md"
    out.write_text(summary)
    return out


# ── Daemon entry (PM2) ────────────────────────────────────────────────────────
def run_daemon() -> None:
    """Forever-loop entry. Call from PM2 service. Honors OBSERVER_ENABLED
    at start AND each iteration so flipping the env var disables the
    loop without requiring a process restart."""
    log.info("[observer] daemon starting")

    # H-1 (PR-4A-2): graceful shutdown on PM2 SIGTERM. The RAM ring buffer is
    # ephemeral by design; the concrete leak is a screencapture tempfile left
    # behind if SIGTERM interrupts _screencapture_and_ocr_blocking before its
    # os.unlink. Best-effort purge any namespaced captures on the way out.
    def _observer_cleanup():
        import glob as _glob
        import tempfile as _tf
        for _f in _glob.glob(os.path.join(_tf.gettempdir(), "codec_obs_*.png")):
            try:
                os.unlink(_f)
            except OSError:
                pass
        log.info("[observer] graceful shutdown")
    import codec_lifecycle
    codec_lifecycle.install_handlers(_observer_cleanup, name="codec-observer")

    while True:
        if not _enabled():
            # Sleep 30s and re-check — cheap; enables runtime kill via env.
            time.sleep(30)
            continue
        cfg = _load_config()
        try:
            poll(cfg=cfg)
        except Exception as e:
            log.warning("[observer] poll iteration failed: %s", e)
        idle = _idle_seconds()
        cadence = (int(cfg["cadence_active_s"])
                   if idle < float(cfg["idle_threshold_s"])
                   else int(cfg["cadence_idle_s"]))
        # Long-idle reset (config-flagged).
        if (cfg.get("reset_on_long_idle") and
                idle > float(cfg["reset_idle_threshold_s"])):
            buf = _get_or_init_buffer(cfg)
            if len(buf) > 0:
                buf.clear()
                log.info("[observer] buffer cleared after long idle")

        # Phase 2 Step 7 — fire shift report at 18:00 local OR after 30 min
        # idle. Per-day dedup via skills/shift_report._STATE_PATH so the
        # idle path doesn't repeat. Time path uses a 1-min window
        # (daily_at_hour, daily_at_minute) — observer runs at >=60s cadence
        # so a single fire window is sufficient.
        try:
            _maybe_fire_shift_report(idle)
        except Exception as e:
            log.debug("[observer] shift report check failed: %s", e)

        time.sleep(cadence)


def _maybe_fire_shift_report(idle_seconds: float) -> None:
    """Phase 2 Step 7 — check fire conditions, invoke skill if matched.

    Two trigger paths:
      "time" — wall clock matches daily_at_hour:daily_at_minute (1-min window)
      "idle" — idle_seconds >= idle_minutes * 60

    Per-day dedup means whichever fires first wins; the other is suppressed.
    Manual invocations (via skill name from chat / voice / MCP) bypass this.
    """
    try:
        import importlib
        sys.path.insert(0, os.path.expanduser("~/.codec/skills"))
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/skills")
        spec = importlib.util.find_spec("shift_report")
        if spec is None:
            return
        sr_mod = importlib.import_module("shift_report")
    except Exception:
        return
    try:
        if not sr_mod._enabled():
            return
        if sr_mod.already_fired_today():
            return
        cfg = sr_mod._load_config()
        if not cfg.get("enabled", True):
            return

        # Time path
        now = datetime.now()
        hh = int(cfg.get("daily_at_hour", 18))
        mm = int(cfg.get("daily_at_minute", 0))
        if now.hour == hh and now.minute == mm:
            sr_mod.run_with_trigger_kind("time")
            log.info("[observer] shift report fired (time trigger)")
            return

        # Idle path
        idle_minutes = int(cfg.get("idle_minutes", 30))
        if idle_seconds >= idle_minutes * 60:
            sr_mod.run_with_trigger_kind("idle")
            log.info("[observer] shift report fired (idle trigger)")
    except Exception as e:
        log.debug("[observer] shift report invocation failed: %s", e)


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO,
                        format="[codec-observer] %(message)s")
    run_daemon()


__all__ = [
    "RingBuffer",
    "poll",
    "maybe_inject_observation_summary",
    "get_global_buffer",
    "persist_for_shift_report",
    "run_daemon",
    # Internal helpers exposed for tests + Step 6/7
    "_idle_seconds",
    "_should_inject_for_cloud_transport",
    "_classify_clipboard_kind",
    "_get_active_window",
    "_get_clipboard_now",
    "_get_screenshot_ocr",
    "_get_recent_files",
    "_load_config",
    "_enabled",
]
