"""re-audit MEDIUMs.

M-consent (partial, CHAIN-002): skill_forge writes forged code straight to disk
(bypassing the review-and-approve flow PR-1B mandated) yet was on
CHAT_SKILL_ALLOWLIST — so a prompt-injected [SKILL:skill_forge:...] tag could
persist an unsandboxed skill. It must not be auto-firable from chat. create_skill
STAYS (it stages for human review, never writes without approval).
"""
import codec_dashboard


def test_skill_forge_not_auto_firable_from_chat():
    assert "skill_forge" not in codec_dashboard.CHAT_SKILL_ALLOWLIST, (
        "skill_forge writes code to disk without the review gate — it must not be "
        "auto-firable from a chat message (CHAIN-002)"
    )


def test_phantom_ask_codec_to_build_removed():
    # No skills/ask_codec_to_build.py exists — a stale allowlist entry.
    assert "ask_codec_to_build" not in codec_dashboard.CHAT_SKILL_ALLOWLIST


def test_create_skill_still_allowed_review_gated_path():
    # create_skill routes through /api/skill/review (never writes without
    # approval), so it remains the safe chat-reachable skill-creation path.
    assert "create_skill" in codec_dashboard.CHAT_SKILL_ALLOWLIST
