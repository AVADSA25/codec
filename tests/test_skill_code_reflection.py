"""Tests for is_dangerous_skill_code reflection hardening (D-17 closure).

Closes audit finding D-17 (MEDIUM) — the AST validator missed runtime
reflection sandbox-escapes that build dangerous calls dynamically:
  - `(lambda x: x.__class__.__bases__[0].__subclasses__())(0)`
  - `__builtins__.__dict__["eval"](src)`
  - f-string `__mro__` / `__globals__` chains
  - `vars`/`dir` reflection
  - `from urllib.request import urlopen` network exfil

PR-2H adds: reflection-attribute blocking (any __class__/__bases__/
__subclasses__/__mro__/__globals__/__dict__/__builtins__/__code__/...),
bare __builtins__ Name detection, vars+dir in DANGEROUS_CALLS, and
network modules in DANGEROUS_MODULES.

`open` is intentionally NOT blocked (legitimate file skills need it;
runtime file access is constrained by python_exec's sandbox-exec profile
and file_ops/file_write path blocklists). Documented residual.

Reference: docs/audits/PHASE-1-SECURITY.md finding D-17.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from codec_config import is_dangerous_skill_code  # noqa: E402


# ── Reflection sandbox-escapes — must now be caught ──────────────────────────

_REFLECTION_ESCAPES = [
    # classic object-subclasses walk
    "(lambda x: x.__class__.__bases__[0].__subclasses__())(0)",
    "().__class__.__bases__[0].__subclasses__()",
    "x = type(0).__mro__[1].__subclasses__()",
    # __globals__ pivot to os
    "f().__globals__['os'].system('rm -rf /')",
    # __builtins__ dict subscript
    "__builtins__.__dict__['eval']('1+1')",
    "__builtins__['eval']('1+1')",
    # bare __builtins__ reference
    "b = __builtins__",
    # __code__ / __closure__ introspection
    "f.__code__.co_consts",
    # f-string reflection chain
    'x = f"{type(0).__class__.__mro__[1].__subclasses__()}"',
]


@pytest.mark.parametrize("code", _REFLECTION_ESCAPES)
def test_reflection_escapes_blocked(code):
    dangerous, reason = is_dangerous_skill_code(code)
    assert dangerous is True, f"Reflection escape must be blocked: {code!r} (reason={reason!r})"
    assert reason, "A blocked reflection must carry a non-empty reason"


def test_vars_now_in_dangerous_calls():
    """`vars(__builtins__)['__import__']` — `vars` must now be blocked."""
    dangerous, reason = is_dangerous_skill_code(
        "vars(__builtins__)['__import__']('os')"
    )
    assert dangerous is True
    assert "vars" in reason.lower() or "builtins" in reason.lower()


def test_dir_now_in_dangerous_calls():
    dangerous, reason = is_dangerous_skill_code("dir(__builtins__)")
    assert dangerous is True


def test_socket_still_blocked():
    """`socket` was already in DANGEROUS_MODULES pre-PR-2H — regression."""
    dangerous, _ = is_dangerous_skill_code("import socket")
    assert dangerous is True


def test_network_libs_intentionally_allowed():
    """Scope decision (PR-2H): `requests` / `urllib.request` / `http.client`
    are NOT blocked at the AST gate. Skills legitimately make HTTP calls
    (weather, web_search, self_improve-drafted API skills); network for
    UNTRUSTED python_exec is blocked at runtime by sandbox-exec (PR-2C).
    Comprehensive network gating is deferred to the audit's future
    positive-allowlist rewrite. This test pins the deliberate decision so a
    future change to block them is intentional, not accidental."""
    for code in ("import requests", "from urllib.request import urlopen",
                 "import http.client"):
        dangerous, _ = is_dangerous_skill_code(code)
        assert dangerous is False, (
            f"PR-2H scopes network out; this should stay allowed: {code!r}"
        )


def test_urllib_parse_still_allowed():
    """`urllib.parse` (URL encoding, no network) stays allowed."""
    dangerous, reason = is_dangerous_skill_code(
        "from urllib.parse import quote\nx = quote('a b')"
    )
    assert dangerous is False, f"urllib.parse must stay allowed; got {reason!r}"


# ── Existing detections must still work (regression) ─────────────────────────

_STILL_DANGEROUS = [
    "import os",
    "import subprocess",
    "from subprocess import run",
    "eval('1+1')",
    "exec('x=1')",
    "__import__('os')",
    "getattr(__builtins__, 'eval')",
    "import os\nos.system('ls')",
    "import ctypes",
    "import socket",
]


@pytest.mark.parametrize("code", _STILL_DANGEROUS)
def test_existing_detections_regression(code):
    dangerous, _ = is_dangerous_skill_code(code)
    assert dangerous is True, f"Regression — must stay blocked: {code!r}"


# ── Safe skill code must NOT be flagged (UX guard) ───────────────────────────

_SAFE_SKILL_CODE = [
    "import json\nx = json.dumps({'a': 1})",
    "import re\nm = re.match('a', 'abc')",
    "import math\ny = math.sqrt(2)",
    "from datetime import datetime\nd = datetime.now()",
    "import collections\nc = collections.Counter()",
    "from urllib.parse import urlencode\ns = urlencode({'q': 'x'})",
    "def run(task, app='', ctx=''):\n    return task.upper()",
    "data = [1, 2, 3]\ntotal = sum(data)",
    # open() is intentionally allowed for legitimate file skills
    "with open('/tmp/x.txt') as f:\n    content = f.read()",
    "result = 'hello'.replace('l', 'L')",
]


@pytest.mark.parametrize("code", _SAFE_SKILL_CODE)
def test_safe_skill_code_not_flagged(code):
    dangerous, reason = is_dangerous_skill_code(code)
    assert dangerous is False, (
        f"Safe skill code must NOT be flagged (UX guard): {code!r} (reason={reason!r})"
    )


def test_open_intentionally_allowed():
    """Documented residual: `open` is NOT blocked at the AST gate. Legit file
    skills need it; runtime file access is constrained elsewhere (python_exec
    sandbox-exec, file_ops/file_write path blocklists)."""
    dangerous, _ = is_dangerous_skill_code("open('/tmp/notes.txt').read()")
    assert dangerous is False


def test_syntax_error_blocked():
    """Unparseable code is treated as dangerous (fail-closed)."""
    dangerous, reason = is_dangerous_skill_code("def (:\n  pass")
    assert dangerous is True
    assert "syntax" in reason.lower()


def test_returns_tuple_bool_str():
    """Contract: returns (bool, str)."""
    result = is_dangerous_skill_code("x = 1")
    assert isinstance(result, tuple) and len(result) == 2
    assert isinstance(result[0], bool)
    assert isinstance(result[1], str)
