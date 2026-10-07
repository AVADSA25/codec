"""A refused page action shows the server's reason, not "HTTP 400" (docs/known-issues.md).

The shell's postJSON only read {error}; FastAPI's HTTPException answers {detail},
so an Inbox Grant or Resume refused by the agents routes said "HTTP 400".
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SHELL = (Path(__file__).resolve().parent.parent / "static" / "codec-shell.js").read_text(encoding="utf-8")


def _err_text_source() -> str:
    m = re.search(r"function errText\(d, status\) \{.*?\n  \}\n", SHELL, re.S)
    assert m, "errText is defined in the shell"
    return m.group(0)


def test_every_shell_request_helper_uses_the_reason():
    assert "throw new Error(d.error || ('HTTP ' + r.status))" not in SHELL
    assert SHELL.count("errText(d, r.status)") >= 2


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_the_reason_comes_from_error_detail_or_a_validation_list():
    cases = [({"error": "Not allowed here."}, 400), ({"detail": "agent must be paused"}, 409),
             ({"detail": [{"msg": "field required"}, {"msg": "bad value"}]}, 422), ({}, 500), (None, 502)]
    js = _err_text_source() + "console.log(JSON.stringify(%s.map(c => errText(c[0], c[1]))));" % json.dumps(cases)
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30, check=True).stdout
    assert json.loads(out) == ["Not allowed here.", "agent must be paused", "field required; bad value",
                               "HTTP 500", "HTTP 502"]
