"""Tests for routes/skills.py — verify /api/save_skill and /api/forge are
removed (closes D-2 + D-3), and that /api/skill/review + /api/skill/approve
remain functional as the replacement flow.

Reference: docs/audits/PHASE-1-SECURITY.md findings D-2, D-3.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from routes import skills as skills_routes  # noqa: E402


# ── Static checks (read the source) ───────────────────────────────────────────


def test_save_skill_handler_removed():
    """The save_skill function name must not exist in the routes module."""
    assert not hasattr(skills_routes, "save_skill"), (
        "save_skill must be removed (D-3 — direct-write endpoint with weak validation)"
    )


def test_forge_skill_handler_removed():
    """The forge_skill function name must not exist in the routes module."""
    assert not hasattr(skills_routes, "forge_skill"), (
        "forge_skill must be removed (D-2 — URL-fetch → LLM → write, no review gate)"
    )


def test_no_route_decorator_strings_for_removed_endpoints():
    """The route-decorator strings must not appear in the module source."""
    src = inspect.getsource(skills_routes)
    assert '"/api/save_skill"' not in src, (
        "Route string '/api/save_skill' must be gone from routes/skills.py"
    )
    assert '"/api/forge"' not in src, (
        "Route string '/api/forge' must be gone from routes/skills.py"
    )


def test_replacement_handlers_present():
    """The /api/skill/review + /api/skill/approve handlers must remain."""
    assert hasattr(skills_routes, "skill_review"), "skill_review handler must remain"
    assert hasattr(skills_routes, "skill_approve"), "skill_approve handler must remain"


# ── Live TestClient checks (verify the FastAPI app behavior) ──────────────────


def _make_client() -> TestClient:
    app = FastAPI()
    app.include_router(skills_routes.router)
    return TestClient(app)


def test_post_save_skill_returns_404():
    """A POST to the removed endpoint must return 404 (route not registered)."""
    client = _make_client()
    r = client.post("/api/save_skill", json={"filename": "x.py", "content": "..."})
    assert r.status_code == 404, (
        f"Expected 404 (route removed), got {r.status_code}: {r.text[:200]}"
    )


def test_post_forge_returns_404():
    """A POST to the removed endpoint must return 404 (route not registered)."""
    client = _make_client()
    r = client.post("/api/forge", json={"code": "http://example.com"})
    assert r.status_code == 404, (
        f"Expected 404 (route removed), got {r.status_code}: {r.text[:200]}"
    )


def test_post_skill_review_still_accepts_valid_body():
    """The replacement /api/skill/review endpoint must still accept valid
    payloads — same shape as save_skill's content+filename."""
    client = _make_client()
    valid_skill = (
        'SKILL_NAME = "test_review"\n'
        'SKILL_DESCRIPTION = "Probe that /api/skill/review still accepts valid input"\n'
        'SKILL_TRIGGERS = ["test review"]\n'
        '\n'
        'def run(task, app="", ctx=""):\n'
        '    return "ok"\n'
    )
    r = client.post(
        "/api/skill/review",
        json={"code": valid_skill, "filename": "test_review.py"},
    )
    assert r.status_code == 200, (
        f"/api/skill/review must accept valid input — got {r.status_code}: {r.text[:200]}"
    )
    body = r.json()
    assert "review_id" in body, "Response must include a review_id for the approve step"
    assert body.get("filename") == "test_review.py"


def test_skill_review_rejects_empty_body():
    """The replacement endpoint still validates input (sanity check on
    semantics — we didn't accidentally widen its contract)."""
    client = _make_client()
    r = client.post("/api/skill/review", json={})
    assert r.status_code == 400


def test_skill_approve_writes_only_after_review(tmp_path, monkeypatch):
    """Full review → approve flow must write the skill file to disk only
    after the explicit approve step, not at review."""
    # Redirect _get_skills_dir to a tmp_path so the test doesn't touch
    # ~/.codec/skills/.
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr(skills_routes, "_get_skills_dir", lambda: str(skills_dir))

    client = _make_client()
    valid_skill = (
        'SKILL_NAME = "test_approve"\n'
        'SKILL_DESCRIPTION = "Probe that /api/skill/approve writes after approval"\n'
        'SKILL_TRIGGERS = ["test approve"]\n'
        '\n'
        'def run(task, app="", ctx=""):\n'
        '    return "ok"\n'
    )

    # Step 1: review — file MUST NOT be on disk yet.
    r1 = client.post(
        "/api/skill/review",
        json={"code": valid_skill, "filename": "test_approve.py"},
    )
    assert r1.status_code == 200
    review_id = r1.json()["review_id"]
    assert not (skills_dir / "test_approve.py").exists(), (
        "Review step must NOT write to disk"
    )

    # Step 2: approve — file MUST be on disk now.
    r2 = client.post("/api/skill/approve", json={"review_id": review_id})
    assert r2.status_code == 200
    written = skills_dir / "test_approve.py"
    assert written.exists(), "Approve step must write the skill to disk"
    assert "test_approve" in written.read_text(encoding="utf-8")
