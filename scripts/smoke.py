#!/usr/bin/env python3
"""CODEC Smoke Test — catches import errors, NameErrors, and path mismatches.

A script, not a pytest module (it prints and exits). install.sh --update and update.sh run it after a pull and
roll back with `git reset --keep` when it exits 1 (docs/SAFE-UPDATE-DESIGN.md, P-027). Run it by hand after a
change to codec.py, codec_core.py, codec_overlays.py, codec_watcher.py or codec_dashboard.py:
    python3.13 scripts/smoke.py

Every section runs inside a guard: a crash in one is one failed line, and the rest still run.
Exit 0 = every check passed, 1 = at least one failed.
"""
import ast
import importlib
import inspect
import os
import re
import sys

# The checkout this script belongs to wins over anything else on the path.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

PASSED = 0
FAILED = 0
MODS = {}


def check(name, ok, detail=""):
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  \033[32m✓\033[0m {name}")
    else:
        FAILED += 1
        print(f"  \033[31m✗\033[0m {name} — {detail}")


def section(title):
    """Run the decorated function now; an exception becomes one failed check, not a traceback."""
    def run(fn):
        print(f"\n== {title} ==")
        try:
            fn()
        except Exception as e:
            check(f"{title}: finished", False, f"{type(e).__name__}: {e}")
        return fn
    return run


def mod(name):
    m = MODS.get(name)
    if m is None:
        raise RuntimeError(f"{name} did not import (see Import checks)")
    return m


def src(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return f.read()


# ── 1. IMPORTS ───────────────────────────────────────────────────────────────
NEEDED = {
    "codec_overlays": ("show_overlay", "show_recording_overlay", "show_processing_overlay", "show_toggle_overlay"),
    "codec_watcher": ("TASK_FILE", "handle_draft", "clean_draft", "extract_content"),
    "codec_identity": ("CODEC_IDENTITY", "CODEC_VOICE_PROMPT", "CODEC_CHAT_PROMPT"),
    "codec_config": ("DRAFT_TASK_FILE",),
    "codec_core": ("is_draft", "DRAFT_KEYWORDS"),
}


@section("Import checks")
def _imports():
    for name, attrs in NEEDED.items():
        try:
            m = importlib.import_module(name)
        except (Exception, SystemExit) as e:
            check(f"{name} imports", False, f"{type(e).__name__}: {e}")
            continue
        missing = [a for a in attrs if not hasattr(m, a)]
        check(f"{name} imports", not missing, f"missing {', '.join(missing)}")
        if not missing:
            MODS[name] = m


@section("Identity prompts")
def _identity():
    ident = mod("codec_identity")
    identity, voice, chat = ident.CODEC_IDENTITY, ident.CODEC_VOICE_PROMPT, ident.CODEC_CHAT_PROMPT
    # Operating-principles prompt rewrite (April 2026): the principles are present and the skill tag is taught.
    check("Identity opens with 'You are CODEC'", identity.startswith("You are CODEC"))
    check("Identity contains 'Stability first'", "Stability first" in identity)
    check("Identity contains 'Plan before blast radius'", "Plan before blast radius" in identity)
    check("Identity contains 'Sovereign by default'", "Sovereign by default" in identity)
    check("Identity teaches [SKILL:...] tag mechanic", "[SKILL:" in identity)
    check("Identity references memory injection", "[MEMORY]" in identity)
    check("Identity has {date} placeholder", "{date}" in identity)
    check("Voice prompt enforces 1-3 sentence brevity",
          "1–3 plain sentences" in voice or "1-3 plain sentences" in voice)
    check("Voice prompt forbids markdown", "No markdown" in voice or "no markdown" in voice)
    check("Chat prompt allows markdown", "markdown" in chat.lower())


# ── 2. PATH ALIGNMENT ───────────────────────────────────────────────────────
# codec.py is read, never imported: importing it starts the voice loop (signal handlers, pynput).
CODEC_SRC = ""


@section("Path alignment")
def _paths():
    global CODEC_SRC
    CODEC_SRC = src("codec.py")
    draft = mod("codec_config").DRAFT_TASK_FILE
    watcher = mod("codec_watcher").TASK_FILE
    check("Draft task file path matches", draft == watcher, f"codec_config={draft} vs watcher={watcher}")
    check("codec.py takes DRAFT_TASK_FILE from codec_config",
          "DRAFT_TASK_FILE" in CODEC_SRC and not re.search(r"^DRAFT_TASK_FILE\s*=", CODEC_SRC, re.M),
          "codec.py defines its own DRAFT_TASK_FILE")


# ── 3. NO UNDEFINED REFERENCES ──────────────────────────────────────────────
@section("Undefined reference checks")
def _refs():
    code = CODEC_SRC or src("codec.py")
    has_log_calls = bool(re.search(r'^\s+log\.(info|error|warning|debug)\(', code, re.MULTILINE))
    has_log_import = bool(re.search(r'^(import logging|from logging import|log\s*=\s*logging)', code, re.MULTILINE))
    check("No bare log.xxx() without import", not has_log_calls or has_log_import,
          "Found log.info()/log.error() but no logging import")
    # {kt} / {kv} in the banner caused a crash loop once.
    banner_vars = re.findall(r'f["\'].*?\{(\w+)\}.*?["\']', code)
    check("No {kt} in banner", "kt" not in banner_vars, "Found {kt} — variable was removed")
    check("No {kv} in banner", "kv" not in banner_vars, "Found {kv} — variable was removed")
    check("codec.py imports CODEC_VOICE_PROMPT", "from codec_identity import" in code,
          "codec.py should import from codec_identity.py")
    check("codec.py uses CODEC_VOICE_PROMPT in dispatch", "CODEC_VOICE_PROMPT" in code,
          "dispatch() should use shared identity prompt")


# ── 4. OVERLAY FUNCTIONS ────────────────────────────────────────────────────
@section("Overlay function signatures")
def _overlays():
    ov = mod("codec_overlays")
    sig = inspect.signature(ov.show_recording_overlay)
    check("show_recording_overlay accepts key_label", "key_label" in sig.parameters, str(sig))
    check("show_recording_overlay is callable", callable(ov.show_recording_overlay))
    check("show_processing_overlay is callable", callable(ov.show_processing_overlay))
    check("show_toggle_overlay is callable", callable(ov.show_toggle_overlay))


# ── 5. DISPATCH FLOW ────────────────────────────────────────────────────────
@section("Dispatch flow checks")
def _dispatch():
    is_draft = mod("codec_core").is_draft  # codec.py imports it from codec_core
    check("codec.py imports is_draft from codec_core",
          bool(re.search(r"from codec_core import \([^)]*\bis_draft\b", CODEC_SRC or src("codec.py"))),
          "codec.py no longer takes is_draft from codec_core")
    check("is_draft('draft a message') = True", is_draft("draft a message saying hello"))
    check("is_draft('what time is it') = False", not is_draft("what time is it"))
    check("is_draft('reply to this email') = True", is_draft("reply to this email"))


# ── 6. WATCHER CLEAN_DRAFT ──────────────────────────────────────────────────
@section("Watcher clean_draft")
def _clean():
    clean_draft = mod("codec_watcher").clean_draft
    check("Strips preamble", clean_draft("Here is the message:\nHello there") == "Hello there")
    check("Strips quotes", clean_draft('"Hello there"') == "Hello there")
    check("Passes clean text", clean_draft("Hello there") == "Hello there")


# ── 7. DASHBOARD WATCHER INTEGRATION ────────────────────────────────────────
@section("Dashboard integration")
def _dashboard():
    dash = src("codec_dashboard.py")
    check("Dashboard imports from codec_watcher", "from codec_watcher import" in dash)
    check("Dashboard has _bg_watcher function", "async def _bg_watcher" in dash)
    check("Dashboard starts watcher on startup", "_bg_watcher()" in dash)


# ── 8. WAKE WORD CASE SENSITIVITY ───────────────────────────────────────────
@section("Wake word checks")
def _wake():
    tree = ast.parse(CODEC_SRC or src("codec.py"))
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_is_wake_utterance"), None)
    defaults = next((n.value for n in tree.body if isinstance(n, ast.Assign)
                     and any(getattr(t, "id", "") == "_WAKE_KEYWORD_DEFAULTS" for t in n.targets)), None)
    check("codec.py defines _is_wake_utterance", fn is not None, "function not found")
    check("codec.py defines _WAKE_KEYWORD_DEFAULTS", defaults is not None, "constant not found")
    if fn is None or defaults is None:
        return
    ns = {"WAKE_PHRASES": ["Okay Smoke Helper"], "_WAKE_KEYWORD_DEFAULTS": ast.literal_eval(defaults)}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "codec.py:_is_wake_utterance", "exec"), ns)
    wake = ns["_is_wake_utterance"]
    check("Wake phrase matching is case-insensitive", wake("okay smoke helper, open mail"),
          "a configured phrase with capitals did not match the lowered text")
    check("Wake keyword matches in any case", wake("HEY CODEC"))
    check("Ordinary speech does not wake", not wake("what time is it"))


# ── RESULTS ──────────────────────────────────────────────────────────────────
print(f"\n{'=' * 40}")
total = PASSED + FAILED
if FAILED == 0:
    print(f"\033[32m All {total} checks passed.\033[0m\n")
else:
    print(f"\033[31m {FAILED}/{total} checks FAILED.\033[0m\n")
    sys.exit(1)
