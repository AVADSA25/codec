"""Review and promote a proposed skill from ~/.codec/skill_proposals/ to skills/.

Usage:
    python3 scripts/promote_skill.py                    # list pending
    python3 scripts/promote_skill.py <name>             # review + promote
    python3 scripts/promote_skill.py <name> --reject    # delete proposal
    python3 scripts/promote_skill.py --list-dates       # show review dirs
"""
import argparse
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILLS = REPO / "skills"
PROPOSALS = Path.home() / ".codec" / "skill_proposals"


def _find_proposal(name: str) -> Path | None:
    if not PROPOSALS.exists():
        return None
    for date_dir in sorted(PROPOSALS.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue
        py = date_dir / f"{name}.py"
        if py.exists():
            return py
    return None


def _list_pending():
    if not PROPOSALS.exists():
        print("No proposals directory yet — run self_improve first.")
        return
    found = []
    for date_dir in sorted(PROPOSALS.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue
        for py in date_dir.glob("*.py"):
            md = py.with_suffix(".md")
            status = "unknown"
            if md.exists():
                t = md.read_text()
                if "✅ PASSED" in t:
                    status = "✅ valid"
                elif "❌ REJECTED" in t:
                    status = "❌ rejected"
            found.append((date_dir.name, py.stem, status))
    if not found:
        print("No pending proposals.")
        return
    print(f"{'Date':<12} {'Status':<14} Name")
    print("-" * 50)
    for date, name, status in found:
        print(f"{date:<12} {status:<14} {name}")
    print(f"\nReview:  open {PROPOSALS}/<date>/<name>.py")
    print(f"Promote: python3 scripts/promote_skill.py <name>")


def _promote(name: str) -> int:
    src = _find_proposal(name)
    if src is None:
        print(f"✗ No proposal named {name}", file=sys.stderr)
        return 1

    md = src.with_suffix(".md")
    if md.exists() and "❌ REJECTED" in md.read_text():
        print(f"✗ {name} was rejected during validation — aborting promotion.")
        print(f"  Inspect: {md}")
        return 1

    dest = SKILLS / f"{name}.py"
    if dest.exists():
        print(f"✗ {dest} already exists. Rename the proposal or remove existing first.")
        return 1

    # Final safety: recompile + AST scan
    from codec_config import is_dangerous_skill_code
    code = src.read_text()
    try:
        compile(code, str(dest), "exec")
    except SyntaxError as e:
        print(f"✗ Syntax error — cannot promote: {e}")
        return 1
    dangerous, reason = is_dangerous_skill_code(code)
    if dangerous:
        print(f"✗ Safety check failed: {reason}")
        return 1

    shutil.copy2(src, dest)
    print(f"✓ Promoted {name} → {dest}")
    print("  Next: pm2 restart codec-mcp-http  # reload registry")
    return 0


def _reject(name: str) -> int:
    src = _find_proposal(name)
    if src is None:
        print(f"✗ No proposal named {name}", file=sys.stderr)
        return 1
    md = src.with_suffix(".md")
    src.unlink(missing_ok=True)
    md.unlink(missing_ok=True)
    print(f"✓ Rejected + deleted proposal {name}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name", nargs="?")
    ap.add_argument("--reject", action="store_true")
    ap.add_argument("--list-dates", action="store_true")
    args = ap.parse_args()

    if args.name is None or args.list_dates:
        _list_pending()
        sys.exit(0)

    if args.reject:
        sys.exit(_reject(args.name))
    sys.exit(_promote(args.name))


if __name__ == "__main__":
    sys.path.insert(0, str(REPO))
    main()
