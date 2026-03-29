"""Context compaction — summarize old conversations, keep recent raw"""
import os
import json
import logging

log = logging.getLogger('codec')


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
        import httpx
        cfg_path = os.path.expanduser("~/.codec/config.json")
        with open(cfg_path) as f:
            cfg = json.load(f)
        base_url = cfg.get("llm_base_url", "http://localhost:8081/v1")
        model = cfg.get("llm_model", "")
        api_key = cfg.get("llm_api_key", "")
        llm_kwargs = cfg.get("llm_kwargs", {})

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": model,
            "messages": [
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
            "max_tokens": max_summary_tokens,
            "temperature": 0.1,
        }
        payload.update(llm_kwargs)

        r = httpx.post(
            f"{base_url}/chat/completions",
            json=payload,
            headers=headers,
            timeout=15,
        )
        if r.status_code == 200:
            summary = r.json()["choices"][0]["message"]["content"].strip()
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
