#!/usr/bin/env python3
"""CODEC Smoke Test — catches import errors, NameErrors, and path mismatches.

Run after ANY change to codec.py, codec_overlays.py, codec_watcher.py, or codec_dashboard.py:
    python3.13 tests/test_smoke.py

Every test here exists because a real bug shipped without it.
"""
import sys, os, importlib, types

# Ensure repo root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASSED = 0
FAILED = 0

def check(name, ok, detail=""):
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  \033[32m✓\033[0m {name}")
    else:
        FAILED += 1
        print(f"  \033[31m✗\033[0m {name} — {detail}")


# ── 1. IMPORTS ───────────────────────────────────────────────────────────────
print("\n== Import checks ==")

try:
    from codec_overlays import show_overlay, show_recording_overlay, show_processing_overlay, show_toggle_overlay
    check("codec_overlays imports", True)
except ImportError as e:
    check("codec_overlays imports", False, str(e))

try:
    from codec_watcher import TASK_FILE, handle_draft, clean_draft, extract_content
    check("codec_watcher imports", True)
except ImportError as e:
    check("codec_watcher imports", False, str(e))

try:
    from codec_identity import CODEC_IDENTITY, CODEC_VOICE_PROMPT, CODEC_CHAT_PROMPT
    check("codec_identity imports", True)
    check("Identity mentions 7 products", "7 CODEC PRODUCTS" in CODEC_IDENTITY or "7 products" in CODEC_IDENTITY.lower())
    check("Identity mentions memory system", "MEMORY SYSTEM" in CODEC_IDENTITY)
    check("Voice prompt is concise directive", "1-3 sentences" in CODEC_VOICE_PROMPT)
except ImportError as e:
    check("codec_identity imports", False, str(e))


# ── 2. PATH ALIGNMENT ───────────────────────────────────────────────────────
print("\n== Path alignment ==")

# Load DRAFT_TASK_FILE from codec.py without running it (it has signal handlers + pynput)
codec_src = open(os.path.join(os.path.dirname(__file__), "..", "codec.py")).read()

# Extract DRAFT_TASK_FILE value
import re
m = re.search(r'DRAFT_TASK_FILE\s*=\s*(.+)', codec_src)
if m:
    # Evaluate the expression safely
    expr = m.group(1).strip()
    codec_draft_path = eval(expr)  # e.g. os.path.expanduser("~/.codec/draft_task.json")
    watcher_draft_path = TASK_FILE
    check("Draft task file path matches",
          codec_draft_path == watcher_draft_path,
          f"codec.py={codec_draft_path} vs watcher={watcher_draft_path}")
else:
    check("Draft task file path matches", False, "Could not find DRAFT_TASK_FILE in codec.py")


# ── 3. NO UNDEFINED REFERENCES ──────────────────────────────────────────────
print("\n== Undefined reference checks ==")

# Check codec.py doesn't use `log.` without importing logging
has_log_calls = bool(re.search(r'^\s+log\.(info|error|warning|debug)\(', codec_src, re.MULTILINE))
has_log_import = bool(re.search(r'^(import logging|from logging import|log\s*=\s*logging)', codec_src, re.MULTILINE))
check("No bare log.xxx() without import",
      not has_log_calls or has_log_import,
      "Found log.info()/log.error() but no logging import")

# Check banner doesn't reference undefined variables (kt, kv, etc.)
# Find the banner/main function area and check for f-string variables
banner_area = re.findall(r'f["\'].*?\{(\w+)\}.*?["\']', codec_src)
# Known safe variables in codec.py
safe_vars = {'W', 'O', 'stream_label', 'wake_label', 'self', 'e', 'task', 'app',
             'name', 'result', 'audio', 'question', 'ctx', 'summary', 'w', 'h',
             'duration', 'text', 'rid', 'session_id', 'ts', 'mem', 'sys_p',
             'safe_sys', 'CONFIG_PATH', 'Q_TERMINAL_TITLE', 'DB_PATH'}
# Check all top-level constants
constants = set(re.findall(r'^([A-Z_]+)\s*=', codec_src, re.MULTILINE))
safe_vars.update(constants)
# Check for kt, kv which caused the crash loop
check("No {kt} in banner", 'kt' not in banner_area, "Found {kt} — variable was removed")
check("No {kv} in banner", 'kv' not in banner_area, "Found {kv} — variable was removed")

# Check codec.py uses shared identity, not hardcoded prompt
check("codec.py imports CODEC_VOICE_PROMPT",
      "from codec_identity import" in codec_src,
      "codec.py should import from codec_identity.py")
check("codec.py uses CODEC_VOICE_PROMPT in dispatch",
      "CODEC_VOICE_PROMPT" in codec_src,
      "dispatch() should use shared identity prompt")


# ── 4. OVERLAY FUNCTIONS RETURN POPEN ────────────────────────────────────────
print("\n== Overlay function signatures ==")

import inspect
sig = inspect.signature(show_recording_overlay)
check("show_recording_overlay accepts key_label", 'key_label' in sig.parameters, str(sig))
# show_recording_overlay should return a Popen (can't test without display, just verify it's callable)
check("show_recording_overlay is callable", callable(show_recording_overlay))
check("show_processing_overlay is callable", callable(show_processing_overlay))
check("show_toggle_overlay is callable", callable(show_toggle_overlay))


# ── 5. CODEC.PY DISPATCH FLOW ───────────────────────────────────────────────
print("\n== Dispatch flow checks ==")

# Verify is_draft works
exec_ns = {}
exec(compile("from codec_watcher import TASK_FILE\n" +
             re.search(r'(DRAFT_KEYWORDS\s*=\s*\[.*?\])', codec_src, re.DOTALL).group(1) + "\n" +
             re.search(r'(def is_draft\(.*?\):.*?)(?=\ndef )', codec_src, re.DOTALL).group(1),
             '<test>', 'exec'), exec_ns)
is_draft = exec_ns['is_draft']
check("is_draft('draft a message') = True", is_draft("draft a message saying hello"))
check("is_draft('what time is it') = False", not is_draft("what time is it"))
check("is_draft('reply to this email') = True", is_draft("reply to this email"))


# ── 6. WATCHER CLEAN_DRAFT ──────────────────────────────────────────────────
print("\n== Watcher clean_draft ==")

check("Strips preamble", clean_draft("Here is the message:\nHello there") == "Hello there")
check("Strips quotes", clean_draft('"Hello there"') == "Hello there")
check("Passes clean text", clean_draft("Hello there") == "Hello there")


# ── 7. DASHBOARD WATCHER INTEGRATION ────────────────────────────────────────
print("\n== Dashboard integration ==")

dashboard_src = open(os.path.join(os.path.dirname(__file__), "..", "codec_dashboard.py")).read()
check("Dashboard imports TASK_FILE from watcher",
      "from codec_watcher import TASK_FILE" in dashboard_src or
      "from codec_watcher import" in dashboard_src)
check("Dashboard has _bg_watcher function",
      "async def _bg_watcher" in dashboard_src)
check("Dashboard starts watcher on startup",
      "_bg_watcher()" in dashboard_src)


# ── 8. WAKE WORD CASE SENSITIVITY ────────────────────────────────────────────
print("\n== Wake word checks ==")

# The wake word comparison must be case-insensitive
# codec.py lowers the transcribed text, so phrases must also be lowered for matching
wake_match_code = re.search(r'any\(phrase(\.lower\(\))? in text', codec_src)
check("Wake word matching is case-insensitive",
      wake_match_code and '.lower()' in wake_match_code.group(0),
      "Wake phrase comparison must use phrase.lower() since text is already lowered")


# ── RESULTS ──────────────────────────────────────────────────────────────────
print(f"\n{'='*40}")
total = PASSED + FAILED
if FAILED == 0:
    print(f"\033[32m All {total} checks passed.\033[0m\n")
else:
    print(f"\033[31m {FAILED}/{total} checks FAILED.\033[0m\n")
    sys.exit(1)
