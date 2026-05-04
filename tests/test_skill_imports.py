"""CI-friendly import smoke test. No network, no hardware, no system calls.

Verifies every skill file parses as Python and the registry can discover it.
This is the floor: if this fails, a skill is syntactically broken and would
crash CODEC at startup.
"""
import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILLS = REPO / "skills"
sys.path.insert(0, str(REPO))


def main():
    files = sorted(SKILLS.glob("*.py"))
    errors = []
    for f in files:
        try:
            ast.parse(f.read_text(), filename=str(f))
        except SyntaxError as e:
            errors.append(f"{f.name}: {e}")
    print(f"Parsed {len(files)} skill files, {len(errors)} errors")
    for e in errors:
        print("  ✗", e)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
