"""Regression guard: no bare `except:` in production code (A-22 / PR-3B-2).

Bare `except:` also catches KeyboardInterrupt / SystemExit / GeneratorExit,
so Ctrl-C and clean shutdown can be silently swallowed. PR-3B-2 converted
all 36 production bare-excepts to `except Exception:`. This AST-based check
pins that — it only sees real code (string-template `except:` inside the
deprecated build_session_script generator is invisible to the AST walker),
and skips the tests/ tree.

Reference: docs/audits/PHASE-1-CODE-QUALITY.md finding A-22.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _production_py_files():
    """All top-level + routes/ + skills/ .py files, excluding tests + vendored."""
    files = []
    files += sorted(REPO.glob("*.py"))
    files += sorted((REPO / "routes").glob("*.py"))
    files += sorted((REPO / "skills").glob("*.py"))
    return [f for f in files if "test" not in f.name.lower()]


def _bare_except_lines(path: Path):
    """Return line numbers of bare `except:` (ExceptHandler with no type)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, OSError):
        return []  # unparseable files aren't this test's concern
    return [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.ExceptHandler) and n.type is None]


def test_no_bare_except_in_production():
    offenders = {}
    for f in _production_py_files():
        lines = _bare_except_lines(f)
        if lines:
            offenders[f.relative_to(REPO).as_posix()] = lines
    assert not offenders, (
        "Bare `except:` found in production code (use `except Exception:` so "
        f"KeyboardInterrupt/SystemExit propagate): {offenders}"
    )
