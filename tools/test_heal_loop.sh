#!/bin/bash
# test_heal_loop.sh — self-healing test loop for CODEC.
#
# The until-pattern: keep invoking headless Claude until the suite is green.
# Each iteration makes ONE focused fix, then the suite re-runs. Ctrl-C to stop.
#
#   ./tools/test_heal_loop.sh              # full suite
#   ./tools/test_heal_loop.sh tests/test_overlays.py   # scope to a file
#
# Notes:
#  - Tests are the spec: Claude is told to fix CODE, never tests.
#  - If a built-in skill file is edited, the PR-1A manifest must be regenerated
#    or the skill silently fails to load — the prompt covers it, and the tool is
#    allowlisted.
#  - MAX_ITERS guards against an infinite burn if something is truly wedged.

set -u
cd "$(dirname "$0")/.."

SCOPE="${1:-}"
MAX_ITERS="${MAX_ITERS:-8}"
PYTEST_CMD="python3 -m pytest -q -x ${SCOPE}"

i=0
until $PYTEST_CMD; do
  i=$((i + 1))
  if [ "$i" -gt "$MAX_ITERS" ]; then
    echo "[heal-loop] still red after ${MAX_ITERS} iterations — stopping for a human."
    exit 1
  fi
  echo ""
  echo "[heal-loop] suite RED — iteration ${i}/${MAX_ITERS}: asking Claude for one focused fix..."
  claude -p "The CODEC test suite is failing. Run '${PYTEST_CMD}', find the root cause, and fix the CODE — do not edit the tests. Make ONE focused change. CODEC rule: if you edit any file under skills/, you MUST run 'python3 tools/generate_skill_manifest.py --write' afterward or the skill will be refused at load time." \
    --allowedTools "Bash(python3 -m pytest*),Bash(python3 tools/generate_skill_manifest.py*),Edit,Read,Grep,Glob" \
    --max-turns 30
done

echo ""
echo "[heal-loop] ✅ suite GREEN after ${i} fix iteration(s)."
