"""Tests for CODEC Voice modes (flash / default / think).

Design: docs/VOICE-MODES-DESIGN.md. Three modes on the live voice pipeline:
- flash:  trimmed prompt + small max_tokens + no per-turn injections
- default: byte-identical to pre-mode behavior
- think:  agent loop over a curated skill allowlist with narrated progress
"""
import asyncio
import os
import sys
import types


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import codec_voice  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────
def _mk_pipeline(mode="default"):
    """Bare VoicePipeline without a websocket / registry scan."""
    p = object.__new__(codec_voice.VoicePipeline)
    p.mode = mode
    p.messages = [{"role": "system", "content": "sys"}]
    p._warmed_up = True
    p._stream_error = False
    p._http = None
    p.interrupted = asyncio.Event()
    return p


# ── mode-switch phrase parsing ───────────────────────────────────────────────
def test_parse_flash_mode():
    assert codec_voice.parse_mode_switch("flash mode") == "flash"
    assert codec_voice.parse_mode_switch("switch to flash mode") == "flash"
    assert codec_voice.parse_mode_switch("Flash mode please") == "flash"


def test_parse_think_mode():
    assert codec_voice.parse_mode_switch("think mode") == "think"
    assert codec_voice.parse_mode_switch("thinking mode") == "think"
    assert codec_voice.parse_mode_switch("go to think mode") == "think"


def test_parse_default_mode():
    assert codec_voice.parse_mode_switch("normal mode") == "default"
    assert codec_voice.parse_mode_switch("regular mode") == "default"
    assert codec_voice.parse_mode_switch("default mode") == "default"


def test_parse_no_false_positive_in_long_sentences():
    # "think mode" inside a real sentence must NOT switch modes
    assert codec_voice.parse_mode_switch(
        "i think mode collapse is a fascinating ai failure case to study") is None
    assert codec_voice.parse_mode_switch("what's the weather like") is None
    assert codec_voice.parse_mode_switch("") is None


# ── flash mode: per-call parameters ──────────────────────────────────────────
def test_flash_trims_context_harder():
    p = _mk_pipeline("flash")
    for i in range(30):
        p.messages.append({"role": "user", "content": f"u{i}"})
        p.messages.append({"role": "assistant", "content": f"a{i}"})
    flash_msgs = p._trimmed_messages()
    p.mode = "default"
    default_msgs = p._trimmed_messages()
    assert len(flash_msgs) == 1 + codec_voice.FLASH_CFG["context_turns"] * 2
    assert len(default_msgs) == 1 + codec_voice.MAX_CONTEXT_TURNS * 2
    assert len(flash_msgs) < len(default_msgs)


def test_flash_uses_small_max_tokens_and_skips_injections(monkeypatch):
    captured = {}

    async def fake_astream(messages, **kw):
        captured.update(kw)
        captured["messages"] = messages
        yield "ok."

    import codec_llm
    monkeypatch.setattr(codec_llm, "astream", fake_astream)

    touched = {"memory": False, "observer": False}
    import codec_memory
    monkeypatch.setattr(codec_memory.CodecMemory, "get_context",
                        lambda self, *a, **k: touched.__setitem__("memory", True) or "ctx")
    import codec_observer
    monkeypatch.setattr(codec_observer, "maybe_inject_observation_summary",
                        lambda **k: touched.__setitem__("observer", True) or (None, ""))

    p = _mk_pipeline("flash")

    async def consume():
        async for _ in p.generate_response("hello"):
            pass
    asyncio.run(consume())

    assert captured["max_tokens"] == codec_voice.FLASH_CFG["max_tokens"]
    assert touched["memory"] is False, "flash must skip per-turn memory injection"
    assert touched["observer"] is False, "flash must skip observer injection"
    # flash brevity rule lands in the system prompt actually sent
    assert "FLASH" in captured["messages"][0]["content"]


def test_default_keeps_2000_tokens(monkeypatch):
    captured = {}

    async def fake_astream(messages, **kw):
        captured.update(kw)
        yield "ok."

    import codec_llm
    monkeypatch.setattr(codec_llm, "astream", fake_astream)

    p = _mk_pipeline("default")

    async def consume():
        async for _ in p.generate_response("hello"):
            pass
    asyncio.run(consume())
    assert captured["max_tokens"] == 2000


# ── think mode: tool allowlist ───────────────────────────────────────────────
def _fake_tools(names):
    return [types.SimpleNamespace(name=n) for n in names]


def test_think_tools_enforce_allowlist(monkeypatch):
    import codec_agents
    monkeypatch.setattr(codec_agents, "load_skill_tools", lambda: _fake_tools([
        "philips_hue", "music", "terminal", "shell", "file_write",
        "web_search", "python_exec", "google_calendar",
    ]))
    p = _mk_pipeline("think")
    names = {t.name for t in p._think_tools()}
    assert "philips_hue" in names and "music" in names and "web_search" in names
    assert "terminal" not in names
    assert "shell" not in names
    assert "file_write" not in names
    assert "python_exec" not in names


def test_think_hard_exclusions_beat_config(monkeypatch):
    # Even if config tries to allowlist terminal, the hard-exclusion wins.
    import codec_agents
    monkeypatch.setattr(codec_agents, "load_skill_tools",
                        lambda: _fake_tools(["terminal", "philips_hue"]))
    monkeypatch.setitem(codec_voice.THINK_CFG, "skills",
                        ["terminal", "philips_hue"])
    try:
        p = _mk_pipeline("think")
        names = {t.name for t in p._think_tools()}
        assert "terminal" not in names
        assert "philips_hue" in names
    finally:
        codec_voice.THINK_CFG.pop("skills", None)


# ── think mode: interrupt + wall-clock guards ────────────────────────────────
class _SlowAgent:
    def __init__(self, *a, **k):
        pass

    async def run(self, task, context="", callback=None):
        await asyncio.sleep(30)
        return "never"


def test_think_interrupt_aborts_quickly(monkeypatch):
    import codec_agents
    monkeypatch.setattr(codec_agents, "Agent", _SlowAgent)
    monkeypatch.setattr(codec_agents, "load_skill_tools", lambda: [])
    p = _mk_pipeline("think")

    async def go():
        async def trip():
            await asyncio.sleep(0.15)
            p.interrupted.set()
        asyncio.get_event_loop().create_task(trip())
        return await asyncio.wait_for(p.dispatch_think_agent("do things"), timeout=5)

    out = asyncio.run(go())
    assert out is not None and "stopp" in out.lower()  # "Stopped."


def test_think_wall_clock_guard(monkeypatch):
    import codec_agents
    monkeypatch.setattr(codec_agents, "Agent", _SlowAgent)
    monkeypatch.setattr(codec_agents, "load_skill_tools", lambda: [])
    monkeypatch.setitem(codec_voice.THINK_CFG, "max_seconds", 0.3)
    try:
        p = _mk_pipeline("think")
        out = asyncio.run(asyncio.wait_for(
            p.dispatch_think_agent("do things"), timeout=5))
        assert out is not None and ("long" in out.lower() or "time" in out.lower())
    finally:
        codec_voice.THINK_CFG["max_seconds"] = 120
