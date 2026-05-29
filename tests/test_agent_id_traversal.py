"""re-audit MEDIUM: routes/agents.py path-param endpoints pass {agent_id} raw to
codec_agent_plan._agent_dir(_AGENTS_DIR / agent_id) with no sanitization, so an
authenticated caller could traverse (../) to read/shape JSON outside the agents
dir. create_agent slugs are safe; the path-param endpoints are not. _agent_dir
must reject traversal, and the load_* readers must degrade to empty (→ the
endpoints return 404, not 500).
"""
import pytest

import codec_agent_plan


@pytest.mark.parametrize("bad", [
    "../../etc/passwd",
    "..",
    "a/b",
    "a\\b",
    ".hidden",
    "~root",
    "",
])
def test_agent_dir_rejects_traversal(bad):
    with pytest.raises(ValueError):
        codec_agent_plan._agent_dir(bad)


def test_agent_dir_allows_normal_slug():
    p = codec_agent_plan._agent_dir("agent_abc-123")
    assert p.name == "agent_abc-123"


def test_load_manifest_graceful_on_traversal():
    # readers must not raise on a hostile id — endpoints rely on {}/None → 404.
    assert codec_agent_plan.load_manifest("../../etc/passwd") == {}
    assert codec_agent_plan.load_grants("../../etc") == {}
    assert codec_agent_plan.load_state("..") == {}
    assert codec_agent_plan.load_plan("../x") is None
