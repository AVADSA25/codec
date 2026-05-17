"""Tests for codec.do_screenshot_question AppleScript safety (D-21 closure).

Closes audit finding D-21 (LOW) — `do_screenshot_question` interpolates
OCR text (potentially adversarial — a malicious webpage shows text
designed to break out of the string literal) into AppleScript with
only `"` and `\\n` escaped. AppleScript supports many other escapes
(`\\r`, `\\t`, `\\"`, `\\\\`, hex escapes) — the escape was insufficient.

PR-2F removes interpolation entirely. The OCR summary is passed as
an osascript ARGV argument; AppleScript reads it from `item 1 of argv`
inside the script body, NEVER concatenated into the script source.

Reference: docs/audits/PHASE-1-SECURITY.md finding D-21.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def test_codec_source_no_longer_interpolates_summary_into_script():
    """Source-level invariant: `do_screenshot_question` MUST NOT contain
    an f-string that interpolates `summary` into the AppleScript body.
    Belt-and-suspenders for the runtime tests below."""
    src = (REPO / "codec.py").read_text()
    # Find the function body
    idx = src.find("def do_screenshot_question")
    assert idx >= 0
    body = src[idx:idx + 3000]
    # The pre-PR-2F pattern was f'... "{summary}…\\n\\n{question}" ...'.
    # No f-string with {summary} interpolation may remain.
    assert 'f\'tell application "System Events"' not in body, (
        "Legacy f-string interpolation of summary into AppleScript must be removed"
    )
    # The new pattern uses `on run argv` + `item 1 of argv`
    assert "on run argv" in body, (
        "PR-2F pattern: AppleScript must read summary from argv, not interpolation"
    )
    assert "item 1 of argv" in body


def test_screenshot_question_passes_body_as_argv(monkeypatch):
    """Runtime test: when `do_screenshot_question` runs, the osascript
    invocation MUST pass the OCR body as a CLI argument (post-script),
    NOT interpolate it into the script source."""
    import codec as codec_mod

    captured = {"args": None, "stdout": ""}

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(args, **kwargs):
        captured["args"] = args
        return _Result()

    # Stub all the things do_screenshot_question touches
    monkeypatch.setattr(codec_mod, "push", lambda fn: None)
    monkeypatch.setattr(codec_mod, "show_overlay", lambda *a, **kw: None)
    adversarial = '" \n display dialog "PWNED" \n display dialog "'
    monkeypatch.setattr(codec_mod, "screenshot_ctx", lambda: adversarial)
    monkeypatch.setattr(codec_mod, "dispatch", lambda task: None)
    monkeypatch.setattr(codec_mod.subprocess, "run", fake_run)

    codec_mod.do_screenshot_question()

    assert captured["args"] is not None
    # argv shape: ["osascript", "-e", "<script>", "<body>"]
    assert captured["args"][0] == "osascript"
    assert captured["args"][1] == "-e"
    script = captured["args"][2]
    # The script body must NOT contain the adversarial text — that means
    # interpolation didn't happen.
    assert "PWNED" not in script, (
        f"Adversarial OCR text leaked into script body: {script[:200]!r}"
    )
    assert "display dialog" in script  # legitimate AppleScript verb
    # The body must be passed as argv[3]
    assert len(captured["args"]) >= 4
    body = captured["args"][3]
    assert "PWNED" in body, "Adversarial OCR text should appear in argv body (where AppleScript treats it as literal)"


def test_screenshot_question_argv_unescaped_quotes_safe(monkeypatch):
    """Adversarial OCR with bare quotes must arrive at argv as literal
    quotes — AppleScript's `item 1 of argv` handles the string boundary,
    not source-level escaping. Quotes in the OCR text must NOT escape
    the script's string context."""
    import codec as codec_mod

    captured = {"args": None}

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(args, **kwargs):
        captured["args"] = args
        return _Result()

    monkeypatch.setattr(codec_mod, "push", lambda fn: None)
    monkeypatch.setattr(codec_mod, "show_overlay", lambda *a, **kw: None)
    monkeypatch.setattr(codec_mod, "screenshot_ctx",
                         lambda: 'OCR with "double" and \'single\' quotes')
    monkeypatch.setattr(codec_mod, "dispatch", lambda task: None)
    monkeypatch.setattr(codec_mod.subprocess, "run", fake_run)

    codec_mod.do_screenshot_question()

    assert captured["args"] is not None
    script = captured["args"][2]
    body = captured["args"][3]
    # Script source must not contain the OCR — proves no interpolation
    assert '"double"' not in script
    # But argv body MUST contain it (literal)
    assert "double" in body or "OCR" in body


def test_screenshot_question_no_screen_context_early_returns(monkeypatch):
    """Sanity: when `screenshot_ctx` returns empty, no subprocess fires."""
    import codec as codec_mod
    fired = {"run": 0}

    def fake_run(*a, **kw):
        fired["run"] += 1
        raise AssertionError("subprocess.run must not fire on empty ctx")

    monkeypatch.setattr(codec_mod, "push", lambda fn: None)
    monkeypatch.setattr(codec_mod, "show_overlay", lambda *a, **kw: None)
    monkeypatch.setattr(codec_mod, "screenshot_ctx", lambda: "")
    monkeypatch.setattr(codec_mod.subprocess, "run", fake_run)

    codec_mod.do_screenshot_question()
    assert fired["run"] == 0
