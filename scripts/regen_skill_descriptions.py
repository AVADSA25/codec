"""One-shot: regenerate SKILL_DESCRIPTION for every skill via local Qwen LLM.

Why: claude.ai picks tools primarily by description. Generic or short
descriptions → wrong-tool calls. This script reads each skill file, asks
Qwen to write a punchy 1-sentence description grounded in the actual code,
and patches SKILL_DESCRIPTION in place.

Safety:
  - Dry-run by default: prints proposed changes, writes nothing.
  - Pass --apply to actually patch files.
  - Always skips files that look unusual or have no existing SKILL_DESCRIPTION.

Run:
    python3 scripts/regen_skill_descriptions.py                 # dry run
    python3 scripts/regen_skill_descriptions.py --apply         # patch
    python3 scripts/regen_skill_descriptions.py --only weather  # single skill
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILLS = REPO / "skills"
sys.path.insert(0, str(REPO))

from codec_config import QWEN_BASE_URL, QWEN_MODEL

PROMPT = """Write a single-sentence tool description for an AI assistant's tool registry.

Target audience: Claude choosing which tool to call. Quality bar: helps Claude
disambiguate from ~55 other tools.

Rules:
- 1 sentence, 15-30 words
- Start with a strong verb (Return, Send, Open, Fetch, Search, Generate, etc.)
- Mention concrete nouns that appear in the code (e.g. "Gmail", "Philips Hue", "Kokoro TTS")
- NO marketing language, NO filler ("this tool", "allows you to")
- NO trailing period if over 25 words

Existing description (may be bad): {old_desc}
Trigger phrases: {triggers}

Code signature / docstring:
<<<
{head}
>>>

Return ONLY the new description sentence, nothing else."""


def _llm(prompt: str) -> str:
    import requests
    r = requests.post(
        f"{QWEN_BASE_URL}/chat/completions",
        json={
            "model": QWEN_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 80,
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip().strip('"').strip()


def _extract_meta(text: str) -> dict:
    out = {}
    for key in ("SKILL_NAME", "SKILL_DESCRIPTION"):
        m = re.search(rf'^{key}\s*=\s*(["\'])(.+?)\1', text, re.MULTILINE)
        if m:
            out[key] = m.group(2)
    m = re.search(r"^SKILL_TRIGGERS\s*=\s*\[(.*?)\]", text, re.DOTALL | re.MULTILINE)
    if m:
        out["SKILL_TRIGGERS"] = re.findall(r'["\'](.+?)["\']', m.group(1))
    return out


def _patch(text: str, new_desc: str) -> str:
    # Escape double quotes in new description
    safe = new_desc.replace('"', '\\"')
    return re.sub(
        r'^(SKILL_DESCRIPTION\s*=\s*)(["\']).+?\2',
        rf'\1"{safe}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Write changes to disk")
    ap.add_argument("--only", default=None, help="Only regenerate this skill (by stem name)")
    args = ap.parse_args()

    files = sorted(SKILLS.glob("*.py"))
    changed = 0
    skipped = 0
    for f in files:
        if f.name.startswith("_") or f.name == "codec.py":
            continue
        if args.only and f.stem != args.only:
            continue
        text = f.read_text()
        meta = _extract_meta(text)
        if "SKILL_DESCRIPTION" not in meta:
            skipped += 1
            continue
        head = text[:2500]
        old = meta["SKILL_DESCRIPTION"]
        triggers = meta.get("SKILL_TRIGGERS", [])
        prompt = PROMPT.format(
            old_desc=old,
            triggers=", ".join(triggers[:8]) if triggers else "(none)",
            head=head,
        )
        try:
            new = _llm(prompt)
        except Exception as e:
            print(f"  ✗ {f.name}: LLM error {type(e).__name__}: {e}")
            continue
        # Strip any leading "Description:" etc
        new = re.sub(r"^(description|new|output)\s*:\s*", "", new, flags=re.IGNORECASE).strip()
        new = new.splitlines()[0].strip()  # one line only
        if not new or len(new) < 10 or len(new) > 300:
            print(f"  ⊘ {f.name}: LLM output rejected ({len(new)} chars)")
            skipped += 1
            continue
        print(f"\n── {f.stem}")
        print(f"   OLD: {old}")
        print(f"   NEW: {new}")
        if args.apply:
            patched = _patch(text, new)
            if patched != text:
                f.write_text(patched)
                changed += 1
    print(f"\n{'APPLIED' if args.apply else 'DRY RUN'}: {changed} changed, {skipped} skipped, {len(files)} files scanned.")


if __name__ == "__main__":
    main()
