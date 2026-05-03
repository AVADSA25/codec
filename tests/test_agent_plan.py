"""Phase 3 Step 8 tests — codec_agent_plan + routes/agents.py.

25 tests covering: audit constants, dataclasses, atomic R/W, validation,
plan-hash, LLM drafter, clarifying loop, global allowlist, state machine,
PWA endpoints, and end-to-end integration.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def test_audit_constants_present():
    """Phase 3 Step 8 adds 6 named events + 1 frozenset."""
    import codec_audit
    assert codec_audit.AGENT_PLAN_DRAFTED == "agent_plan_drafted"
    assert codec_audit.AGENT_PLAN_APPROVED == "agent_plan_approved"
    assert codec_audit.AGENT_PLAN_REJECTED == "agent_plan_rejected"
    assert codec_audit.AGENT_PLAN_REVISED == "agent_plan_revised"
    assert codec_audit.AGENT_GLOBAL_GRANT_ADDED == "agent_global_grant_added"
    assert codec_audit.AGENT_GLOBAL_GRANT_REMOVED == "agent_global_grant_removed"
    assert codec_audit.PHASE3_STEP8_EVENTS == frozenset({
        "agent_plan_drafted", "agent_plan_approved", "agent_plan_rejected",
        "agent_plan_revised", "agent_global_grant_added", "agent_global_grant_removed",
    })
