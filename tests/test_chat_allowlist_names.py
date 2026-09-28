"""CHAT_SKILL_ALLOWLIST names must be skill REGISTRY names.

The chat gate compares `check_skill(text)["name"]` — the skill's SKILL_NAME —
against the allowlist. An entry spelled as the file name instead (the digest was
listed as "ai_news_digest" while its SKILL_NAME is "AI News Digest") can never
match, so that skill silently never fires from chat.
"""
from pathlib import Path

from codec_skill_registry import SkillRegistry
from routes.chat import CHAT_SKILL_ALLOWLIST

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

# Allowlist entries that are deliberately not built-in skills (none today).
KNOWN_NON_SKILLS = set()


def _registry_names():
    reg = SkillRegistry(str(SKILLS_DIR))
    reg.scan()
    return set(reg.names())


def test_every_allowlist_name_is_a_registry_name():
    missing = sorted(CHAT_SKILL_ALLOWLIST - KNOWN_NON_SKILLS - _registry_names())
    assert not missing, f"allowlist names with no matching SKILL_NAME: {missing}"


def test_news_digest_uses_its_registry_name():
    assert "AI News Digest" in _registry_names()
    assert "AI News Digest" in CHAT_SKILL_ALLOWLIST
    assert "ai_news_digest" not in CHAT_SKILL_ALLOWLIST
