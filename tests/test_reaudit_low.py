"""re-audit LOW cluster: N19 (AST gate misses `import builtins`) + N20
(_pinned_builtin_names fails OPEN on a manifest read error, so the skill-approve
guard blocks nothing during a transient failure)."""
import asyncio

import routes.skills as sr

_BENIGN = (
    'SKILL_NAME = "x"\n'
    'SKILL_DESCRIPTION = "x"\n'
    'SKILL_TRIGGERS = []\n'
    'def run(task, app="", ctx=""):\n'
    '    return "ok"\n'
)


class _Req:
    def __init__(self, payload):
        self._p = payload

    async def json(self):
        return self._p


# ── N19: builtins.exec/eval bypassed the AST gate (builtins not a dangerous module) ──
def test_ast_gate_flags_builtins_import():
    from codec_config import is_dangerous_skill_code

    bad, reason = is_dangerous_skill_code("import builtins\nbuiltins.exec('1')")
    assert bad is True, f"`import builtins` must be flagged (AST gate bypass); reason={reason!r}"


def test_ast_gate_still_allows_safe_code():
    from codec_config import is_dangerous_skill_code

    bad, _ = is_dangerous_skill_code("import json\nx = json.dumps({'a': 1})\n")
    assert bad is False, "benign stdlib code must still pass"


# ── N20: skill_approve must FAIL CLOSED when the pinned-builtin manifest can't be read ──
def test_skill_approve_fails_closed_when_manifest_unreadable(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "_get_skills_dir", lambda: str(tmp_path))
    monkeypatch.setattr(sr, "_pinned_builtin_names", lambda: None)  # simulate read failure
    rid = "rev_failclosed"
    sr._pending_skills[rid] = {"code": _BENIGN, "filename": "my_helper.py"}
    resp = asyncio.run(sr.skill_approve(_Req({"review_id": rid})))
    status = getattr(resp, "status_code", 200)
    assert status == 400, "approve must fail closed when the pinned-builtin manifest can't be verified"
    assert not (tmp_path / "my_helper.py").exists()
