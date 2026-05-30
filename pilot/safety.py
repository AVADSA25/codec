"""Pilot PP-11 (audit P-3): minimal AST safety gate for auto-compiled skills.

Pilot is a separate repo and can't cleanly import the parent's
`codec_config.is_dangerous_skill_code`, so this vendors a minimal equivalent —
the same allow-by-denylist AST walk — to run at skill-approve time. Defense in
depth: PP-2 already stops the compiler from emitting injected code, and the
parent SkillRegistry AST-checks non-manifest skills at load; this fails fast at
approve, before a dangerous file ever reaches ~/.codec/skills/.
"""
from __future__ import annotations

import ast

_DANGEROUS_MODULES = {"os", "subprocess", "ctypes", "shutil", "importlib",
                      "signal", "pty", "socket"}
_DANGEROUS_CALLS = {"eval", "exec", "compile", "__import__", "globals", "locals",
                    "getattr", "setattr", "delattr", "vars"}


def is_dangerous_skill_code(code: str) -> tuple[bool, str]:
    """Return (dangerous, reason). A syntax error counts as dangerous (won't run a
    file we can't parse). Mirrors the parent gate's module/call denylist."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return (True, f"syntax error: {e}")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _DANGEROUS_MODULES:
                    return (True, f"dangerous import: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in _DANGEROUS_MODULES:
                return (True, f"dangerous import: from {node.module}")
        elif isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id in _DANGEROUS_CALLS:
                return (True, f"dangerous call: {f.id}()")
    return (False, "")
