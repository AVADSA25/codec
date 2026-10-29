"""P-101: browser skills open http(s) public URLs only, unless browser_policy allows.

subprocess is mocked, getaddrinfo is patched and the config is a temp file.
"""
import asyncio
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

import codec_browser_policy
import codec_ssrf

_REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def env(tmp_path, monkeypatch):
    table = {"router.local": "192.168.1.1", "intranet": "10.0.0.7"}

    def gai(host, port, *a, **k):
        ip = table.get(host) or ("93.184.216.34" if any(c.isalpha() for c in host) else host)
        return [(2, 1, 6, "", (ip, port))]

    monkeypatch.setattr(codec_ssrf.socket, "getaddrinfo", gai)
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"browser_policy": {"deny": ["blocked.example"]}}))
    monkeypatch.setattr(codec_browser_policy, "CONFIG_PATH", str(cfg))
    events = []
    import codec_audit
    monkeypatch.setattr(codec_audit, "log_event", lambda ev, **kw: events.append((ev, kw)))
    calls = []

    def fake_run(argv, *a, **k):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return {"cfg": cfg, "events": events, "calls": calls}


def _skill(name):
    spec = importlib.util.spec_from_file_location(f"_t_{name}", _REPO / "skills" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


REFUSED = [
    "file:///etc/passwd",
    "javascript:alert(1)",
    "data:text/html,hi",
    "http://127.0.0.1:8090/api/status",
    "http://169.254.169.254/latest/meta-data/",
    "https://router.local/",
    "https://blocked.example/page",
    "https://www.blocked.example/",
]


@pytest.mark.parametrize("url", REFUSED)
def test_policy_refuses(env, url):
    ok, reason = codec_browser_policy.check_url(url, "test")
    assert ok is False and reason
    assert env["events"][-1][0] == "browser_url_blocked"
    assert url not in json.dumps(env["events"][-1][1])  # the audit never holds the URL


def test_private_refusal_names_the_setting(env):
    _, reason = codec_browser_policy.check_url("http://127.0.0.1:8090/", "test")
    assert "browser_policy.allow_private" in reason


def test_allow_private_lets_a_listed_host_through(env):
    env["cfg"].write_text(json.dumps({"browser_policy": {"allow_private": ["127.0.0.1:8090"]}}))
    assert codec_browser_policy.check_url("http://127.0.0.1:8090/", "test")[0] is True
    assert codec_browser_policy.check_url("http://127.0.0.1:8091/", "test")[0] is False


def test_public_site_allowed(env):
    assert codec_browser_policy.check_url("https://example.com/news", "test") == (True, "")


@pytest.mark.parametrize("task", [
    "open http://127.0.0.1:8090/api/status",
    "open http://169.254.169.254/",
    "open router.local",
    "open https://blocked.example/x",
])
def test_chrome_open_refuses_before_any_launch(env, task):
    out = _skill("chrome_open").run(task)
    assert env["calls"] == []
    assert "browser" in out.lower()


def test_chrome_open_normal_site_reaches_chrome(env):
    out = _skill("chrome_open").run("open https://example.com")
    assert env["calls"] == [["open", "-a", "Google Chrome", "https://example.com"]]
    assert "Opened" in out


def test_chrome_search_still_searches(env):
    _skill("chrome_search").run("search google for local weather")
    assert any(c[0] == "osascript" for c in env["calls"])


def test_chrome_automate_refuses_a_private_tab(env):
    mod = _skill("chrome_automate")
    mod.WORK_TABS[:] = ["https://example.com", "http://10.0.0.7/admin"]
    out = mod.run("work mode")
    assert "browser" in out.lower()
    assert len([c for c in env["calls"] if c[0] == "osascript"]) == 1  # only the public one


def test_cdp_navigate_refuses(env):
    # Loaded by path: chrome_automate puts ~/codec-repo first on sys.path.
    spec = importlib.util.spec_from_file_location("_t_codec_cdp", _REPO / "codec_cdp.py")
    codec_cdp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(codec_cdp)

    class _NoSend(codec_cdp.ChromeCDP):
        def __init__(self):
            pass

        async def send(self, *a, **k):
            raise AssertionError("a refused URL must not reach Chrome")

    with pytest.raises(ValueError):
        asyncio.run(_NoSend().navigate("javascript:alert(1)"))
