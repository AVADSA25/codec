"""Runtime smoke tests for all 12 pre-built crews (B3 / SR-24).

Audit T4 found that only 3 of the 12 crews had dedicated runtime tests.
This file pins the other 9 plus the 3 already covered, so every crew in
CREW_REGISTRY at codec_agents.py:1696-1709 builds cleanly with stub args
and exposes a non-empty allowed_tools list.

A crew that won't build is a deploy-time regression; a crew with an
empty allowed_tools list bypasses the per-crew scope guard.
"""

import pytest


# Crews documented in CLAUDE.md §3 + FEATURES.md §5 #19.
EXPECTED_CREWS = [
    "deep_research",
    "daily_briefing",
    "trip_planner",
    "competitor_analysis",
    "email_handler",
    "social_media",
    "code_review",
    "data_analysis",
    "content_writer",
    "meeting_summarizer",
    "invoice_generator",
    "project_manager",
]


# Catch-all kwargs covering every documented arg name across all crews.
_STUB_KWARGS = {
    "query": "test query",
    "topic": "test topic",
    "destination": "Paris",
    "dates": "2026-06-01 to 2026-06-05",
    "code": "def foo(): return 1",
    "meeting_input": "Standup notes: Alice ships X, Bob blocks on Y",
    "invoice_details": "Bill ACME $500 for consulting Q2",
    "project": "Launch CODEC v3.3",
    "content_type": "blog post",
    "audience": "general",
}


@pytest.mark.parametrize("crew_name", EXPECTED_CREWS)
def test_crew_registry_contains_expected(crew_name):
    """Each documented crew is present in CREW_REGISTRY."""
    from codec_agents import CREW_REGISTRY
    assert crew_name in CREW_REGISTRY, (
        f"Crew {crew_name!r} missing from CREW_REGISTRY")


@pytest.mark.parametrize("crew_name", EXPECTED_CREWS)
def test_crew_builder_returns_crew_instance(crew_name):
    """Builder returns a Crew with at least 1 agent and 1 task."""
    from codec_agents import CREW_REGISTRY, Crew
    entry = CREW_REGISTRY[crew_name]
    builder = entry["builder"]
    crew = builder(**_STUB_KWARGS)
    assert isinstance(crew, Crew), (
        f"{crew_name} builder did not return a Crew instance")
    assert len(crew.agents) >= 1, f"{crew_name} has no agents"
    assert len(crew.tasks) >= 1, f"{crew_name} has no tasks"


@pytest.mark.parametrize("crew_name", EXPECTED_CREWS)
def test_crew_has_nonempty_allowed_tools(crew_name):
    """Every crew must declare allowed_tools — the empty list bypasses
    the per-crew tool scope guard at Crew.__post_init__."""
    from codec_agents import CREW_REGISTRY
    entry = CREW_REGISTRY[crew_name]
    builder = entry["builder"]
    crew = builder(**_STUB_KWARGS)
    assert crew.allowed_tools, (
        f"{crew_name} must declare allowed_tools — empty list disables "
        "the tool scope guard")
    assert all(isinstance(t, str) for t in crew.allowed_tools), (
        f"{crew_name}.allowed_tools must be List[str]")
