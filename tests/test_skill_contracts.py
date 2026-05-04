"""Skill contract tests — validates every skill declares the required metadata.

Every skill file in skills/ must define:
  - SKILL_NAME (str)
  - SKILL_DESCRIPTION (str, >= 10 chars)
  - run(task: str, context: str = "") -> str

Optional but recommended:
  - SKILL_INPUT_EXAMPLES (list[str])   canonical prompts for smoke testing
  - SKILL_MCP_EXPOSE (bool)            True = expose via MCP, False = stdio-only

This test runs at CI without importing skill modules (AST-only), so it's
fast and dependency-free.
"""
import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILLS = REPO / "skills"

# Non-skill files that happen to live in skills/
NON_SKILL_FILES = {"codec.py"}


def _extract_meta(tree: ast.AST) -> dict:
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id.startswith("SKILL_"):
                    try:
                        out[t.id] = ast.literal_eval(node.value)
                    except Exception:
                        out[t.id] = "<unresolvable>"
    return out


def _has_run_function(tree: ast.AST) -> bool:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run":
            return True
    return False


def test_every_skill_has_valid_contract():
    failures = []
    for f in sorted(SKILLS.glob("*.py")):
        if f.name.startswith("_") or f.name in NON_SKILL_FILES:
            continue
        try:
            tree = ast.parse(f.read_text(), filename=str(f))
        except SyntaxError as e:
            failures.append(f"{f.name}: syntax error {e}")
            continue

        meta = _extract_meta(tree)
        name = meta.get("SKILL_NAME")
        desc = meta.get("SKILL_DESCRIPTION")

        if not isinstance(name, str) or not name:
            failures.append(f"{f.name}: missing or empty SKILL_NAME")
        if not isinstance(desc, str) or len(desc) < 10:
            failures.append(f"{f.name}: SKILL_DESCRIPTION must be str >= 10 chars")
        if not _has_run_function(tree):
            failures.append(f"{f.name}: missing run() function")

    assert not failures, "Skill contract violations:\n  " + "\n  ".join(failures)


if __name__ == "__main__":
    test_every_skill_has_valid_contract()
    print("All skill contracts valid.")
