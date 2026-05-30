"""CODEC Chat Pipeline — extractable building blocks for the chat handler.

B6-P2 / SR-33: extracted from codec_dashboard.py. The full FastAPI
handler (`chat_completion`, ~608 LOC) stays in codec_dashboard for now
because it threads many implicit module-level state dependencies; this
module hosts the testable, side-effect-light helpers so unit tests can
exercise them without standing up the dashboard.

Today this owns:
  - `_StepBudget`            per-turn step counter + warn / exhaustion logic
  - `_step_budget_enabled`   env-var read for the global kill switch
  - `_step_budget_for_route` config-driven per-route cap
  - `_is_conversational`     fast heuristic for routing chat→LLM vs chat→skill

codec_dashboard re-exports each as a private member so any external
reader that imported from there before the split keeps working.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

from codec_audit import STEP_BUDGET_EXHAUSTED, log_event

log = logging.getLogger("codec_chat_pipeline")

# Match codec_dashboard's CONFIG_PATH resolution so reads land on the same file
# (routes/_shared.CONFIG_PATH is the canonical home).
CONFIG_PATH = os.path.expanduser("~/.codec/config.json")


def _is_conversational(text: str) -> bool:
    """Detect if a message is conversational rather than a direct command.
    Conversational messages should go to the LLM, not trigger skills."""
    low = text.lower().strip()
    words = low.split()
    # Very short messages (1-3 words) are likely commands
    if len(words) <= 3:
        return False
    # Long messages (>15 words) are almost always conversational
    if len(words) > 15:
        return True
    # Messages with question-like patterns about CODEC/features/capabilities
    _CONV_PATTERNS = [
        "what do you think", "what's your", "whats your", "are we",
        "can you check", "can u check", "please check", "take a look",
        "what happened", "what is happening", "why did you", "why you",
        "do you have", "do u have", "have you", "did you",
        "here is", "here's", "check this", "check it",
        "read this", "read the", "now read", "please read",
        "save to", "save this", "your thought", "your thoughts",
        "what say you", "agreed", "let's", "lets", "revise",
        "should we", "how about", "im testing", "i'm testing",
        "i just tested", "i was testing", "something off",
        "something wrong", "not working", "doesn't work",
    ]
    if any(p in low for p in _CONV_PATTERNS):
        return True
    # URLs in messages are usually sharing links, not commands
    if "http://" in low or "https://" in low or ".com" in low or ".org" in low:
        return True
    # Multi-sentence messages are conversational
    if text.count('.') >= 2 or text.count('?') >= 1 or text.count('!') >= 2:
        return True
    return False


def _step_budget_enabled() -> bool:
    """Read STEP_BUDGET_ENABLED env var. Default true. Read each call so
    tests can monkeypatch."""
    val = (os.environ.get("STEP_BUDGET_ENABLED") or "true").strip().lower()
    return val not in ("false", "0", "no", "off")


def _step_budget_for_route(route: str) -> Optional[int]:
    """Return the budget cap for the given route, or None for "no cap"
    (MCP). Read each call so config edits take effect on PM2 restart.

    Defaults per design §3.2:
        chat:  5
        voice: 5
        mcp:   None  (no turn budget — each MCP call is its own turn)
    """
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f).get("step_budget", {})
    except (OSError, ValueError):
        cfg = {}
    if route == "mcp":
        return None  # MCP path has no turn concept; SKILL_TIMEOUT_SEC governs.
    default = 5
    v = cfg.get(route, default)
    if v is None:
        return None
    if isinstance(v, int) and v > 0:
        return v
    return default


class _StepBudget:
    """Per-request counter + warn / exhaustion logic. Construct at request
    entry; call ``consume(kind)`` before each step; check ``warn_now()``
    to decide whether to append the "1 step remaining" prompt suffix.

    Threadsafe-friendly: each request has its own instance (no shared
    state). Audit emits go through log_event so concurrent requests
    serialise via codec_audit's existing _LOCK.
    """
    __slots__ = ("route", "limit", "count", "enabled", "exhausted_emitted",
                 "correlation_id")

    def __init__(self, route: str = "chat", correlation_id: Optional[str] = None):
        self.route = route
        self.limit = _step_budget_for_route(route) if _step_budget_enabled() else None
        self.count = 0
        self.enabled = self.limit is not None
        self.exhausted_emitted = False
        self.correlation_id = correlation_id

    def consume(self, kind: str = "step") -> bool:
        """Try to consume one budget step. Returns True if OK to proceed,
        False if budget would be exhausted by this consumption.

        ``kind`` is a free-form label for telemetry (e.g. "skill_hijack",
        "llm_call", "post_llm_skill_tag", "crew_spawn"). Logged on the
        ``step_budget_exhausted`` audit event when the cap is hit.
        """
        if not self.enabled:
            return True
        self.count += 1
        if self.count > self.limit:
            self._emit_exhausted(kind)
            return False
        return True

    def warn_now(self) -> bool:
        """True when we're at limit-1 and the next step would cap. Used
        by the LLM-call path to inject "⚠ 1 step remaining" into the
        prompt suffix."""
        if not self.enabled:
            return False
        return self.count == max(0, self.limit - 1)

    def at_limit(self) -> bool:
        """True if we've already hit the cap (consume returned False)."""
        if not self.enabled:
            return False
        return self.count >= self.limit

    def _emit_exhausted(self, kind: str):
        if self.exhausted_emitted:
            return
        self.exhausted_emitted = True
        try:
            log_event(
                STEP_BUDGET_EXHAUSTED,
                "codec-dashboard",
                f"chat step budget exhausted at {self.count} (kind={kind})",
                extra={
                    "budget_type": "chat_turn",
                    "limit": self.limit,
                    "actual": self.count,
                    "kind": kind,
                },
                outcome="warning",
                level="warning",
                correlation_id=self.correlation_id,
            )
        except Exception as e:
            log.warning("[step_budget] emit failed: %s", e)
