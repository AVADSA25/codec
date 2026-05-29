"""Fix #7b (H2/H6): skill approve must not overwrite/shadow a hash-pinned built-in.

The review-and-approve flow (routes/skills.py) writes an approved skill to the
skills dir by basename. Without a guard, an approved skill named after a
manifest-pinned built-in (e.g. calculator.py, file_write.py) takes that
trusted name — shadowing the real built-in (or overwriting it if the write
dir is the repo skills dir). The approve gate must refuse pinned names.
"""
import asyncio

import routes.skills as skills_routes


class _FakeReq:
    def __init__(self, payload):
        self._p = payload

    async def json(self):
        return self._p


_BENIGN = (
    'SKILL_NAME = "x"\n'
    'SKILL_DESCRIPTION = "x"\n'
    'SKILL_TRIGGERS = []\n'
    'def run(task, app="", ctx=""):\n'
    '    return "ok"\n'
)


def _approve(payload):
    return asyncio.run(skills_routes.skill_approve(_FakeReq(payload)))


def test_skill_approve_refuses_pinned_builtin_name(tmp_path, monkeypatch):
    monkeypatch.setattr(skills_routes, "_get_skills_dir", lambda: str(tmp_path))
    rid = "rev_pinned"
    skills_routes._pending_skills[rid] = {"code": _BENIGN, "filename": "calculator.py"}
    resp = _approve({"review_id": rid})
    status = getattr(resp, "status_code", 200)
    assert status == 400, f"approving a pinned built-in name must be 400, got {status}: {resp}"
    assert not (tmp_path / "calculator.py").exists(), "a pinned built-in was written to disk"


def test_skill_approve_allows_non_pinned_name(tmp_path, monkeypatch):
    monkeypatch.setattr(skills_routes, "_get_skills_dir", lambda: str(tmp_path))
    rid = "rev_ok"
    skills_routes._pending_skills[rid] = {"code": _BENIGN, "filename": "my_unique_helper.py"}
    resp = _approve({"review_id": rid})
    status = getattr(resp, "status_code", 200)
    assert status == 200, f"a non-pinned skill should approve, got {status}: {resp}"
    assert (tmp_path / "my_unique_helper.py").exists()
