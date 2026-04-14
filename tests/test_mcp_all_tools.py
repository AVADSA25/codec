"""MCP smoke test — iterate every exposed skill, fire a canonical prompt,
assert non-None response. Prevents AI_News_Digest-style regressions.

Run:
    python3 -m pytest tests/test_mcp_all_tools.py -v
    python3 tests/test_mcp_all_tools.py          # standalone report
"""
import os, sys, json, time, traceback
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "skills"))

from codec_config import SKILLS_DIR, MCP_BLOCKED_TOOLS  # noqa: E402
from codec_skill_registry import SkillRegistry  # noqa: E402

# Canonical test prompts per skill. Short, safe, deterministic. Skills
# not listed here get a generic "ping" — they just need to return non-None.
CANONICAL_PROMPTS = {
    "weather": "weather in Marbella",
    "calculator": "2 + 2",
    "bitcoin_price": "bitcoin price",
    "password_generator": "generate password",
    "qr_generator": "qr code for hello",
    "time": "what time is it",
    "time_date": "what time is it",
    "json_formatter": 'format json {"a":1}',
    "translate": "translate hello to spanish",
    "system": "system info",
    "system_info": "system info",
    "active_window": "active window",
    "network_info": "my ip",
    "memory_search": "test",
    "memory_history": "list active facts",
    "memory_entities": "list entities",
    "tts_say": "say test",  # will fallback to macOS say
    "google_tasks": "list tasks",  # read-only
    "google_calendar": "list today events",
    "notes": "list notes",  # read intent, opens Notes
    "reminders": "list reminders",
    "pomodoro": "pomodoro status",
    "file_search": "find file CLAUDE.md",
    "clipboard": "read clipboard",
}

# Skills we skip because they have side effects that are hard to unit-test
# or require live external services that may be down.
SKIP_SKILLS = {
    # Write/destructive — avoid in automated run
    "imessage_send", "memory_save", "create_skill", "skill_forge",
    "google_docs", "google_sheets", "google_slides", "google_drive",
    "google_gmail", "google_keep",
    # Hardware/IOT side effects
    "philips_hue", "music", "volume_brightness", "brightness",
    # Interactive/vision
    "mouse_control", "screenshot_text", "chrome_click_cdp", "chrome_fill",
    "chrome_automate", "chrome_open", "chrome_close", "chrome_extract",
    "chrome_read", "chrome_scroll", "chrome_search", "chrome_tabs",
    # Meta/slow
    "AI_News_Digest", "ai_news_digest", "web_search", "delegate", "codec",
    "scheduler", "scheduler_skill", "ax_control", "file_ops",
    "python_exec", "terminal", "process_manager", "pm2_control",
    "app_switch", "timer",
}


def iter_exposed_skills():
    reg = SkillRegistry(SKILLS_DIR)
    reg.scan()
    for name in reg.names():
        meta = reg.get_meta(name) or {}
        if meta.get("SKILL_MCP_EXPOSE") is False:
            continue
        skill_name = meta.get("SKILL_NAME", name)
        if skill_name in MCP_BLOCKED_TOOLS or name in MCP_BLOCKED_TOOLS:
            continue
        yield name, skill_name, reg


def run_one(registry, name, skill_name, timeout=15):
    prompt = CANONICAL_PROMPTS.get(skill_name) or CANONICAL_PROMPTS.get(name) or "ping"
    t0 = time.time()
    try:
        mod = registry.load(name)
        if mod is None:
            return {"ok": False, "reason": "load_returned_none", "elapsed": 0}
        if not hasattr(mod, "run"):
            return {"ok": False, "reason": "no_run_function", "elapsed": 0}
        result = mod.run(prompt)
        elapsed = time.time() - t0
        if result is None:
            return {"ok": False, "reason": "returned_none", "elapsed": elapsed,
                    "prompt": prompt}
        if isinstance(result, str) and "error" in result.lower()[:30]:
            return {"ok": True, "warn": "contains_error", "elapsed": elapsed,
                    "preview": str(result)[:120]}
        return {"ok": True, "elapsed": elapsed, "preview": str(result)[:120]}
    except Exception as e:
        return {"ok": False, "reason": f"{type(e).__name__}: {e}",
                "elapsed": time.time() - t0,
                "traceback": traceback.format_exc()[-400:]}


def test_all_exposed_skills_respond():
    """pytest entry — asserts every exposed, non-skipped skill returns non-None."""
    failures = []
    for name, skill_name, reg in iter_exposed_skills():
        if skill_name in SKIP_SKILLS or name in SKIP_SKILLS:
            continue
        result = run_one(reg, name, skill_name)
        if not result["ok"]:
            failures.append((name, skill_name, result))
    assert not failures, "Skill failures:\n" + json.dumps(failures, indent=2, default=str)


def main():
    """Standalone CLI report."""
    print("=" * 70)
    print("CODEC MCP Smoke Test")
    print("=" * 70)

    results = {"ok": [], "fail": [], "skip": []}
    for name, skill_name, reg in iter_exposed_skills():
        if skill_name in SKIP_SKILLS or name in SKIP_SKILLS:
            results["skip"].append(skill_name)
            print(f"  ⊘ SKIP   {skill_name}")
            continue
        r = run_one(reg, name, skill_name)
        if r["ok"]:
            results["ok"].append(skill_name)
            warn = f" ⚠ {r.get('warn')}" if r.get("warn") else ""
            print(f"  ✓ OK     {skill_name:<22} ({r['elapsed']:.2f}s){warn}")
        else:
            results["fail"].append((skill_name, r["reason"]))
            print(f"  ✗ FAIL   {skill_name:<22} — {r['reason']}")

    print("=" * 70)
    print(f"PASS: {len(results['ok'])}  FAIL: {len(results['fail'])}  SKIP: {len(results['skip'])}")
    if results["fail"]:
        print("\nFailures:")
        for name, reason in results["fail"]:
            print(f"  - {name}: {reason}")
        sys.exit(1)


if __name__ == "__main__":
    main()
