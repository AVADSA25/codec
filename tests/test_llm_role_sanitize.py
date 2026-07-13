"""codec_llm must never send an API-invalid message role.

2026-07 incident: terminal chat 422'd on every message
("role 'fact' invalid") and surfaced to the user as "Qwen busy". Cause: the
`conversations` table holds role="fact" rows (fact_extract / memory_save) and
the session bootstrap replayed the last N rows verbatim as chat messages.

Two layers now prevent it: the message builders filter facts out, and this —
codec_llm's _build_request — sanitizes any stray non-standard role at the single
chokepoint every call()/stream() goes through.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import codec_llm  # noqa: E402


def test_fact_role_is_relabelled_not_dropped():
    msgs = [{"role": "fact", "content": "user likes tea"}]
    out = codec_llm._sanitize_roles(msgs)
    assert out[0]["role"] in codec_llm._VALID_LLM_ROLES
    assert out[0]["content"] == "user likes tea", "content must survive"


def test_unknown_role_becomes_user_valid_at_any_position():
    """A stray non-standard role mid-conversation must not become a mid-list
    'system' message (some servers 500 on 'system message must be first')."""
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "yo"},
        {"role": "fact", "content": "context"},
        {"role": "user", "content": "q"},
    ]
    out = codec_llm._sanitize_roles(msgs)
    assert out[3]["role"] == "user"
    assert all(m["role"] in codec_llm._VALID_LLM_ROLES for m in out)


def test_valid_roles_pass_through_untouched():
    for role in ("user", "assistant", "system", "developer", "tool"):
        out = codec_llm._sanitize_roles([{"role": role, "content": "x"}])
        assert out[0]["role"] == role


def test_build_request_payload_has_only_valid_roles():
    msgs = [{"role": "fact", "content": "f"}, {"role": "user", "content": "u"}]
    _headers, payload = codec_llm._build_request(
        msgs, model="m", api_key="", max_tokens=10, temperature=0.5,
        enable_thinking=False, extra_kwargs=None,
    )
    roles = {m["role"] for m in payload["messages"]}
    assert roles <= codec_llm._VALID_LLM_ROLES, f"invalid role reached payload: {roles}"
