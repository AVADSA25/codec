"""Fix #10: CI guard for the A-12 invariant.

A-12 (see AGENTS.md §2) routed every chat/completions *text* call site onto
codec_llm. The only inline `requests.post(.../chat/completions)` calls left
are vision sites (pending A-11 cleanup) and codec_core's generated session
script. This guard fails if a NEW inline chat/completions POST appears
anywhere else — i.e. someone bypassed codec_llm.

The detector matches the precise anti-pattern: a `.post(` whose first argument
literally contains `chat/completions`. URL-in-a-variable callers (codec_llm,
codec_vision) don't match and don't need allowlisting; that's intended — the
guard targets the literal inline-POST shape that bypasses the canonical caller.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Files permitted to contain an inline `.post(...chat/completions...)`.
# Every entry is a vision site (pending A-11 migration onto codec_vision) or
# codec_core's build_session_script, which EMITS the call as a string into the
# generated session script (not a live POST in codec_core itself).
_ALLOWLIST = {
    "codec_dashboard.py",        # screen-vision POSTs (A-11 pending)
    "codec_watcher.py",          # screen-vision POST (A-11 pending)
    "codec_imessage.py",         # bridge vision POST (A-11 pending)
    "codec_telegram.py",         # bridge vision POST (A-11 pending)
    "skills/screenshot_text.py",  # OCR vision POST (A-11 pending)
    "codec_core.py",             # generated session-script string, not a live POST
}

_INLINE_POST_RE = re.compile(r"\.post\s*\([^)]*chat/completions")
_SKIP_PREFIXES = ("tests/", ".claude/", "scripts/")


def _scan(root: Path) -> set:
    """Return the set of repo-relative .py paths containing an inline
    chat/completions POST."""
    found = set()
    for p in root.rglob("*.py"):
        rel = p.relative_to(root).as_posix()
        if rel.startswith(_SKIP_PREFIXES) or "__pycache__" in rel:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if _INLINE_POST_RE.search(text):
            found.add(rel)
    return found


def test_no_new_inline_llm_post_outside_codec_llm():
    found = _scan(REPO)
    offenders = found - _ALLOWLIST
    assert not offenders, (
        "New inline chat/completions POST(s) outside codec_llm "
        f"(A-12 invariant violated): {sorted(offenders)}.\n"
        "Route LLM text calls through codec_llm.call/stream/acall/astream. "
        "If this is a legitimate vision site pending A-11, add it to the "
        "documented _ALLOWLIST in this test with a reason."
    )


def test_a12_guard_actually_detects_a_violation(tmp_path):
    # Proves the detector is not a no-op: a synthetic rogue inline POST is found.
    (tmp_path / "rogue_skill.py").write_text(
        "import requests\n"
        "def run(t):\n"
        '    return requests.post("http://127.0.0.1:8090/v1/chat/completions", json={}).text\n'
    )
    (tmp_path / "innocent.py").write_text("x = 1\n")
    found = _scan(tmp_path)
    assert "rogue_skill.py" in found, "guard failed to detect an inline chat/completions POST"
    assert "innocent.py" not in found, "guard false-positived on an unrelated file"
