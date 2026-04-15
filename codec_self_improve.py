"""CODEC Self-Improvement — nightly gap analyzer.

Replays yesterday's audit.log. For every high-signal gap, asks Qwen to DRAFT
a proposal for a new or improved skill. Proposals are staged in
~/.codec/skill_proposals/ for human review — NEVER auto-deployed.

Signals detected:
  1. Unknown-tool calls (Claude tried a tool name that doesn't exist) —
     strong signal the user wants capability X but CODEC can't do it yet.
  2. Repeatedly failing tools (≥3 errors, ≥40% error rate) — candidate for
     rewrite or wrapper.
  3. Repeated timeouts on same tool — candidate for async/retry wrapper.

Output: one markdown file per proposal at
   ~/.codec/skill_proposals/YYYY-MM-DD/<name>.md
containing: rationale, example failing calls, proposed code, validation status.

Safety:
  - Generated code is validated via codec_config.is_dangerous_skill_code
  - Limit: 3 proposals per run
  - Never writes to skills/ directly — review via scripts/promote_skill.py

Run:  python3 codec_self_improve.py
Auto: wire into autopilot.json at a nightly time.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

_REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO))

from codec_config import QWEN_BASE_URL, QWEN_MODEL, SKILLS_DIR, is_dangerous_skill_code
from codec_retry import retry_post

_CODEC = Path(os.path.expanduser("~/.codec"))
_PROPOSALS_ROOT = _CODEC / "skill_proposals"
_PROPOSALS_ROOT.mkdir(parents=True, exist_ok=True)

MAX_PROPOSALS_PER_RUN = 3


def _existing_skill_names() -> set[str]:
    out = set()
    for f in Path(SKILLS_DIR).glob("*.py"):
        if f.name.startswith("_") or f.name == "codec.py":
            continue
        out.add(f.stem)
        # Also pick up explicit SKILL_NAME
        m = re.search(r'^SKILL_NAME\s*=\s*["\'](.+?)["\']', f.read_text(), re.MULTILINE)
        if m:
            out.add(m.group(1))
    return out


def _load_audit_for(date_str: str) -> list[dict]:
    paths = [_CODEC / f"audit.log.{date_str}"]
    # Also include today's live log when the date_str is today
    if date_str == datetime.now(timezone.utc).date().isoformat():
        paths.append(_CODEC / "audit.log")
    out = []
    for p in paths:
        if not p.exists():
            continue
        for line in p.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def _find_gaps(records: list[dict], existing: set[str]) -> list[dict]:
    """Return list of gap descriptors, each a dict with kind/tool/count/examples."""
    by_tool: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_tool[r.get("tool", "unknown")].append(r)

    gaps = []

    # Kind 1: unknown tool name called
    unknown_tool_calls = Counter()
    for tool in by_tool:
        if tool and tool != "unknown" and tool not in existing:
            unknown_tool_calls[tool] = len(by_tool[tool])
    for tool, count in unknown_tool_calls.most_common():
        if count >= 2:
            gaps.append({
                "kind": "missing_tool",
                "tool": tool,
                "count": count,
                "examples": [
                    {"ts": r.get("ts"), "error": r.get("error_type")}
                    for r in by_tool[tool][:3]
                ],
            })

    # Kind 2: high-error rate
    for tool, rs in by_tool.items():
        if tool in ("unknown", ""):
            continue
        n = len(rs)
        errors = [r for r in rs if r.get("outcome") in ("error", "timeout")]
        if n >= 5 and len(errors) / n >= 0.4:
            gaps.append({
                "kind": "unreliable_tool",
                "tool": tool,
                "count": len(errors),
                "total": n,
                "examples": [
                    {"ts": r.get("ts"), "error": r.get("error_type"),
                     "outcome": r.get("outcome")}
                    for r in errors[:3]
                ],
            })

    # Kind 3: repeat timeouts
    for tool, rs in by_tool.items():
        tos = [r for r in rs if r.get("outcome") == "timeout"]
        if len(tos) >= 3:
            gaps.append({
                "kind": "timeout_prone",
                "tool": tool,
                "count": len(tos),
                "examples": [{"ts": r.get("ts")} for r in tos[:3]],
            })

    return gaps[:MAX_PROPOSALS_PER_RUN]


_DRAFT_PROMPT = """You are drafting a CODEC skill proposal. Output MUST be valid Python.

Gap detected:
  Kind:     {kind}
  Tool:     {tool}
  Evidence: {evidence}

Write a complete CODEC skill file that addresses this gap.

Required structure:
    \"\"\"CODEC Skill: <Name>\"\"\"
    SKILL_NAME = "<snake_case_name>"
    SKILL_DESCRIPTION = "<one sentence, starts with a verb>"
    SKILL_TRIGGERS = ["<phrase>", "<phrase>"]
    SKILL_MCP_EXPOSE = True

    # imports — stdlib + requests ONLY. No os.system, subprocess, eval, exec.

    def run(task: str, context: str = "") -> str:
        # implementation — return a string
        ...

Rules:
- NO subprocess, os.system, eval, exec, __import__, ctypes, shutil.rmtree
- Timeouts on any network call (<=10s)
- Handle errors gracefully — return "<skill> failed: <reason>" rather than raising
- Keep under 80 lines
- If addressing a missing_tool gap, infer intent from the tool name
- If addressing an unreliable_tool gap, wrap with retries + better error messages
- If addressing timeout_prone, add async/shorter timeouts + clearer failure modes

Output ONLY the Python code, no fences, no commentary."""


def _draft_skill(gap: dict) -> tuple[str, str] | None:
    """Ask Qwen to draft a skill. Returns (suggested_name, code) or None."""
    evidence = json.dumps(gap.get("examples", []), default=str)[:400]
    prompt = _DRAFT_PROMPT.format(
        kind=gap["kind"], tool=gap["tool"], evidence=evidence
    )
    try:
        r = retry_post(
            f"{QWEN_BASE_URL}/chat/completions",
            json={
                "model": QWEN_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 900,
            },
            timeout=60,
            max_attempts=3,
        )
        r.raise_for_status()
        code = r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return None
    # Strip code fences if LLM included them
    code = re.sub(r"^```(?:python)?|```$", "", code, flags=re.MULTILINE).strip()
    # Extract SKILL_NAME
    m = re.search(r'^SKILL_NAME\s*=\s*["\'](.+?)["\']', code, re.MULTILINE)
    if not m:
        return None
    return m.group(1), code


def _validate(code: str) -> tuple[bool, str]:
    """Layered check: must compile, must not match dangerous patterns."""
    try:
        compile(code, "<proposal>", "exec")
    except SyntaxError as e:
        return False, f"SyntaxError: {e}"
    dangerous, reason = is_dangerous_skill_code(code)
    if dangerous:
        return False, reason
    if "def run(" not in code or "SKILL_NAME" not in code or "SKILL_DESCRIPTION" not in code:
        return False, "Missing required skill metadata"
    return True, ""


def _write_proposal(out_dir: Path, name: str, code: str, gap: dict, ok: bool, why: str):
    out_dir.mkdir(parents=True, exist_ok=True)
    md = out_dir / f"{name}.md"
    py = out_dir / f"{name}.py"
    py.write_text(code)
    md.write_text(
        f"# Proposal: `{name}`\n\n"
        f"**Generated:** {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
        f"**Gap kind:** {gap['kind']}\n"
        f"**Triggering tool:** {gap['tool']}\n"
        f"**Signal count:** {gap.get('count')}\n\n"
        f"## Validation\n\n"
        f"- Status: {'✅ PASSED' if ok else '❌ REJECTED'}\n"
        f"- Reason: {why or 'clean'}\n\n"
        f"## Evidence\n\n"
        f"```json\n{json.dumps(gap.get('examples', []), indent=2, default=str)}\n```\n\n"
        f"## Proposed code\n\n"
        f"See `{name}.py` (next to this file).\n\n"
        f"## To accept\n\n"
        f"```\npython3 scripts/promote_skill.py {name}\n```\n"
    )


def run_once(target_date: str | None = None) -> str:
    if target_date is None:
        target_date = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()

    out_dir = _PROPOSALS_ROOT / target_date
    records = _load_audit_for(target_date)
    if not records:
        return f"No audit records for {target_date} — nothing to analyze."

    existing = _existing_skill_names()
    gaps = _find_gaps(records, existing)
    if not gaps:
        return f"[{target_date}] No gaps detected in {len(records)} records — all systems nominal."

    written = []
    for gap in gaps:
        drafted = _draft_skill(gap)
        if drafted is None:
            continue
        name, code = drafted
        if name in existing:
            name = f"{name}_v2"
        ok, why = _validate(code)
        _write_proposal(out_dir, name, code, gap, ok, why)
        written.append((name, ok))

    lines = [f"[{target_date}] Analyzed {len(records)} records, {len(gaps)} gaps, drafted {len(written)} proposal(s):"]
    for name, ok in written:
        lines.append(f"  {'✓' if ok else '✗'} {name}")
    lines.append(f"\nReview: ls {out_dir}")
    lines.append(f"Promote: python3 scripts/promote_skill.py <name>")
    return "\n".join(lines)


if __name__ == "__main__":
    print(run_once(sys.argv[1] if len(sys.argv) > 1 else None))
