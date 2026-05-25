"""Cross-cutting audit follow-up: a SCOPED strict mode for the skill-safety AST gate.

The default `is_dangerous_skill_code` is allow-by-omission (it misses network /
serialization / file-open primitives) — fine for hand-written user skills (the user curates
their own ~/.codec/skills/) and for python_exec. But the audit flagged that *autonomously
LLM-generated* skills deserve a stricter check. So:

  - `is_dangerous_skill_code(code, strict=False)` — DEFAULT IS UNCHANGED (zero impact on
    SkillRegistry load, python_exec, skill_forge, create_skill→approve).
  - `strict=True` additionally blocks network (urllib/http/requests/httpx/smtplib/ftplib/
    telnetlib), serialization (pickle/marshal/shelve), and the `open` builtin.
  - Applied ONLY at `codec_self_improve` (the nightly autonomous drafter) — user-invoked
    generation (skill_forge/create_skill) stays default so legitimate HTTP/file skills still
    work via the human-review flow.

Reference: docs/STRICT-AST-GATE-DESIGN.md.
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from codec_config import is_dangerous_skill_code  # noqa: E402


# ─── Default mode must be byte-for-byte unchanged (the safety property) ──────────

def test_default_mode_still_allows_network_and_open():
    # These are allow-by-omission in default mode — and MUST stay that way so user skills
    # + python_exec + user-invoked generation don't suddenly break.
    for src in ("import requests", "import urllib.request", "import pickle",
                "x = open('/tmp/f')"):
        bad, _ = is_dangerous_skill_code(src)
        assert not bad, f"default mode wrongly flagged: {src!r}"


def test_default_mode_still_blocks_base_dangerous():
    for src in ("import os", "import subprocess", "x = eval('1')"):
        bad, _ = is_dangerous_skill_code(src)
        assert bad, f"default mode should still block: {src!r}"


# ─── Strict mode blocks the rarely-legit primitives the base gate misses ─────────

def test_strict_blocks_serialization():
    for src in ("import pickle", "import marshal", "import shelve",
                "from pickle import loads"):
        bad, _ = is_dangerous_skill_code(src, strict=True)
        assert bad, f"strict should block deserialization-RCE: {src!r}"


def test_strict_blocks_legacy_exfil_protocols():
    for src in ("import smtplib", "import ftplib", "import telnetlib"):
        bad, _ = is_dangerous_skill_code(src, strict=True)
        assert bad, f"strict should block legacy exfil protocol: {src!r}"


def test_strict_deliberately_allows_http_and_open():
    # HTTP + file-open are common + legitimate; self_improve proposals are human-reviewed,
    # so these stay allowed even in strict (review is the control, not a blanket block).
    for src in ("import requests", "import httpx", "from urllib.request import urlopen",
                "x = open('/tmp/f')"):
        bad, _ = is_dangerous_skill_code(src, strict=True)
        assert not bad, f"strict should NOT block common-legit: {src!r}"


def test_strict_still_blocks_base():
    bad, _ = is_dangerous_skill_code("import os\nos.system('id')", strict=True)
    assert bad


def test_strict_allows_safe_skill_shape():
    safe = ("import json\nimport urllib.parse\n"
            "SKILL_NAME='x'\nSKILL_DESCRIPTION='y'\ndef run(task, app='', ctx=''):\n    return {}\n")
    bad, reason = is_dangerous_skill_code(safe, strict=True)
    assert not bad, reason  # json + urllib.parse stay safe even in strict


# ─── self_improve (autonomous drafter) wires strict mode ────────────────────────

def test_self_improve_validate_uses_strict():
    import pytest
    try:
        import codec_self_improve
    except Exception as e:  # optional deps unavailable on a bare CI runner
        pytest.skip(f"codec_self_improve import unavailable: {e}")
    # A strict-blocked primitive (pickle) in an autonomously-drafted skill is refused.
    pickle_skill = ("import pickle\nSKILL_NAME='load'\nSKILL_DESCRIPTION='d'\n"
                    "def run(task, app='', ctx=''):\n    return pickle.loads(task)\n")
    ok, reason = codec_self_improve._validate(pickle_skill)
    assert not ok, "self_improve must refuse an autonomously-drafted skill using pickle (strict)"
    # An HTTP skill stays allowed (proposals are human-reviewed; HTTP is common-legit).
    ok2, _ = codec_self_improve._validate(
        "import requests\nSKILL_NAME='x'\nSKILL_DESCRIPTION='y'\n"
        "def run(task, app='', ctx=''):\n    return requests.get(task).text\n")
    assert ok2, "self_improve should still accept an HTTP skill (review is the control)"
