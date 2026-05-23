"""CODEC outbound-bridge shared core (A-19, PR-3F).

The genuinely-identical helpers that `codec_telegram` and `codec_imessage` each
had their own copy of: skill dispatch (`load_dispatch`/`try_skill`), the
canonical bridge LLM call (`call_llm`, persona chosen by channel, via the
canonical `codec_llm.call`), and memory persistence (`save_to_memory`). Each
bridge keeps its OWN `process_message` — those have intentionally drifted
(telegram: audio transcription + Gemini fallback + daily briefing; imessage:
goal tracking + intent classification) and unifying them would risk regressing a
working channel. This module is the seed for an "add a channel" surface
(CLAUDE.md §1: future WhatsApp / Discord).

Inbound stays PWA-only — these helpers are OUTBOUND only.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime

log = logging.getLogger("codec.bridges")

MEMORY_DB = os.path.expanduser("~/.codec/memory.db")

# Skills that need a terminal / GUI and must not run from a headless bridge.
_SKIP_SKILLS = {"open_terminal", "run_command", "vibe_code", "deep_chat",
                "memory_search", "ask_mike_to_build"}

# Per-channel assistant persona (the only thing that differed between the two
# bridges' call_llm). `{now}` is the formatted current date/time.
_PERSONAS = {
    "telegram": (
        "You are CODEC, a personal AI assistant replying via Telegram. "
        "Today is {now}. Be concise and direct. "
        "Keep replies under 3 sentences unless more detail is needed. "
        "You can use Markdown formatting. Be natural and helpful."
    ),
    "imessage": (
        "You are CODEC, a personal AI assistant replying via iMessage. "
        "Today is {now}. Be concise — this is a text message conversation. "
        "Keep replies under 3 sentences unless more detail is needed. "
        "Be natural and conversational, like texting a smart friend."
    ),
}

# ── Skill dispatch (lazy — codec_dispatch pulls pynput/GUI deps) ──────────────
_dispatch_loaded = False
_check_skill = None
_run_skill = None


def load_dispatch() -> bool:
    """Lazy-load codec_dispatch (handling pynput/GUI import issues). Returns
    True if skill dispatch is available."""
    global _dispatch_loaded, _check_skill, _run_skill
    if _dispatch_loaded:
        return _check_skill is not None
    _dispatch_loaded = True
    try:
        from codec_dispatch import check_skill, run_skill
        _check_skill = check_skill
        _run_skill = run_skill
        log.info("Skill dispatch loaded")
        return True
    except Exception as e:
        log.warning(f"Skill dispatch unavailable ({e}) — LLM-only mode")
        return False


def try_skill(text):
    """Match a CODEC skill for `text`. Returns (skill_name, result) or
    (None, None) — skipping terminal/GUI skills that a bridge can't run."""
    if not load_dispatch():
        return (None, None)
    try:
        skill = _check_skill(text)
        if skill:
            if skill["name"] in _SKIP_SKILLS:
                return (None, None)
            result = _run_skill(skill, text)
            if result:
                return (skill["name"], str(result))
    except Exception as e:
        log.warning(f"Skill error: {e}")
    return (None, None)


# ── Canonical bridge LLM call ────────────────────────────────────────────────
def call_llm(channel, text, llm_cfg, conversation_history=None,
             system_prompt_override=None):
    """The shared outbound-bridge LLM call. `channel` selects the persona;
    routes through codec_llm.call (default never-raise → "" → None for graceful
    bridge degradation). `chat_template_kwargs` is filtered out of
    `llm_cfg["kwargs"]` so codec_llm's enable_thinking=False is preserved."""
    import codec_llm
    if system_prompt_override:
        sys_prompt = system_prompt_override
    else:
        now_str = datetime.now().strftime("%A %B %d, %Y at %H:%M")
        sys_prompt = _PERSONAS[channel].format(now=now_str)

    messages = [{"role": "system", "content": sys_prompt}]
    if conversation_history:
        messages.extend(conversation_history[-8:])
    messages.append({"role": "user", "content": text})

    extra = {k: v for k, v in llm_cfg["kwargs"].items() if k != "chat_template_kwargs"}
    content = codec_llm.call(
        messages, base_url=llm_cfg["base_url"], model=llm_cfg["model"],
        api_key=llm_cfg["api_key"], max_tokens=1500, temperature=0.7,
        timeout=120, extra_kwargs=extra,
    )
    return content if content else None


# ── Memory persistence (cross-channel recall via memory.db) ──────────────────
def save_to_memory(channel, conv_id, user_text, assistant_text):
    """Store the exchange in memory.db under session_id `<channel>-<conv_id>`."""
    try:
        os.makedirs(os.path.dirname(MEMORY_DB), exist_ok=True)
        conn = sqlite3.connect(MEMORY_DB)
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT, timestamp TEXT, role TEXT, content TEXT
            )
        """)
        session_id = f"{channel}-{conv_id}"
        ts = datetime.now().isoformat()
        c.execute("INSERT INTO conversations (session_id, timestamp, role, content) VALUES (?,?,?,?)",
                  (session_id, ts, "user", user_text[:2000]))
        c.execute("INSERT INTO conversations (session_id, timestamp, role, content) VALUES (?,?,?,?)",
                  (session_id, ts, "assistant", assistant_text[:2000]))
        conn.commit()
        conn.close()
    except Exception as e:
        log.debug(f"Memory save error: {e}")
