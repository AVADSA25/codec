"""MCP HTTP blast radius — side-effect skills are blocked or owner-consent-gated
over the remote (claude.ai) transport, and unchanged over stdio.

Audit finding (2026-09): with mcp_default_allow=true, only _HTTP_BLOCKED plus
the destructive set were refused over HTTP, so a remote caller could plant
standing rules (appended to every local chat system prompt), write skills,
post to the n8n webhook, schedule jobs, drive Chrome / the mouse, and read the
clipboard or screen with no owner involvement.

Pins:
  - standing_rules + create_skill: blocked over HTTP (audited stub), registered
    normally over stdio, and NOT in _HTTP_BLOCKED (which would also refuse them
    over stdio via codec_consent.is_destructive_skill)
  - delegate / scheduler / chrome_fill / chrome_click_cdp / mouse_control /
    clipboard / screenshot_text, and since 28 Sep every other chrome_* tool
    except chrome_scroll: over HTTP each call waits for the owner's PWA
    approval (codec_ask_user strict consent); over stdio no prompt
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import codec_ask_user
import codec_config
import codec_consent
import codec_mcp
from codec_skill_registry import SkillRegistry

HTTP_ONLY_BLOCKED = ["standing_rules", "create_skill"]
FIRST_CONSENT_TOOLS = ["delegate", "scheduler", "chrome_fill", "chrome_click_cdp",
                       "mouse_control", "clipboard", "screenshot_text"]
CHROME_CONSENT_TOOLS = ["chrome_read", "chrome_extract", "chrome_tabs", "chrome_open",
                        "chrome_search", "chrome_close", "chrome_automate"]
CONSENT_TOOLS = FIRST_CONSENT_TOOLS + CHROME_CONSENT_TOOLS


# ── blocklist composition (pure) ─────────────────────────────────────────────


def test_http_blocklist_adds_side_effect_skills_and_keeps_existing():
    blocked = codec_config._mcp_blocked_tools("http", {})
    for name in HTTP_ONLY_BLOCKED + codec_config._HTTP_BLOCKED:
        assert name in blocked, name


def test_user_config_cannot_unblock_over_http():
    blocked = codec_config._mcp_blocked_tools("http", {"mcp_blocked_tools": []})
    assert set(HTTP_ONLY_BLOCKED) <= set(blocked)


def test_stdio_blocklist_unchanged():
    assert codec_config._mcp_blocked_tools("stdio", {}) == codec_config._STDIO_BLOCKED
    for name in HTTP_ONLY_BLOCKED + CONSENT_TOOLS:
        assert name not in codec_config._mcp_blocked_tools("stdio", {})


def test_new_entries_do_not_leak_into_the_cross_path_destructive_set():
    """_HTTP_BLOCKED membership makes a skill destructive on every path (stdio
    MCP refusal, chat consent). The new HTTP-only entries must stay out of it."""
    for name in HTTP_ONLY_BLOCKED + CONSENT_TOOLS:
        assert name not in codec_config._HTTP_BLOCKED
        assert codec_consent.mcp_allowed(name, registry=_registry()) is True


def test_consent_list_only_grows_and_chrome_scroll_stays_open():
    """The list is add-only (AGENTS.md §10): the first seven stay, the seven
    chrome tools are added, and chrome_scroll (moves the page, reads nothing)
    runs over HTTP without a prompt."""
    assert set(FIRST_CONSENT_TOOLS + CHROME_CONSENT_TOOLS) <= set(codec_config._HTTP_CONSENT_REQUIRED)
    assert codec_consent.mcp_http_consent_required("chrome_scroll") is False
    reg = _registry()
    for name in CHROME_CONSENT_TOOLS + ["chrome_scroll"]:
        assert reg.get_meta(name) is not None, f"{name} is not a skill any more"


def test_consent_tools_are_not_blocked_over_http():
    blocked = codec_config._mcp_blocked_tools("http", {})
    for name in CONSENT_TOOLS:
        assert name not in blocked
        assert codec_consent.mcp_http_consent_required(name) is True
    assert codec_consent.mcp_http_consent_required("weather") is False


# ── tool registration + calls through the real codec_mcp.tool_fn ────────────


def _registry():
    reg = SkillRegistry(str(REPO / "skills"))
    reg.scan()
    return reg


class _FakeMCP:
    """Captures what _load_skill_tools_into registers (no FastMCP needed)."""

    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


@pytest.fixture
def mcp_env(monkeypatch):
    """Build the tool set for a transport with skill execution and ask_user faked."""
    reg = _registry()
    ran, asked, audits = [], [], []

    def fake_load(name):
        return SimpleNamespace(run=lambda t, c="": ran.append(name) or f"RAN {name}")

    monkeypatch.setattr(reg, "load", fake_load)
    monkeypatch.setattr(codec_mcp, "_mcp_registry", reg)
    monkeypatch.setattr(codec_mcp, "MCP_DEFAULT_ALLOW", True)
    monkeypatch.setattr(codec_mcp, "run_with_hooks",
                        lambda **kw: kw["invoke"](kw["task"], kw["context"]))
    monkeypatch.setattr(codec_mcp, "_audit",
                        lambda tool, **kw: audits.append((tool, kw)))
    monkeypatch.delenv("CONSENT_GATE_ENABLED", raising=False)
    env = SimpleNamespace(ran=ran, asked=asked, audits=audits, answer=None)

    def fake_ask(question, **kw):
        asked.append(kw)
        return env.answer

    monkeypatch.setattr(codec_ask_user, "ask", fake_ask)

    def build(transport):
        monkeypatch.setenv("CODEC_MCP_TRANSPORT", transport)
        monkeypatch.setattr(codec_mcp, "MCP_BLOCKED_TOOLS",
                            codec_config._mcp_blocked_tools(transport, {}))
        m = _FakeMCP()
        codec_mcp._load_skill_tools_into(m)
        return m.tools

    env.build = build
    return env


@pytest.mark.parametrize("name", HTTP_ONLY_BLOCKED)
def test_blocked_over_http_returns_stub_and_never_runs(mcp_env, name):
    tools = mcp_env.build("http")
    out = tools[name]("do something")
    assert "not available over HTTP" in out
    assert mcp_env.ran == []


@pytest.mark.parametrize("name", HTTP_ONLY_BLOCKED + CONSENT_TOOLS)
def test_available_over_stdio_without_prompt(mcp_env, name):
    tools = mcp_env.build("stdio")
    assert tools[name]("do something") == f"RAN {name}"
    assert mcp_env.asked == [], "stdio must not prompt — client has its own dialog"


@pytest.mark.parametrize("name", CONSENT_TOOLS)
def test_http_call_without_owner_approval_is_refused(mcp_env, name):
    mcp_env.answer = codec_ask_user.TIMEOUT_SENTINEL  # also what "Decline" returns
    tools = mcp_env.build("http")
    out = tools[name]("do something")
    assert mcp_env.ran == []
    assert "approval" in out
    kw = mcp_env.asked[0]
    assert kw["asked_from"] == "mcp" and kw["destructive"] is True
    assert any(a[1].get("error_type") == "OwnerConsentDenied" for a in mcp_env.audits)


@pytest.mark.parametrize("name", CONSENT_TOOLS)
def test_http_call_runs_after_owner_clicks_allow(mcp_env, name):
    mcp_env.answer = f"Allow {name}"
    tools = mcp_env.build("http")
    assert tools[name]("do something") == f"RAN {name}"
    assert len(mcp_env.asked) == 1


@pytest.mark.parametrize("answer", ["don't allow", "yes", "ok", "", None,
                                    codec_ask_user.DISABLED_SENTINEL])
def test_only_an_explicit_allow_counts(mcp_env, answer):
    mcp_env.answer = answer
    tools = mcp_env.build("http")
    tools["clipboard"]("read clipboard")
    assert mcp_env.ran == []


def test_ask_user_error_fails_closed(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("pending_questions.json unwritable")
    monkeypatch.setattr(codec_ask_user, "ask", boom)
    assert codec_consent.mcp_http_consent_ok("clipboard", "x") is False


def test_non_side_effect_tool_over_http_never_prompts(mcp_env):
    tools = mcp_env.build("http")
    assert tools["weather"]("weather in Paris") == "RAN weather"
    assert mcp_env.asked == []
