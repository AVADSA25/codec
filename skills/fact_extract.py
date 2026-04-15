"""CODEC Skill: fact_extract — pull discrete facts from free text and save them.

Given a blob of text (conversation transcript, meeting notes, email, etc.),
this skill asks the local Qwen LLM to extract atomic facts and persists each
one to CODEC memory via the same CodecMemory backing store used by memory_save.

Usage from Claude:
    fact_extract "had dinner with Juan last night. He's launching a fintech
    called NovaPay in Q3 2026. Wants to partner on KYC flows. His wife Marta
    is expecting in July."

Writes N facts and returns a summary like:
    Saved 4 facts:
      1. Juan is launching NovaPay fintech in Q3 2026
      2. Juan wants to partner on KYC flows
      3. Juan's wife Marta is expecting in July
      4. MF had dinner with Juan last night
"""
SKILL_NAME = "fact_extract"
SKILL_DESCRIPTION = "Extract atomic facts from free text (conversation, notes, transcript) and auto-save each to CODEC memory. Builds a durable knowledge base over time."
SKILL_TRIGGERS = [
    "extract facts", "fact extract", "remember from this",
    "save facts from", "memorize this text", "pull facts",
]
SKILL_MCP_EXPOSE = True

import os
import sys
import json
import re

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from codec_config import QWEN_BASE_URL, QWEN_MODEL

_EXTRACT_PROMPT = """You are extracting atomic, durable facts from text for a personal knowledge base.

Rules:
- Output a JSON array of strings, each string ONE self-contained fact
- Each fact must be understandable without the original context
- Resolve pronouns to names
- Omit filler, opinions, and transient details (weather, moods)
- Keep facts concise — under 140 chars
- If no facts, return []

Text:
<<<
{text}
>>>

Return ONLY the JSON array, nothing else."""


def _call_llm(prompt: str) -> str:
    from codec_retry import retry_post
    try:
        r = retry_post(
            f"{QWEN_BASE_URL}/chat/completions",
            json={
                "model": QWEN_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 800,
            },
            timeout=60,
            max_attempts=3,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"__ERR__:{type(e).__name__}:{e}"


def _parse_facts(raw: str) -> list[str]:
    # Strip code fences
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    # Try to locate the first [...] array
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except Exception:
        return []
    return [str(x).strip() for x in arr if isinstance(x, (str, int, float)) and str(x).strip()]


def _save(fact: str) -> bool:
    try:
        from codec_memory import CodecMemory
        mem = CodecMemory()
        mem.save(session_id="fact_extract", role="fact", content=fact, user_id="mickael")
        # Best-effort structured fact storage too
        try:
            mem.store_fact(fact)
        except Exception:
            pass
        return True
    except Exception:
        return False


def run(task: str, context: str = "") -> str:
    text = (task or "").strip()
    # Strip common trigger prefixes
    for trig in ("extract facts from", "extract facts", "fact extract",
                 "remember from this", "save facts from", "pull facts from",
                 "memorize this text"):
        if text.lower().startswith(trig):
            text = text[len(trig):].strip(" :,-")
            break

    if context:
        text = (text + "\n\n" + context).strip() if text else context.strip()

    if len(text) < 20:
        return "Give me at least one or two sentences to extract facts from."

    if len(text) > 8000:
        text = text[:8000]

    raw = _call_llm(_EXTRACT_PROMPT.format(text=text))
    if raw.startswith("__ERR__:"):
        return f"LLM extraction failed: {raw[8:]}"

    facts = _parse_facts(raw)
    if not facts:
        return "No durable facts found in this text."

    saved = [f for f in facts if _save(f)]
    lines = [f"Saved {len(saved)}/{len(facts)} facts:"]
    for i, f in enumerate(saved, 1):
        lines.append(f"  {i}. {f}")
    return "\n".join(lines)
