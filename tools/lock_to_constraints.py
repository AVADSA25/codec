#!/usr/bin/env python3
"""Turn the buyer hash lock into a plain pip constraints file (CI helper, D-33).

    python tools/lock_to_constraints.py packaging/macos/requirements-lock.txt
    python tools/lock_to_constraints.py --neutral packaging/macos/requirements-lock.txt

Prints one `name==version` line per pinned package, without hashes, so CI can
install test tools (or, on Linux, requirements.txt) at the exact versions the
lock pins: `pip install -c constraints.txt ...`.

--neutral leaves out the packages that exist only for macOS (MLX and PyObjC).
The Linux job uses that set; the macOS job uses the full one. Nothing at
runtime reads this script.
"""
import argparse
import re
import sys

# Packages that publish macOS-only builds: MLX (Apple silicon) and the
# PyObjC bridge to Apple frameworks. Everything else in the lock is
# platform-neutral (pure Python, or wheels for Linux too).
MACOS_ONLY = re.compile(r"^(mlx|mlx-.+|pyobjc|pyobjc-.+)$")

PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)")


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def constraints(lock_text: str, neutral: bool) -> tuple[list[str], list[str]]:
    kept, dropped = [], []
    for line in lock_text.splitlines():
        m = PIN.match(line)
        if not m:
            continue
        name, version = canonical(m.group(1)), m.group(2)
        if neutral and MACOS_ONLY.match(name):
            dropped.append(name)
            continue
        kept.append(f"{name}=={version}")
    return kept, dropped


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("lock", help="hash lock file (pip requirements format)")
    ap.add_argument("--neutral", action="store_true",
                    help="leave out macOS-only packages (MLX, PyObjC)")
    args = ap.parse_args(argv)
    with open(args.lock, encoding="utf-8") as fh:
        kept, dropped = constraints(fh.read(), args.neutral)
    if not kept:
        print(f"no pinned packages found in {args.lock}", file=sys.stderr)
        return 1
    print(f"# Generated from {args.lock} by tools/lock_to_constraints.py"
          + (" --neutral" if args.neutral else ""))
    print("\n".join(kept))
    print(f"{len(kept)} constraints"
          + (f", left out (macOS only): {', '.join(dropped)}" if dropped else ""),
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
