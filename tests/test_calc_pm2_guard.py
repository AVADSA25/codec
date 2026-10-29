"""Calculator without eval() and pm2_control refusing protected services (P-079)."""
import importlib.util
import json
import os
import subprocess
import sys
import textwrap

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_skill(name):
    spec = importlib.util.spec_from_file_location(
        f"_t_{name}", os.path.join(REPO, "skills", f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Answers recorded from the eval() version before the change.
RECORDED = [
    ("calculate 2 plus 2", "2 + 2 = 4"),
    ("what is 47 times 89", "47 * 89 = 4183"),
    ("100 divided by 4", "100 / 4 = 25"),
    ("10 divided by 4", "10 / 4 = 2.5"),
    ("15 percent of 200", "15 *0.01* 200 = 30"),
    ("(3 + 4) * 5", "(3 + 4) * 5 = 35"),
    ("2 ** 10", "2 ** 10 = 1024"),
    ("17 % 5", "17 % 5 = 2"),
    ("1,250 + 3,750", "1250 + 3750 = 5000"),
    ("7 minus 10", "7 - 10 = -3"),
    ("5 over 2", "5 / 2 = 2.5"),
    ("-3 * -3", "-3 * -3 = 9"),
    ("1/0", None),
    ("hello there", None),
    ("2(3)", None),
]


@pytest.mark.parametrize("task,expected", RECORDED)
def test_calculator_answers_unchanged(task, expected):
    assert _load_skill("calculator").run(task) == expected


def test_calculator_source_has_no_eval():
    src = open(os.path.join(REPO, "skills", "calculator.py")).read()
    assert "eval(" not in src.replace("safe_eval(", "")


def _timed_in_child(snippet):
    """Run snippet in a child killed after 5 s; it prints JSON {elapsed, out}."""
    code = textwrap.dedent(f"""
        import json, pathlib, sys, tempfile, time
        sys.path.insert(0, {REPO!r})
        import codec_audit  # never the owner's real audit log
        codec_audit._AUDIT_DIR = pathlib.Path(tempfile.mkdtemp())
        codec_audit._AUDIT_LOG = codec_audit._AUDIT_DIR / "audit.log"
        t0 = time.monotonic()
        {snippet}
        print(json.dumps({{"elapsed": time.monotonic() - t0, "out": out}}))
    """)
    r = subprocess.run([sys.executable, "-c", code], cwd=REPO,
                       capture_output=True, text=True, timeout=5)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_calculator_power_bomb_is_refused_fast():
    res = _timed_in_child(textwrap.dedent("""
        import importlib.util
        spec = importlib.util.spec_from_file_location("c", "skills/calculator.py")
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        out = m.run("9**9**8")
    """).replace("\n", "\n        "))
    assert res["elapsed"] < 0.5
    assert res["out"] is None or "too large" in res["out"]


def test_chat_fallback_power_bomb_is_refused_fast():
    res = _timed_in_child(textwrap.dedent("""
        from unittest import mock
        import routes.chat as chat
        with mock.patch("codec_consent.chat_consent_ok", return_value=True), \\
             mock.patch("codec_dispatch.run_skill", return_value=None):
            out = chat._try_skill_by_name("calculator", "9**9**8")[1]
    """).replace("\n", "\n        "))
    assert res["elapsed"] < 0.5
    assert "too large" in res["out"]


@pytest.mark.parametrize("expr,value", [
    ("2**10", 1024), ("2**-2", 0.25), ("1.5**2", 2.25), ("7//2", 3),
    ("-(3+4)", -7), ("10**300", 10 ** 300),
])
def test_safe_eval_values(expr, value):
    from codec_math import safe_eval
    assert safe_eval(expr) == value


@pytest.mark.parametrize("expr", [
    "__import__('os')", "(1).__class__", "a+1", "[1,2]", "True+1", "f(2)",
    "1 if 1 else 2", "'a'*3",
])
def test_safe_eval_refuses_non_arithmetic(expr):
    from codec_math import safe_eval
    with pytest.raises((ValueError, SyntaxError)):
        safe_eval(expr)


@pytest.mark.parametrize("expr", ["9**9**8", "10**100000", "(10**2999)*(10**2999)*(10**2999)*(10**2999)"])
def test_safe_eval_too_large(expr):
    from codec_math import TooLarge, safe_eval
    with pytest.raises(TooLarge):
        safe_eval(expr)


# --- pm2_control --------------------------------------------------------------

@pytest.fixture
def pm2(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"pm2_protected_services": ["acme-booking"]}))
    mod = _load_skill("pm2_control")
    monkeypatch.setattr(mod, "CONFIG_PATH", str(cfg))
    calls = []

    def recorder(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", recorder)
    return mod, calls


@pytest.mark.parametrize("task,rule", [
    ("restart service acme-booking", "protected"),
    ("restart codec-dashboard", "protected"),
    ("restart service codec-mcp-http", "protected"),
    ("restart cloudflared", "protected"),
    ("restart all", "every service"),
    ("restart --update-env", "not a service name"),
    ("restart service -f", "not a service name"),
])
def test_pm2_restart_refused(pm2, task, rule):
    mod, calls = pm2
    out = mod.run(task)
    assert calls == []
    assert rule in out


def test_pm2_restart_allowed_service(pm2):
    mod, calls = pm2
    out = mod.run("restart codec-telegram")
    assert calls == [["pm2", "restart", "codec-telegram"]]
    assert "Restarted 'codec-telegram'" in out


def test_pm2_protected_refusal_points_to_terminal(pm2):
    mod, _ = pm2
    assert "pm2 restart codec-dashboard" in mod.run("restart codec-dashboard")


def test_pm2_unused_allowed_commands_removed():
    assert not hasattr(_load_skill("pm2_control"), "ALLOWED_COMMANDS")
