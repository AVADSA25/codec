"""Context compaction — summarize old conversations, keep recent raw"""
import os
import sys
import logging

log = logging.getLogger('codec')

# Load config once at import time (single source of truth via codec_config)
try:
    _repo_dir = os.path.dirname(os.path.abspath(__file__))
    if _repo_dir not in sys.path:
        sys.path.insert(0, _repo_dir)
    from codec_config import cfg as _cfg
    from codec_config import get_llm_api_key as _kc_get_llm
    _LLM_BASE_URL = _cfg.get("llm_base_url", "http://localhost:8083/v1")
    _LLM_MODEL = _cfg.get("llm_model", "")
    # PR-2B (D-15 partial): keychain-aware. Evaluated at import; post-
    # migration daemon restarts will pick up the Keychain value.
    _LLM_API_KEY = _kc_get_llm()
    _LLM_KWARGS = _cfg.get("llm_kwargs", {})
except ImportError:
    _LLM_BASE_URL = "http://localhost:8083/v1"
    _LLM_MODEL = ""
    _LLM_API_KEY = ""
    _LLM_KWARGS = {}


def compact_context(recent_messages: list, max_recent: int = 5, max_summary_tokens: int = 200) -> str:
    """
    Takes a list of conversation messages (dicts with 'role' and 'content').
    Returns a compacted context string:
      - Last max_recent messages kept raw
      - Older messages summarized into a brief paragraph via LLM
    """
    if not recent_messages:
        return ""

    if len(recent_messages) <= max_recent:
        return "\n".join(f"[{m['role']}] {m['content'][:200]}" for m in recent_messages)

    old_messages = recent_messages[:-max_recent]
    recent = recent_messages[-max_recent:]

    # Summarize old messages via the configured LLM
    old_text = "\n".join(
        f"[{m['role']}] {m['content'][:150]}"
        for m in old_messages[-20:]  # cap at last 20 old messages
    )

    summary = None
    try:
        # A-12 (PR-3E-2): canonical codec_llm.call replaces the inline httpx
        # POST + parse. Never raises -> "" on failure, which the fallback below
        # already handles. (Also now sends enable_thinking=False + strips any
        # <think> leak, so summaries are slightly cleaner than the old path.)
        import codec_llm
        summary = codec_llm.call(
            [
                {
                    "role": "system",
                    "content": (
                        "Summarize these conversation snippets in 2-3 sentences. "
                        "Focus on key facts, decisions, and action items. "
                        "Be extremely concise."
                    ),
                },
                {"role": "user", "content": old_text},
            ],
            base_url=_LLM_BASE_URL, model=_LLM_MODEL, api_key=_LLM_API_KEY,
            max_tokens=max_summary_tokens, temperature=0.1, timeout=15,
            extra_kwargs=_LLM_KWARGS,
        )
        if summary:
            log.info(f"Context compacted: {len(old_messages)} old msgs → {len(summary)} char summary")
    except Exception as e:
        log.warning(f"Compaction LLM failed, using fallback: {e}")

    if not summary:
        # Fallback: take key phrases from the last 5 old messages
        summary = "Previous context: " + ". ".join(
            m['content'][:50] for m in old_messages[-5:]
        )

    context = f"[SUMMARY OF EARLIER CONVERSATION]\n{summary}\n\n[RECENT MESSAGES]\n"
    context += "\n".join(f"[{m['role']}] {m['content'][:200]}" for m in recent)
    return context
