#!/usr/bin/env python3
# C-1 (PR-4A): real SIGTERM/SIGINT handlers are registered in main() via
# _graceful_shutdown (after `state` is defined). The old no-op handlers that
# lived here ignored shutdown signals, orphaning the sox recording subprocess +
# tkinter overlays and leaking temp .wav/.png files on every PM2 restart.
import signal
"""CODEC v2.1 | F13=on/off | F18=voice | F16=text | *=screenshot | +=doc | Wake word"""
import logging
import threading
import tempfile
import subprocess
import os
import sys
import time
import json
import re
import base64
import shutil
import atexit
from datetime import datetime
from pynput import keyboard

log = logging.getLogger(__name__)

# Audit emits route through the unified log_event adapter (real, not no-op)
# per docs/PHASE1-STEP1-DESIGN.md.
from codec_audit import log_event

# Ensure homebrew tools are on PATH (PM2 may not inherit full shell PATH)
_BREW = "/opt/homebrew/bin"
if _BREW not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _BREW + ":" + os.environ.get("PATH", "")
SOX = shutil.which("sox") or "sox"

# ── CONFIG (single source of truth: codec_config.py) ─────────────────────────
from codec_config import (
    cfg as _cfg,
    QWEN_BASE_URL, QWEN_MODEL, LLM_API_KEY, LLM_KWARGS,
    WHISPER_URL,
    TASK_QUEUE_FILE, DRAFT_TASK_FILE, SESSION_ALIVE, STREAMING, WAKE_WORD, WAKE_ENERGY, WAKE_CHUNK_SEC,
    WAKE_PHRASES,
    get_gemini_api_key,
)


# ── Wake-word matching (A-16, PR-3C) ─────────────────────────────────────────
# Curated homophone variants Whisper produces for "codec" (deduped — the old
# inline list had "kodak" twice). Matched as case-insensitive substrings of the
# ASR text.
_WAKE_KEYWORD_DEFAULTS = ("codec", "codex", "kodak", "kodec", "co-dec", "caudec", "codag")
# Generic tokens that must NOT become wake triggers on their own (avoid false
# wakes from a bare "hey"/"okay" in normal speech).
_WAKE_STRIP_PREFIXES = ("hey", "and", "hay", "eh", "ay", "okay", "ok")


def _is_wake_utterance(text: str) -> bool:
    """True if `text` (lowercased ASR output) is a wake utterance.

    A-16 fix: honors user-customized `WAKE_PHRASES` from config — previously
    codec.py hardcoded the keyword list and ignored `wake_phrases` entirely, so
    the documented config knob had no effect. Two matchers:
      1. Homophone keyword substring (codec/codex/kodak/...) — the legacy behavior.
      2. Configured wake PHRASE substring, but only phrases ≥5 chars so generic
         short entries ("hey") can't false-trigger on ordinary speech.
    """
    t = (text or "").lower()
    if any(kw in t for kw in _WAKE_KEYWORD_DEFAULTS):
        return True
    for phrase in WAKE_PHRASES:
        p = (phrase or "").lower().strip()
        if len(p) >= 5 and p in t:
            return True
    return False

# Vision — prefer Gemini Flash (fast cloud), fall back to local Qwen VL
# These are codec.py-specific (not in codec_config)
# PR-2B-2 (D-15): key now sourced via Keychain-aware getter (cfg→Keychain
# migration on first call), with GEMINI_API_KEY env fallback inside the getter.
GEMINI_API_KEY    = get_gemini_api_key()
VISION_PROVIDER   = _cfg.get("vision_provider", "gemini" if GEMINI_API_KEY else "local")
# A-17 (PR-3C): the dead `DRAFT_KEYWORDS_CFG` knob was removed here — user
# `draft_keywords` overrides are now honored inside codec_core.is_draft().

# ─��� SHARED (from codec_core.py — single source of truth) ─────────────────────
import codec_core as _core
from codec_core import (
    is_draft, init_db, save_task, update_session_response, get_memory, get_recent_conversations,
    transcribe, speak_text, focused_app, get_text_dialog,
    terminal_session_exists,
    # A-14 (PR-3G): `close_session` import dropped — codec.py defines its own
    # local close_session() (below) that shadowed this import, making it dead.
    # A-4 (PR-3): `loaded_skills`/`load_skills`/`run_skill` dropped — the voice/
    # wake path now uses the canonical codec_dispatch registry (below), which
    # applies the PR-1A AST safety gate + plugin hooks the legacy path skipped.
)
from codec_agent import run_session_in_terminal
# A-4: canonical skill dispatch (lazy SkillRegistry + safety gate + run_with_hooks).
from codec_dispatch import check_skill, run_skill, load_skills

from codec_overlays import show_overlay, show_recording_overlay, show_processing_overlay, show_toggle_overlay
from codec_identity import CODEC_VOICE_PROMPT

# ── SKILLS ───────────────────────────────────────────────────────────────────
# A-4 (PR-3): codec.py's local `check_skills_ranked` / `check_skill` (which
# iterated the legacy `codec_core.loaded_skills`) were removed. Skill matching +
# execution now go through the canonical `codec_dispatch.check_skill` /
# `run_skill` (imported above) — the same path chat / MCP / session / telegram /
# imessage already use. That routes voice/wake skill calls through the PR-1A AST
# safety gate AND plugin lifecycle hooks (run_with_hooks), both of which the
# legacy path bypassed.

# ── VISION (A-11, PR-3E: canonical helper in codec_vision) ──────────────────
# The Gemini-Flash → local-Qwen-VL fallback used to be hand-rolled here (and in
# codec_voice + codec_session). It now lives in codec_vision; this is a thin
# delegate kept for any caller of codec.vision_describe.
import codec_vision
import codec_llm  # A-12: canonical chat/completions caller

def vision_describe(img_b64, prompt="Read all visible text on this screen. Include app name, window title, and all message/content text. Output raw text only.", max_tokens=800):
    """Route vision to Gemini or local based on config (codec_vision)."""
    return codec_vision.describe_sync(img_b64, prompt, mime="image/png", max_tokens=max_tokens)

def screenshot_ctx():
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.close()
        subprocess.run(["screencapture", "-x", tmp.name], timeout=5)
        if not os.path.exists(tmp.name) or os.path.getsize(tmp.name) < 1000:
            return ""
        with open(tmp.name, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        os.unlink(tmp.name)
        provider = "Gemini Flash" if (VISION_PROVIDER == "gemini" and GEMINI_API_KEY) else "Qwen VL"
        print(f"[CODEC] Reading screen via {provider}...")
        content = vision_describe(img_b64)
        if content:
            print(f"[CODEC] Screen context: {len(content)} chars")
            return content[:2000]
    except Exception as e:
        print(f"[CODEC] Vision error: {e}")
    return ""

# ── STATE ─────────────────────────────────────────────────────────────────────
state = {
    "active": False,
    "recording": False,
    "rec_proc": None,
    "audio_path": None,
    "last_f13": 0.0,
    "last_star": 0.0,
    "screen_ctx": "",
    "screen_ctx_ts": 0.0,   # when screen_ctx was captured; used for TTL expiry
    "last_plus": 0.0,
    "last_minus": 0.0,
    "doc_ctx": "",
}

# H-2 (PR-4F): `state` is mutated by the keyboard listener, wake-word, and worker
# threads. Single-field reads/writes are GIL-atomic, but COMPOUND check-then-set
# (e.g. `if not state["recording"]: state["recording"]=True`) is not — two
# threads could both pass the guard and start two sox recordings. These helpers
# do ONLY the atomic decision under _state_lock; all expensive work (sox,
# overlays, sounds, dispatch) stays OUTSIDE the lock at the call sites.
_state_lock = threading.Lock()


def _try_begin_recording() -> bool:
    """Atomically claim the recording slot. Returns True if THIS caller acquired
    it (and set recording=True + rec_start), False if a recording was already in
    progress (the caller must NOT start a second sox)."""
    with _state_lock:
        if state["recording"]:
            return False
        state["recording"] = True
        state["rec_start"] = time.time()
        return True


def _activate_if_off() -> bool:
    """Atomically set active=True if it was off. Returns True if this caller
    changed it (so it alone shows the 'on' overlay)."""
    with _state_lock:
        if state["active"]:
            return False
        state["active"] = True
        return True


def _toggle_active() -> bool:
    """Atomically flip state['active']; return the NEW value (True == now on)."""
    with _state_lock:
        state["active"] = not state["active"]
        return state["active"]

# ── SCREEN-CONTEXT RELEVANCE GATE ─────────────────────────────────────────────
# Tasks that clearly have nothing to do with the screen — skip context injection
# to prevent the LLM from being confused by stale/irrelevant captured text.
_TRIVIAL_SCREEN_BYPASS = re.compile(
    r"^\s*(?:"
    r"\d+\s*[+\-*/x×÷]\s*\d+"               # arithmetic: "1+1", "5 * 3"
    r"|what\s*time"                         # "what time is it"
    r"|time\s*(?:is\s*it|now)?"             # "time now"
    r"|what'?s?\s+the\s+date"               # "what's the date"
    r"|bitcoin\s*(?:price)?"                # "bitcoin price"
    r"|btc\s*price"
    r"|weather"                             # weather queries
    r"|calculate\s+"                        # "calculate 5 * 4"
    r"|speed\s*test"                        # the user's actual failing query
    r"|ping"
    r"|hello|hi|hey"                        # greetings
    r"|status|health|uptime"                # system checks
    r")\b",
    re.IGNORECASE,
)
_SCREEN_CTX_TTL = 120.0  # seconds — stale screen context expires

def _maybe_screen_context(task: str) -> str:
    """Return ' [SCREEN CONTEXT: ...]' to append, or '' if skipped.

    Clears expired/used screen_ctx as a side-effect. Keeps existing behavior
    when the task genuinely looks screen-related; skips for trivial lookups or
    when the captured screenshot is older than TTL.
    """
    ctx = state.get("screen_ctx", "")
    if not ctx:
        return ""
    # TTL: stale screenshots shouldn't follow the user around
    ts = state.get("screen_ctx_ts", 0.0)
    if ts and (time.time() - ts) > _SCREEN_CTX_TTL:
        print(f"[CODEC] Screen context expired ({int(time.time()-ts)}s old) — discarding")
        state["screen_ctx"] = ""
        state["screen_ctx_ts"] = 0.0
        return ""
    # Relevance: trivial intents ignore screen context
    if _TRIVIAL_SCREEN_BYPASS.match(task or ""):
        print("[CODEC] Trivial task — skipping screen context injection")
        return ""
    # Use it, one-shot
    out = " [SCREEN CONTEXT: " + ctx[:800] + "]"
    state["screen_ctx"] = ""
    state["screen_ctx_ts"] = 0.0
    return out

# ── DISPATCH LOCK — only one dispatch at a time, prevents feedback loops ──
_dispatch_lock = threading.Lock()
_dispatch_cooldown = 0.0  # timestamp: ignore wake words until this time
_last_tts_text = ""  # last TTS output — used to strip echo from mic recordings

# ── VOICE CONVERSATION SESSION (persistent across F18 presses) ──────────────
voice_session = {
    "messages": [],      # [{role, content}, ...] — full conversation history
    "started": None,     # ISO timestamp of session start
    "turn_count": 0,     # number of exchanges
}

def reset_voice_session():
    """Clear voice conversation history (called on F13 toggle)."""
    voice_session["messages"] = []
    voice_session["started"] = None
    voice_session["turn_count"] = 0
    print("[CODEC] Voice session reset")

# ── WORK QUEUE ────────────────────────────────────────────────────────────────
work_queue = []
work_lock = threading.Lock()

def push(fn, *args):
    with work_lock:
        work_queue.append((fn, args))

def worker():
    while True:
        item = None
        with work_lock:
            if work_queue:
                item = work_queue.pop(0)
        if item:
            fn, args = item
            try: fn(*args)
            except Exception as e:
                print(f"[CODEC] Error: {e}")
                import traceback; traceback.print_exc()
        else:
            time.sleep(0.05)

# ── SESSION CLEANUP ───────────────────────────────────────────────────────────
def close_session():
    if os.path.exists(SESSION_ALIVE):
        try:
            with open(SESSION_ALIVE) as f: pid = int(f.read().strip())
            os.kill(pid, 15)
            print(f"[CODEC] Session process {pid} terminated")
        except Exception: pass
        try: os.unlink(SESSION_ALIVE)
        except Exception: pass
    try: os.unlink(TASK_QUEUE_FILE)
    except Exception: pass
    subprocess.Popen(["osascript", "-e",
        'tell application "Terminal" to close (every window whose name contains "python3.13 /var/folders")'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ── DISPATCH ──────────────────────────────────────────────────────────────────
def dispatch(task):
    global _dispatch_cooldown
    if not _dispatch_lock.acquire(blocking=False):
        print(f"[CODEC] Dispatch BLOCKED (already processing): {task[:60]}")
        return
    try:
        _dispatch_inner(task)
    finally:
        # Post-dispatch cooldown — TTS is now blocking so audio is already done
        # 1.5s buffer for echo/reverb decay (was 5s — too long, made CODEC feel unresponsive)
        _dispatch_cooldown = time.time() + 1.5
        _dispatch_lock.release()

def _build_voice_system_prompt(task):
    """A-5 (PR-3D-b): assemble the voice system prompt — CODEC_VOICE_PROMPT +
    boot identity + active temporal facts + recent memory + targeted/recent
    conversation context. Reads the memory stores (each guarded); returns the
    assembled system-prompt string. Extracted verbatim from _dispatch_inner."""
    mem = get_memory(5)
    mem_ctx = ""
    boot_ctx = ""
    facts_ctx = ""
    try:
        from codec_memory import CodecMemory
        cm = CodecMemory()
        targeted = cm.get_context(task, n=5)
        if targeted:
            mem_ctx += f"\n\n[MEMORY — RELEVANT PAST CONVERSATIONS]\n{targeted}\n[END MEMORY]"
        recent = cm.search_recent(days=3, limit=5)
        if recent:
            lines = ["[RECENT MEMORY — LAST 3 DAYS]"]
            for r in recent:
                ts = r["timestamp"][:16].replace("T", " ")
                snippet = r["content"][:200].replace("\n", " ")
                lines.append(f"  [{ts}] {r['role'].upper()}: {snippet}")
            lines.append("[END RECENT MEMORY]")
            mem_ctx += "\n\n" + "\n".join(lines)
    except Exception as e:
        log.warning("Memory context retrieval failed: %s", e)

    # ── Memory upgrade: L0/L1 identity + active temporal facts ──────────
    try:
        from codec_memory_upgrade import load_identity, query_valid_facts, compress_rule_based
        identity = load_identity()
        if identity:
            boot_ctx = f"\n\n[IDENTITY — BOOT PAYLOAD]\n{identity}\n[END IDENTITY]"
        facts = query_valid_facts(limit=20)
        if facts:
            lines = ["[ACTIVE FACTS]"]
            for f in facts:
                lines.append(f"  {f['key']} = {f['value']}")
            lines.append("[END FACTS]")
            facts_ctx = "\n\n" + "\n".join(lines)
        # Compress the recalled memory block to save tokens (identity+facts stay verbatim)
        if mem_ctx:
            mem_ctx = compress_rule_based(mem_ctx)
    except Exception as e:
        log.warning("Memory upgrade injection failed: %s", e)

    # 2026-04-29 prompt rewrite: CODEC_VOICE_PROMPT now contains a {date}
    # placeholder. Format it before use so the LLM doesn't see literal '{date}'.
    sys_p = CODEC_VOICE_PROMPT.format(date=datetime.now().strftime("%A, %B %d, %Y"))
    if boot_ctx: sys_p += boot_ctx
    if facts_ctx: sys_p += facts_ctx
    if mem: sys_p += "\n\n" + mem
    if mem_ctx: sys_p += mem_ctx
    return sys_p


def _persist_voice_turn(task, answer, rid):
    """A-5 (PR-3D-b): persist a completed voice turn — append the assistant
    message to the in-memory session, bump turn_count, write the response to the
    session DB (WAL helper), and save the exchange to shared CodecMemory.
    Extracted verbatim from _dispatch_inner's quick-reply block."""
    voice_session["messages"].append({"role": "assistant", "content": answer})
    voice_session["turn_count"] += 1
    # Save response to DB (A-20: codec_core helper, WAL + busy_timeout).
    update_session_response(rid, answer[:500])
    # Save to shared memory (same store as Chat)
    try:
        from codec_memory import CodecMemory
        cm = CodecMemory()
        cm.save("voice", "user", task)
        cm.save("voice", "assistant", answer)
    except Exception as e:
        log.warning(f"[CODEC] Memory save failed after LLM: {e}")


def _dispatch_inner(task):
    app = focused_app()
    log_event("wake_dispatch", "open-codec",
              f"Voice dispatch: {task[:80]}",
              extra={"task_preview": task[:200]})
    print(f"[CODEC] Task: {task[:80]} | App: {app}")
    _safe_task = task[:50].replace('\\', '\\\\').replace('"', '\\"')
    subprocess.Popen(["osascript", "-e", f'display notification "Heard: {_safe_task}" with title "CODEC"'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Check skills — canonical dispatch (A-4). check_skill returns the best
    # match with all fall-through candidates; run_skill tries them in order
    # (wrapped in run_with_hooks) and returns the first non-None result.
    skill_result = None
    if len(task) < 500:
        skill = check_skill(task)
        if skill:
            result = run_skill(skill, task, app)
            if result is not None:
                push(lambda: show_overlay('Skill: ' + skill['name'], '#E8711A', 2000))
                global _last_tts_text
                _last_tts_text = str(result)[:200]
                speak_text(result)
                subprocess.Popen(["osascript", "-e", f'display notification "{str(result)[:80]}" with title "CODEC Skill"'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print(f"[CODEC] Skill response: {str(result)[:100]}")
                skill_result = str(result)
                # Add skill exchange to voice session for continuity
                voice_session["messages"].append({"role": "user", "content": task})
                voice_session["messages"].append({"role": "assistant", "content": f"[Skill: {skill['name']}] {skill_result}"})
                voice_session["turn_count"] += 1
                # Save to shared memory
                try:
                    from codec_memory import CodecMemory
                    cm = CodecMemory()
                    cm.save("voice", "user", task)
                    cm.save("voice", "assistant", skill_result[:500])
                except Exception as e:
                    log.warning(f"[CODEC] Memory save failed after skill: {e}")
                # After skill fires, grab screen context in background (don't block queue)
                def _post_skill_screenshot():
                    try:
                        time.sleep(2)
                        screen = screenshot_ctx()
                        if screen and len(screen) > 50:
                            voice_session["messages"].append({
                                "role": "user",
                                "content": f"[CONTEXT: My screen now shows: {screen[:1000]}]"
                            })
                            print(f"[CODEC] Post-skill screen captured: {len(screen)} chars")
                    except Exception as e:
                        print(f"[CODEC] Post-skill screenshot failed: {e}")
                threading.Thread(target=_post_skill_screenshot, daemon=True).start()
                return

    if is_draft(task):
        push(lambda: show_overlay('Reading screen...', '#E8711A', 2000))
        ctx = screenshot_ctx()
        push(lambda: show_processing_overlay('Drafting your message...', 15000))
        with open(DRAFT_TASK_FILE, "w") as f:
            json.dump({"task": task, "ctx": ctx, "app": app}, f)
        print("[CODEC] Draft queued for watcher")
        return

    rid = save_task(task, app)

    # ── Build system prompt with memory (A-5: extracted helper) ─────────
    sys_p = _build_voice_system_prompt(task)
    safe_sys = sys_p.replace('\n', ' ')

    # ── Open terminal session (the real CODEC session window) ───────────
    with open(TASK_QUEUE_FILE, "w") as f:
        json.dump({"task": task, "app": app, "ts": datetime.now().isoformat()}, f)

    if terminal_session_exists():
        # Warm session: codec_session.py handles the LLM + TTS.
        # Skip the inline quick-reply to avoid double-TTS (bug fix 2026-04-16).
        print("[CODEC] Queued to existing session (inline quick-reply skipped)")
        return
    else:
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_session_in_terminal(safe_sys, session_id, task)

    # ── Quick voice reply (immediate feedback while terminal loads) ─────
    # Only runs on COLD START — a warm terminal session handles TTS itself.
    if not voice_session["started"]:
        voice_session["started"] = datetime.now().isoformat()

    # Add current user message to session
    voice_session["messages"].append({"role": "user", "content": task})

    # Build LLM messages: system + conversation history (keep last 20 turns)
    llm_messages = [{"role": "system", "content": sys_p}]
    # Trim to last 10 messages to keep prompt fast on 35B model
    # Filter out system messages from history — Qwen requires system only at start
    history = [m for m in voice_session["messages"][-10:] if m["role"] != "system"]
    llm_messages.extend(history)

    push(lambda: show_processing_overlay('Thinking...', 15000))
    try:
        # A-12 (PR-3E): canonical codec_llm.call replaces the inline
        # chat/completions POST + headers + enable_thinking + <think> strip +
        # choices parse. Returns the stripped answer, or "" on any failure
        # (non-200 and empty now collapse to the same apology).
        answer = codec_llm.call(
            llm_messages, base_url=QWEN_BASE_URL, model=QWEN_MODEL,
            api_key=LLM_API_KEY, max_tokens=400, temperature=0.7,
            timeout=120, retries=1, extra_kwargs=LLM_KWARGS,
        )
        if answer:
            print(f"[CODEC] Voice reply (turn {voice_session['turn_count']+1}): {answer[:120]}")
            log_event("tts_speak", "open-codec",
                      f"TTS: {answer[:60]}",
                      extra={"text_len": len(answer)})
            # Persist the turn (A-5: extracted to _persist_voice_turn)
            _persist_voice_turn(task, answer, rid)
            _last_tts_text = answer[:200]
            speak_text(answer)
            _safe_ans = answer[:80].replace('\\', '\\\\').replace('"', '\\"')
            subprocess.Popen(["osascript", "-e",
                f'display notification "{_safe_ans}" with title "CODEC"'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            print("[CODEC] Voice LLM returned no response")
            speak_text("Sorry, I didn't get a response.")
    except Exception as e:
        log.error("Voice LLM call failed: %s", e)
        import traceback; traceback.print_exc()
        speak_text("Sorry, something went wrong.")

# ── DOCUMENT INPUT ────────────────────────────────────────────────────────────
def do_document_input():
    push(lambda: show_overlay('Select document...', '#E8711A', 3000))
    try:
        r = subprocess.run(["osascript", "-e",
            'set f to POSIX path of (choose file with prompt "Select a document for CODEC:" of type {"public.item"})'],
            capture_output=True, text=True, timeout=60)
        filepath = r.stdout.strip()
        if not filepath:
            print("[CODEC] No file selected"); return
        print(f"[CODEC] Document: {filepath}")
        push(lambda: show_overlay('Reading document...', '#E8711A', 3000))
        ext = os.path.splitext(filepath)[1].lower()
        fname = os.path.basename(filepath)
        content_text = ""

        if ext in ['.txt','.md','.csv','.json','.py','.js','.html','.css','.log']:
            with open(filepath, 'r', errors='ignore') as f:
                content_text = f.read()[:5000]
        elif ext in ['.png','.jpg','.jpeg','.gif','.webp']:
            try:
                with open(filepath, "rb") as imgf:
                    img_b64 = base64.b64encode(imgf.read()).decode()
                content_text = vision_describe(img_b64, "Describe this image in detail. Read any text visible.", 1000)[:5000]
            except Exception as e:
                print(f"[CODEC] Image vision error: {e}")
        elif ext == '.pdf':
            try:
                import fitz
                doc = fitz.open(filepath)
                content_text = "\n".join(p.get_text() for p in doc[:5])[:5000]
                doc.close()
                print(f"[CODEC] PDF extracted: {len(content_text)} chars from {len(doc)} pages")
            except Exception as e:
                print(f"[CODEC] PDF extraction error: {e}")

        if content_text:
            # Dispatch directly to terminal for analysis
            task = "Analyze and summarize this document (" + fname + "): " + content_text[:3000]
            print(f"[CODEC] Document dispatched ({len(content_text)} chars)")
            dispatch(task)
        else:
            push(lambda: show_overlay('Could not read document', '#ff3333', 2000))
    except Exception as e:
        print(f"[CODEC] Document error: {e}")

# ── SCREENSHOT SHORTCUT ──────────────────────────────────────────────────────
def do_screenshot_question():
    push(lambda: show_overlay('Analyzing screen...', '#E8711A', 3000))
    ctx = screenshot_ctx()
    if not ctx:
        push(lambda: show_overlay('Screenshot failed', '#ff3333', 2000))
        return
    print(f"[CODEC] Screenshot captured ({len(ctx)} chars)")
    # PR-2F (closes D-21): pass the OCR summary as an osascript ARGV argument
    # rather than interpolating it into the script source. AppleScript reads
    # `summary` from `item 1 of argv` — NO string interpolation means an
    # adversarial OCR result (`"\n display dialog "PWNED"`) is treated as
    # literal text by AppleScript and cannot break out of the string context.
    summary = ctx[:120]
    body = f"I captured your screen:\n\n{summary}…\n\nWhat would you like to know about it?"
    script = (
        'on run argv\n'
        '  set bodyText to item 1 of argv\n'
        '  tell application "System Events"\n'
        '    set frontmost of first process whose frontmost is true to true\n'
        '  end tell\n'
        '  set t to text returned of (display dialog bodyText '
        'default answer "" with title "CODEC Screenshot" '
        'buttons {"Cancel","Ask"} default button "Ask")\n'
        '  return t\n'
        'end run'
    )
    try:
        r = subprocess.run(
            ["osascript", "-e", script, body],
            capture_output=True, text=True, timeout=120,
        )
        question = r.stdout.strip()
        if question:
            task = question + " [SCREEN CONTEXT: " + ctx[:800] + "]"
            dispatch(task)
        else:
            # User cancelled — save for later F18/F16 AND inject into voice session
            state["screen_ctx"] = ctx
            voice_session["messages"].append({
                "role": "system",
                "content": f"[SCREEN CAPTURE: The user's screen currently shows: {ctx[:1000]}]"
            })
            state["screen_ctx_ts"] = time.time()
            push(lambda: show_overlay('Screenshot saved — use voice or text to ask', '#E8711A', 3000))
    except Exception as e:
        print(f"[CODEC] Screenshot dialog error: {e}")
        state["screen_ctx"] = ctx
        state["screen_ctx_ts"] = time.time()

# ── TEXT/VOICE HANDLERS ───────────────────────────────────────────────────────
def do_text():
    task = get_text_dialog()
    if task:
        task = task + _maybe_screen_context(task)
        dispatch(task)

def do_start_recording():
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    state["audio_path"] = tmp.name; tmp.close()
    rec = subprocess.Popen(
        [SOX, "-t", "coreaudio", "default", "-r", "16000", "-c", "1", "-b", "16", "-e", "signed-integer", state["audio_path"]],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    state["rec_proc"] = rec
    print("[CODEC] Recording...")

def do_stop_voice():
    audio = state.get("audio_path")
    rec = state.get("rec_proc")
    rec_start = state.get("rec_start", 0)
    if rec:
        try: rec.terminate(); rec.wait(timeout=3)
        except Exception as e: log.debug("Recording process cleanup failed: %s", e)
    state["rec_proc"] = None; state["recording"] = False
    ovl = state.get("overlay_proc")
    if ovl:
        try: ovl.terminate()
        except Exception as e: log.debug("Overlay process cleanup failed: %s", e)
        state["overlay_proc"] = None
    if not audio or not os.path.exists(audio): return
    # Reject recordings shorter than 0.5s — just button taps, not speech
    rec_duration = time.time() - rec_start if rec_start else 0
    if rec_duration < 0.5:
        print(f"[CODEC] Recording too short ({rec_duration:.1f}s) — ignored")
        try: os.unlink(audio)
        except Exception: pass
        return
    if os.path.getsize(audio) < 1000:
        try: os.unlink(audio)
        except Exception as e: log.debug("Audio file cleanup failed: %s", e)
        return
    print("[CODEC] Transcribing...")
    push(lambda: show_processing_overlay('Transcribing...', 2000))
    task = transcribe(audio)
    if not task: print("[CODEC] No speech detected"); return
    # Strip TTS echo — mic sometimes captures CODEC's own voice response
    if _last_tts_text and len(_last_tts_text) > 10:
        # Build fuzzy fragments from last TTS to match in transcription
        tts_lower = _last_tts_text.lower()
        task_lower = task.lower()
        # Find the longest overlap between end of task and TTS text
        for frag_len in range(min(len(tts_lower), len(task_lower)), 10, -1):
            frag = tts_lower[:frag_len]
            idx = task_lower.find(frag)
            if idx >= 0:
                # Everything from this match onward is likely TTS echo
                cleaned = task[:idx].strip()
                if len(cleaned) > 5:
                    print(f"[CODEC] Stripped TTS echo: '{task[idx:idx+60]}...'")
                    task = cleaned
                break
    print(f"[CODEC] Heard: {task}")
    task = task + _maybe_screen_context(task)
    dispatch(task)

# ── WAKE WORD LISTENER ───────────────────────────────────────────────────────
def wake_word_listener():
    import requests as req_wake
    sample_rate = 16000
    chunk_sec = WAKE_CHUNK_SEC
    # Find the Anker webcam mic — always use it for wake word regardless of BT devices
    wake_device = "default"
    try:
        import sounddevice as sd
        for i, d in enumerate(sd.query_devices()):
            if d['max_input_channels'] > 0 and 'anker' in d['name'].lower():
                wake_device = d['name']
                break
    except Exception as e: log.debug("Wake mic device detection failed: %s", e)
    print(f"[CODEC] Wake word listener started (mic: {wake_device}, threshold={WAKE_ENERGY}). Say 'Hey CODEC' to activate.")
    if WAKE_ENERGY > 1000:
        print(f"[CODEC] ⚠️  Wake energy threshold ({WAKE_ENERGY}) is very high — wake word may not trigger. Default is 200.")
    _wake_diag_done = False
    _wake_low_count = 0  # track consecutive below-threshold to warn user
    while True:
        if not WAKE_WORD or state["recording"]:
            time.sleep(0.3); continue
        # Skip while TTS is actively playing (prevents mic hearing our own voice)
        if _core.tts_playing:
            time.sleep(0.3); continue
        # Skip for 8s after TTS finishes (audio echo / reverb decay)
        if _core.tts_finished_at and (time.time() - _core.tts_finished_at) < 8.0:
            time.sleep(0.3); continue
        # Skip wake processing during dispatch cooldown (prevents hearing our own TTS)
        if time.time() < _dispatch_cooldown:
            time.sleep(0.3); continue
        # Skip if a dispatch is already in progress
        if _dispatch_lock.locked():
            time.sleep(0.3); continue
        try:
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False); tmp.close()
            subprocess.run(
                [SOX, "-t", "coreaudio", wake_device, "-r", str(sample_rate), "-c", "1",
                 "-b", "16", "-e", "signed-integer", tmp.name, "trim", "0", str(chunk_sec)],
                timeout=int(chunk_sec) + 3, capture_output=True)
            if not os.path.exists(tmp.name) or os.path.getsize(tmp.name) < 500:
                try: os.unlink(tmp.name)
                except Exception as e: log.debug("Wake temp file cleanup failed: %s", e)
                continue
            # Check actual audio energy
            try:
                import wave
                import numpy as np
                wf = wave.open(tmp.name, 'rb')
                data = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
                wf.close()
                energy = np.abs(data).mean()
            except Exception as e:
                log.debug("Wake audio energy check failed: %s", e)
                energy = 0
            if not _wake_diag_done or energy > 80:
                print(f"[CODEC] Wake mic: energy={energy:.0f} (threshold={WAKE_ENERGY})")
                _wake_diag_done = True
            if energy < WAKE_ENERGY:
                if energy > WAKE_ENERGY * 0.3:
                    _wake_low_count += 1
                    if _wake_low_count == 20:
                        print(f"[CODEC] ⚠️  Mic picks up speech (energy ~{energy:.0f}) but threshold is {WAKE_ENERGY}. Lower wake_energy in ~/.codec/config.json if wake word doesn't trigger.")
                try: os.unlink(tmp.name)
                except Exception as e: log.debug("Wake temp file cleanup failed: %s", e)
                continue
            try:
                with open(tmp.name, "rb") as f:
                    r = req_wake.post(WHISPER_URL,
                        files={"file": ("wake.wav", f, "audio/wav")},
                        data={"model": "mlx-community/whisper-large-v3-turbo", "language": "en"},
                        timeout=10)
                if r.status_code == 200:
                    text = r.json().get("text", "").lower().strip()
                    if not text or len(text) < 2:
                        continue
                    # Filter Whisper hallucinations (repetitive gibberish)
                    if len(text) > 120:
                        continue
                    print(f"[CODEC] Wake heard: '{text}'")
                    # Wake match — homophone keyword OR a configured wake phrase
                    # (A-16: honors user WAKE_PHRASES; see _is_wake_utterance).
                    _matched = _is_wake_utterance(text)
                    if _matched:
                        log_event("wake_word_detected", "open-codec",
                                  "Wake word detected")
                        # Auto-activate if not already on (H-2: atomic vs the F13 toggle)
                        if _activate_if_off():
                            push(lambda: show_toggle_overlay(True, "F18=voice | **=screen | --=chat"))
                        command = text
                        # Strip wake keywords and common prefixes (case-insensitive)
                        for kw in list(_WAKE_KEYWORD_DEFAULTS) + list(_WAKE_STRIP_PREFIXES):
                            command = re.sub(r'(?i)\b' + re.escape(kw) + r'\b', '', command).strip()
                        command = re.sub(r'^[\s,.\-]+|[\s,.\-]+$', '', command)
                        if len(command) > 3:
                            print(f"[CODEC] Wake + command: {command}")
                            push(lambda: show_overlay('Heard you!', '#E8711A', 1500))
                            push(lambda cmd=command: dispatch(cmd))
                        else:
                            print("[CODEC] Wake word detected! Listening...")
                            push(lambda: show_overlay('Listening...', '#E8711A', 5000))
                            # Record follow-up command (8 seconds)
                            tmp2 = tempfile.NamedTemporaryFile(suffix=".wav", delete=False); tmp2.close()
                            subprocess.run(
                                [SOX, "-t", "coreaudio", wake_device, "-r", str(sample_rate), "-c", "1",
                                 "-b", "16", "-e", "signed-integer", tmp2.name, "trim", "0", "8"],
                                timeout=12, capture_output=True)
                            task = transcribe(tmp2.name)
                            if task:
                                print(f"[CODEC] Heard: {task}")
                                push(lambda t=task: dispatch(t))
            except Exception as e:
                print(f"[CODEC] Wake whisper error: {e}")
            finally:
                try: os.unlink(tmp.name)
                except Exception as e: log.debug("Wake temp file cleanup failed: %s", e)
        except Exception as e:
            print(f"[CODEC] Wake listener error: {e}")
            time.sleep(0.5)
        time.sleep(0.1)

# ── KEYBOARD ──────────────────────────────────────────────────────────────────
def on_press(key):
    now = time.time()
    if key == keyboard.Key.f13:
        if now - state["last_f13"] < 1.5: return
        state["last_f13"] = now
        # H-2: atomic flip (the wake-word thread also writes state["active"]).
        if _toggle_active():          # now ON
            reset_voice_session()
            push(lambda: show_toggle_overlay(True, "F18=voice  F16=text  **=screen  ++=doc  --=chat"))
            print("[CODEC] ON -- F18=voice | F16=text | *=screen | +=doc | --=chat")
        else:                         # now OFF
            push(lambda: show_toggle_overlay(False))
            push(close_session)
            reset_voice_session()
            print("[CODEC] OFF")
        return
    if not state["active"]: return
    if key == keyboard.Key.f16:
        if not state["recording"]:
            # Run text dialog in its own thread so it opens instantly
            # (don't wait for work_queue which may be blocked by vision/LLM)
            threading.Thread(target=do_text, daemon=True).start()
        return
    if key == keyboard.Key.f18:
        if not state["recording"]:
            # Don't start recording while TTS is speaking — mic captures speaker output
            if _core.tts_playing:
                print("[CODEC] F18 ignored — TTS still playing")
                return
            # Don't start if dispatch is still processing (cooldown)
            if time.time() < _dispatch_cooldown:
                print("[CODEC] F18 ignored — still processing")
                return
            # H-2: atomically claim the recording slot. If another thread (a
            # double-F18 or the wake-word path) already claimed it, bail instead
            # of starting a second sox into the same audio_path.
            if not _try_begin_recording():
                return
            threading.Thread(target=lambda: subprocess.run(
                ['afplay', '/System/Library/Sounds/Glass.aiff'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL), daemon=True).start()
            push(do_start_recording)
            state["overlay_proc"] = show_recording_overlay('F18')
        return
    if hasattr(key, 'char') and key.char == '*':
        if now - state["last_star"] < 0.25 and now - state.get("last_screenshot_time", 0) > 8:
            print("[CODEC] Star x2 -- screenshot mode")
            push(do_screenshot_question)
            state["last_star"] = 0.0
            state["last_screenshot_time"] = now
            return
        state["last_star"] = now
        return
    if hasattr(key, 'char') and key.char == '+':
        if now - state.get("last_plus", 0.0) < 0.5:
            print("[CODEC] Plus x2 -- document mode")
            push(do_document_input)
            state["last_plus"] = 0.0
            return
        state["last_plus"] = now
        return
    if hasattr(key, 'char') and key.char == '-':
        if now - state.get("last_minus", 0.0) < 0.5:
            print("[CODEC] Minus x2 -- live chat mode")
            voice_url = _cfg.get("voice_url", "http://localhost:8090/voice?auto=1")
            push(lambda: show_overlay('Live Chat connecting...', '#E8711A', 3000))
            subprocess.Popen(["open", "-a", "Google Chrome", voice_url])
            state["last_minus"] = 0.0
            return
        state["last_minus"] = now
        return

def on_release(key):
    if key == keyboard.Key.f18 and state["recording"]:
        # Kill overlay immediately on release (don't wait for work queue)
        ovl = state.get("overlay_proc")
        if ovl:
            try: ovl.terminate()
            except Exception: pass
            state["overlay_proc"] = None
        threading.Thread(target=lambda: subprocess.run(
            ['afplay', '/System/Library/Sounds/Pop.aiff'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL), daemon=True).start()
        push(do_stop_voice)

# ── MAIN ──────────────────────────────────────────────────────────────────────
def _graceful_shutdown(signum=None, frame=None):
    """C-1 (PR-4A): terminate the recording + overlay subprocesses and unlink the
    temp audio file so PM2 SIGTERM (restart / reboot / max-memory) doesn't orphan
    `sox`/tkinter children or leak temp files. Registered as the SIGTERM/SIGINT
    handler AND via atexit. Idempotent (state nulled) and never raises. On the
    signal path (signum set) it exits 0; on the atexit path it just cleans up."""
    rec = state.get("rec_proc")
    if rec:
        try:
            rec.terminate(); rec.wait(timeout=2)
        except Exception as e:
            log.debug("Shutdown: rec_proc cleanup failed: %s", e)
        state["rec_proc"] = None
    ovl = state.get("overlay_proc")
    if ovl:
        try:
            ovl.terminate()
        except Exception as e:
            log.debug("Shutdown: overlay_proc cleanup failed: %s", e)
        state["overlay_proc"] = None
    audio = state.get("audio_path")
    if audio:
        try:
            if os.path.exists(audio):
                os.unlink(audio)
        except Exception as e:
            log.debug("Shutdown: audio_path unlink failed: %s", e)
        state["audio_path"] = None
    if signum is not None:   # real signal (not atexit) → exit within PM2's 10s window
        sys.exit(0)


def main():
    from codec_logging import setup_logging
    setup_logging()
    # C-1 (PR-4A): graceful shutdown — registered here (not at module top) so the
    # handler sees the `state` dict. Replaces the old no-op SIGINT/SIGTERM handlers.
    signal.signal(signal.SIGTERM, _graceful_shutdown)
    signal.signal(signal.SIGINT, _graceful_shutdown)
    atexit.register(_graceful_shutdown)
    init_db()
    for f in [SESSION_ALIVE, TASK_QUEUE_FILE, DRAFT_TASK_FILE]:
        try: os.unlink(f)
        except Exception as e: log.debug("Startup file cleanup failed (%s): %s", f, e)

    stream_label = "ON" if STREAMING else "OFF"
    wake_label = "ON" if WAKE_WORD else "OFF"
    O = "\033[38;2;232;113;26m"
    D = "\033[38;2;80;80;80m"
    W = "\033[38;2;200;200;200m"
    R = "\033[0m"
    print(f"""
{O}    ╔═══════════════════════════════════════════╗
    ║                                           ║
    ║  ██████  ██████  ██████  ███████  ██████  ║
    ║ ██      ██    ██ ██   ██ ██      ██       ║
    ║ ██      ██    ██ ██   ██ █████   ██       ║
    ║ ██      ██    ██ ██   ██ ██      ██       ║
    ║  ██████  ██████  ██████  ███████  ██████  ║
    ║                                   v2.1.0  ║
    ╠═══════════════════════════════════════════╣
    ║{W}  F13 toggle   F18 voice   ** screen       {O}║
    ║{W}  F16 text     ++ doc     -- chat          {O}║
    ║{W}  Hey CODEC = wake word (hands-free)           {O}║
    ╠═══════════════════════════════════════════╣
    ║{D}  Stream={stream_label}  Wake={wake_label}  Skills=ON            {O}║
    ╚═══════════════════════════════════════════╝{R}""")

    load_skills()
    print("[CODEC] Whisper: HTTP (port 8084)")
    print("[CODEC] Vision: Qwen 3.6 (port 8083)")
    mem = get_memory(3)
    if mem: print(f"[CODEC] Memory: {mem.count(chr(10))+1} sessions loaded")
    convs = get_recent_conversations(10)
    if convs: print(f"[CODEC] Persistent memory: {len(convs)} messages from past sessions")
    if WAKE_WORD: print("[CODEC] Wake word: ON")
    print("[CODEC] Online. Press F13 to activate.")

    threading.Thread(target=worker, daemon=True).start()
    if WAKE_WORD:
        threading.Thread(target=wake_word_listener, daemon=True).start()

    while True:
        try:
            with keyboard.Listener(on_press=on_press, on_release=on_release) as l:
                l.join()
        except Exception as e:
            print(f"[CODEC] Listener restarting: {e}")
            time.sleep(0.5)

if __name__ == "__main__":
    main()
