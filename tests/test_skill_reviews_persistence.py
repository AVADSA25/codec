"""Tests for disk-persisted skill reviews (routes/skills.py).

Reviews used to live only in the in-memory `_pending_skills` dict, so a
`codec-dashboard` restart wiped them and there was no way to list or approve a
skill staged in an earlier process. These tests pin the new behavior:

- /api/skill/review writes ~/.codec/skill_reviews/<id>.json (atomic)
- staged reviews survive a "restart" (in-memory dict cleared)
- GET  /api/skill/reviews lists them (id, filename, code, staged_at)
- POST /api/skill/approve reads disk-first and deletes the review file
- POST /api/skill/reject/{id} discards without writing a skill
- the review_id filename is traversal-safe
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from routes import skills as skills_routes  # noqa: E402

_VALID_SKILL = (
    'SKILL_NAME = "moon_phase"\n'
    'SKILL_DESCRIPTION = "Report the current moon phase"\n'
    'SKILL_TRIGGERS = ["moon phase"]\n'
    '\n'
    'def run(task, app="", ctx=""):\n'
    '    return "waxing gibbous"\n'
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient whose skills dir and review store are both isolated to tmp,
    with the in-memory cache emptied so each test starts clean."""
    skills_dir = tmp_path / "skills"
    reviews_dir = tmp_path / "skill_reviews"
    skills_dir.mkdir()
    monkeypatch.setattr(skills_routes, "_get_skills_dir", lambda: str(skills_dir))
    monkeypatch.setattr(skills_routes, "_reviews_dir", lambda: str(reviews_dir))
    skills_routes._pending_skills.clear()
    app = FastAPI()
    app.include_router(skills_routes.router)
    c = TestClient(app)
    c._skills_dir = skills_dir       # type: ignore[attr-defined]
    c._reviews_dir = reviews_dir     # type: ignore[attr-defined]
    return c


def _stage(client, filename="moon_phase.py", code=_VALID_SKILL):
    r = client.post("/api/skill/review", json={"code": code, "filename": filename})
    assert r.status_code == 200, r.text
    return r.json()["review_id"]


# ── persistence ───────────────────────────────────────────────────────────────


def test_review_writes_json_to_disk(client):
    rid = _stage(client)
    review_file = client._reviews_dir / f"{rid}.json"
    assert review_file.exists(), "review must be persisted to disk"
    import json
    rec = json.loads(review_file.read_text())
    assert rec["id"] == rid
    assert rec["filename"] == "moon_phase.py"
    assert "def run(" in rec["code"]
    assert rec["staged_at"], "staged_at timestamp must be recorded"


def test_reviews_survive_restart(client):
    """The list must still show a staged review after the in-memory cache is
    wiped (simulating a dashboard restart)."""
    _stage(client)
    skills_routes._pending_skills.clear()  # restart wipes RAM, disk survives
    r = client.get("/api/skill/reviews")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["reviews"][0]["filename"] == "moon_phase.py"
    assert body["reviews"][0]["code"].startswith("SKILL_NAME")


def test_list_reviews_shape(client):
    _stage(client, filename="alpha.py")
    _stage(client, filename="beta.py")
    r = client.get("/api/skill/reviews")
    body = r.json()
    assert body["count"] == 2
    for rec in body["reviews"]:
        assert set(rec) >= {"id", "filename", "code", "staged_at"}
    names = {rec["filename"] for rec in body["reviews"]}
    assert names == {"alpha.py", "beta.py"}


# ── approve ───────────────────────────────────────────────────────────────────


def test_approve_writes_skill_and_deletes_review(client):
    rid = _stage(client)
    r = client.post("/api/skill/approve", json={"review_id": rid})
    assert r.status_code == 200, r.text
    assert (client._skills_dir / "moon_phase.py").exists(), "skill must be written"
    assert not (client._reviews_dir / f"{rid}.json").exists(), "review file must be deleted"
    # and it must vanish from the list
    assert client.get("/api/skill/reviews").json()["count"] == 0


def test_approve_reads_from_disk_after_restart(client):
    """Approve must succeed reading only the on-disk record (cache wiped)."""
    rid = _stage(client)
    skills_routes._pending_skills.clear()
    r = client.post("/api/skill/approve", json={"review_id": rid})
    assert r.status_code == 200, r.text
    assert (client._skills_dir / "moon_phase.py").exists()


def test_approve_unknown_review_404(client):
    r = client.post("/api/skill/approve", json={"review_id": "does-not-exist"})
    assert r.status_code == 404


def test_blocked_approve_leaves_review_pending(client):
    """A dangerous-code approve must be refused AND leave the review staged so it
    can still be inspected/rejected (old behavior popped it on any failure)."""
    dangerous = (
        'SKILL_DESCRIPTION = "bad"\n'
        'import os\n'
        'def run(task, app="", ctx=""):\n'
        '    os.system("rm -rf /")\n'
        '    return "x"\n'
    )
    rid = _stage(client, filename="danger.py", code=dangerous)
    r = client.post("/api/skill/approve", json={"review_id": rid})
    assert r.status_code == 400
    assert not (client._skills_dir / "danger.py").exists()
    assert (client._reviews_dir / f"{rid}.json").exists(), "blocked review must stay staged"
    assert client.get("/api/skill/reviews").json()["count"] == 1


# ── reject ────────────────────────────────────────────────────────────────────


def test_reject_discards_without_writing(client):
    rid = _stage(client)
    r = client.post(f"/api/skill/reject/{rid}")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "rejected"
    assert not (client._skills_dir / "moon_phase.py").exists(), "reject must not write a skill"
    assert not (client._reviews_dir / f"{rid}.json").exists(), "review file must be gone"
    assert client.get("/api/skill/reviews").json()["count"] == 0


def test_reject_unknown_returns_404(client):
    r = client.post("/api/skill/reject/nope-nope")
    assert r.status_code == 404


# ── traversal safety (unit) ───────────────────────────────────────────────────


def test_review_id_is_traversal_safe():
    assert skills_routes._safe_review_id("f47ac10b-58c") == "f47ac10b-58c"
    assert skills_routes._safe_review_id("../../etc/passwd") is None
    assert skills_routes._safe_review_id("a/b") is None
    assert skills_routes._safe_review_id("") is None
    assert skills_routes._review_path("../../etc/passwd") is None
