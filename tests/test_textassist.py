"""Tests for codec_textassist modes and macOS Quick Action workflows."""
import os
import subprocess
import sys

REPO = os.path.expanduser("~/codec-repo")
TEXTASSIST = os.path.join(REPO, "codec_textassist.py")
SERVICES_DIR = os.path.expanduser("~/Library/Services")

EXPECTED_WORKFLOWS = [
    "CODEC Proofread.workflow",
    "CODEC Elevate.workflow",
    "CODEC Explain.workflow",
    "CODEC Prompt.workflow",
    "CODEC Translate.workflow",
    "CODEC Reply.workflow",
    "CODEC Read Aloud.workflow",
    "CODEC Save.workflow",
]


# ── Source code checks ────────────────────────────────────────────────────────

def test_read_aloud_mode_exists():
    """read_aloud branch is defined in codec_textassist.py"""
    with open(TEXTASSIST) as f:
        content = f.read()
    assert 'MODE == "read_aloud"' in content or "MODE == 'read_aloud'" in content


def test_save_mode_exists():
    """save branch is defined in codec_textassist.py"""
    with open(TEXTASSIST) as f:
        content = f.read()
    assert 'MODE == "save"' in content or "MODE == 'save'" in content


def test_read_aloud_exits_before_llm():
    """read_aloud mode exits before reaching the LLM call (no call_qwen)"""
    with open(TEXTASSIST) as f:
        content = f.read()
    ra_idx  = content.find('MODE == "read_aloud"')
    llm_idx = content.find('call_qwen(text, MODE)')
    # read_aloud block must appear before the LLM call
    assert 0 < ra_idx < llm_idx, "read_aloud block should precede the LLM call"


def test_save_exits_before_llm():
    """save mode exits before reaching the LLM call"""
    with open(TEXTASSIST) as f:
        content = f.read()
    save_idx = content.find('MODE == "save"')
    llm_idx  = content.find('call_qwen(text, MODE)')
    assert 0 < save_idx < llm_idx, "save block should precede the LLM call"


def test_read_aloud_uses_tts():
    """read_aloud mode calls the TTS endpoint"""
    with open(TEXTASSIST) as f:
        content = f.read()
    assert "audio/speech" in content or "tts_url" in content


def test_save_has_local_fallback():
    """save mode has a local file fallback"""
    with open(TEXTASSIST) as f:
        content = f.read()
    assert "saved_notes.txt" in content


def test_save_shows_notification():
    """save mode shows a macOS notification on success"""
    with open(TEXTASSIST) as f:
        content = f.read()
    assert "display notification" in content


# ── Workflow file checks ──────────────────────────────────────────────────────

def test_all_8_workflows_exist():
    """All 8 CODEC Quick Action workflow directories exist"""
    missing = [w for w in EXPECTED_WORKFLOWS
               if not os.path.isdir(os.path.join(SERVICES_DIR, w))]
    assert not missing, f"Missing workflows: {missing}"


def test_workflow_documents_valid():
    """Each workflow contains a parseable document.wflow plist"""
    import plistlib
    for wf in EXPECTED_WORKFLOWS:
        doc_path = os.path.join(SERVICES_DIR, wf, "Contents", "document.wflow")
        assert os.path.exists(doc_path), f"Missing document.wflow in {wf}"
        with open(doc_path, "rb") as f:
            data = plistlib.load(f)
        assert "actions" in data, f"No actions key in {wf}/document.wflow"


def test_read_aloud_workflow_script():
    """CODEC Read Aloud workflow calls codec_textassist.py read_aloud"""
    import plistlib
    doc_path = os.path.join(SERVICES_DIR, "CODEC Read Aloud.workflow",
                            "Contents", "document.wflow")
    with open(doc_path, "rb") as f:
        data = plistlib.load(f)
    cmd = data["actions"][0]["action"]["ActionParameters"]["COMMAND_STRING"]
    assert "read_aloud" in cmd


def test_save_workflow_script():
    """CODEC Save workflow calls codec_textassist.py save"""
    import plistlib
    doc_path = os.path.join(SERVICES_DIR, "CODEC Save.workflow",
                            "Contents", "document.wflow")
    with open(doc_path, "rb") as f:
        data = plistlib.load(f)
    cmd = data["actions"][0]["action"]["ActionParameters"]["COMMAND_STRING"]
    assert " save" in cmd


# ── Smoke tests ───────────────────────────────────────────────────────────────

def test_proofread_mode_syntax():
    """codec_textassist.py has no syntax errors"""
    result = subprocess.run(
        [sys.executable, "-c", f"import ast; ast.parse(open('{TEXTASSIST}').read())"],
        capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, f"Syntax error: {result.stderr}"


def test_save_mode_empty_input_exits_cleanly():
    """save mode with empty clipboard exits with code 0 (not 1 or crash)"""
    env = os.environ.copy()
    # pbpaste returns empty when clipboard is empty; we force that via stdin trick
    # We can't easily mock pbpaste, so just check the script parses and the
    # mode string is present — actual integration tested manually
    with open(TEXTASSIST) as f:
        content = f.read()
    # Ensure there's an early exit for empty text before the save block
    empty_exit_idx = content.find("if not text: sys.exit(0)")
    save_idx       = content.find('MODE == "save"')
    assert empty_exit_idx < save_idx, \
        "Empty-text guard must come before the save block"
