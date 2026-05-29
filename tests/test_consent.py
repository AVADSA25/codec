"""re-audit consent gate (Decision C): a skill is destructive if it declares
SKILL_DESTRUCTIVE=True (registry flag, extensible), is in _HTTP_BLOCKED
(backstop), or is a known high-power built-in. codec_consent is the shared
classifier for the chat (codec_dispatch.run_skill) and MCP (codec_mcp.tool_fn)
gates.
"""
import codec_consent


class _FakeReg:
    def __init__(self, destructive_names):
        self._d = set(destructive_names)

    def get_destructive(self, name):
        return name in self._d


def test_destructive_via_registry_flag():
    reg = _FakeReg({"my_user_skill"})
    assert codec_consent.is_destructive_skill("my_user_skill", registry=reg) is True
    assert codec_consent.is_destructive_skill("weather", registry=reg) is False


def test_destructive_via_http_blocked_backstop():
    reg = _FakeReg(set())
    # python_exec / terminal are in codec_config._HTTP_BLOCKED
    assert codec_consent.is_destructive_skill("python_exec", registry=reg) is True
    assert codec_consent.is_destructive_skill("terminal", registry=reg) is True


def test_destructive_known_builtins():
    reg = _FakeReg(set())
    for s in ("file_ops", "file_write", "imessage_send", "pilot", "skill_forge"):
        assert codec_consent.is_destructive_skill(s, registry=reg) is True, s


def test_benign_skills_not_destructive():
    reg = _FakeReg(set())
    for s in ("weather", "calculator", "web_search", "create_skill", "time"):
        assert codec_consent.is_destructive_skill(s, registry=reg) is False, s


def test_empty_toolname_not_destructive():
    assert codec_consent.is_destructive_skill("", registry=_FakeReg(set())) is False
    assert codec_consent.is_destructive_skill(None, registry=_FakeReg(set())) is False


def test_gate_kill_switch(monkeypatch):
    monkeypatch.delenv("CONSENT_GATE_ENABLED", raising=False)
    assert codec_consent.gate_enabled() is True
    monkeypatch.setenv("CONSENT_GATE_ENABLED", "false")
    assert codec_consent.gate_enabled() is False


# ── chat_consent_ok (A2: reuse the AskUserQuestion PWA panel) ────────────────
def test_chat_consent_nondestructive_runs_without_prompt(monkeypatch):
    import codec_ask_user

    called = {"n": 0}
    monkeypatch.setattr(codec_ask_user, "ask", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    assert codec_consent.chat_consent_ok("weather", "x", registry=_FakeReg(set())) is True
    assert called["n"] == 0, "non-destructive skills must not prompt for consent"


def test_chat_consent_destructive_granted(monkeypatch):
    import codec_ask_user

    monkeypatch.setattr(codec_ask_user, "ask", lambda *a, **k: "delete")  # verb-matched approval
    assert codec_consent.chat_consent_ok("file_ops", "delete x", registry=_FakeReg(set())) is True


def test_chat_consent_destructive_timeout_blocks(monkeypatch):
    import codec_ask_user

    monkeypatch.setattr(codec_ask_user, "ask", lambda *a, **k: codec_ask_user.TIMEOUT_SENTINEL)
    assert codec_consent.chat_consent_ok("file_ops", "x", registry=_FakeReg(set())) is False


def test_chat_consent_gate_off_runs(monkeypatch):
    monkeypatch.setenv("CONSENT_GATE_ENABLED", "false")
    assert codec_consent.chat_consent_ok("file_ops", "x", registry=_FakeReg(set())) is True


def test_chat_consent_fails_closed_on_error(monkeypatch):
    import codec_ask_user

    def _boom(*a, **k):
        raise RuntimeError("ask broke")

    monkeypatch.setattr(codec_ask_user, "ask", _boom)
    assert codec_consent.chat_consent_ok("file_ops", "x", registry=_FakeReg(set())) is False
