"""UI-TARS on demand (docs/UI-TARS-SERVER-DESIGN.md).

No real server, no real model, no pm2: Popen, the port probe, the clock and the memory
reading are fakes, and the state lives in tmp_path."""
import asyncio
import importlib.util
import json
import os
import stat
import subprocess
import sys
import threading
import types
from pathlib import Path

import pytest

import codec_dispatch
import codec_ui_tars as ut

REPO = Path(__file__).resolve().parent.parent
MODEL = "org/tars"
CFG = {"ui_tars_model": MODEL}

_spec = importlib.util.spec_from_file_location("_t_ui_tars_server", REPO / "scripts" / "ui_tars_server.py")
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)


class Clock:
    """Stands in for the time module: sleep moves the clock forward."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def time(self):
        return 1_700_000_000.0 + self.now

    def sleep(self, s):
        self.now += s


class FakePopen:
    calls = []
    next_pid = 50000
    exit_code = None            # None = keeps running

    def __init__(self, argv, **kw):
        FakePopen.calls.append((argv, kw))
        FakePopen.next_pid += 1
        self.pid = FakePopen.next_pid
        self.code = FakePopen.exit_code

    def poll(self):
        return self.code


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEC_UI_TARS_DIR", str(tmp_path / "codec"))
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    monkeypatch.delenv("UI_TARS_ON_DEMAND", raising=False)
    (tmp_path / "hub" / "models--org--tars" / "snapshots").mkdir(parents=True)
    w = types.SimpleNamespace(port_open=False, audits=[], stopped=[], free=40.0, image=False,
                              clock=Clock())
    FakePopen.calls, FakePopen.exit_code = [], None
    monkeypatch.setattr(ut, "_children", {})
    monkeypatch.setattr(ut, "time", w.clock)
    # Only codec_ui_tars sees the fake Popen; its ps check still runs the real one.
    monkeypatch.setattr(ut, "subprocess", types.SimpleNamespace(
        Popen=FakePopen, run=subprocess.run, DEVNULL=subprocess.DEVNULL,
        SubprocessError=subprocess.SubprocessError))
    monkeypatch.setattr(ut, "_port_open", lambda port, timeout=0.5: w.port_open)
    monkeypatch.setattr(ut, "_free_gb", lambda: w.free)
    monkeypatch.setattr(ut, "_image_busy", lambda: w.image)
    monkeypatch.setattr(ut, "_audit", lambda event, extra, level="info": w.audits.append((event, extra)))
    monkeypatch.setattr(ut, "_stop_own", lambda pid: w.stopped.append(pid))
    return w


def _ready_after_start(w, monkeypatch):
    """The port opens once a server has been started, as uvicorn does after the load."""
    monkeypatch.setattr(ut, "_port_open", lambda port, timeout=0.5: bool(FakePopen.calls) or w.port_open)


def test_the_owners_own_server_is_used_and_nothing_starts(world):
    assert ut.endpoint({**CFG, "ui_tars_base_url": "http://10.0.0.5:9000/v1/"}) == ("http://10.0.0.5:9000/v1", None)
    assert FakePopen.calls == []


def test_switched_off_gives_the_old_url(world, monkeypatch):
    assert ut.endpoint({**CFG, "ui_tars": {"on_demand": False}}) == (ut.LEGACY_URL, None)
    monkeypatch.setenv("UI_TARS_ON_DEMAND", "false")
    assert ut.endpoint(CFG) == (ut.LEGACY_URL, None)
    assert FakePopen.calls == []


@pytest.mark.parametrize("reason, setup, words", [
    ("not_installed", lambda w, tmp: (tmp / "hub" / "models--org--tars" / "snapshots").rmdir(), "not installed"),
    ("image_job", lambda w, tmp: setattr(w, "image", True), "image is being generated"),
    ("low_memory", lambda w, tmp: setattr(w, "free", 3.0), "3 GB free"),
    ("port_taken", lambda w, tmp: setattr(w, "port_open", True), "Port 8087 is used by another program"),
])
def test_refusals_start_nothing_and_say_why(world, tmp_path, reason, setup, words):
    setup(world, tmp_path)
    url, message = ut.endpoint(CFG)
    assert url is None and words in message
    assert world.audits == [("ui_tars_server_refused", {"reason": reason})]
    assert FakePopen.calls == []


def test_start_command_environment_and_state(world, monkeypatch):
    monkeypatch.setenv("SOME_API_KEY", "secret-value")
    _ready_after_start(world, monkeypatch)
    url, message = ut.endpoint(CFG)
    assert (url, message) == ("http://127.0.0.1:8087/v1", None)
    (argv, kw), = FakePopen.calls
    assert argv[1:] == [ut.SERVER_SCRIPT, "--model", MODEL, "--host", "127.0.0.1", "--port", "8087",
                        "--idle-seconds", "300", "--max-seconds", "7200"]
    assert kw["start_new_session"] is True
    env = kw["env"]
    assert env["HF_HUB_OFFLINE"] == "1" and env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert "SOME_API_KEY" not in env
    state = json.loads(Path(ut.state_path()).read_text())
    assert state["port"] == 8087 and state["model"] == MODEL and state["pid"] > 50000
    assert stat.S_IMODE(os.stat(ut.state_path()).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(ut.log_path()).st_mode) == 0o600
    (event, extra), = world.audits
    assert event == "ui_tars_server_started" and extra["model"] == MODEL and extra["port"] == 8087


def test_a_running_server_is_reused(world, monkeypatch):
    _ready_after_start(world, monkeypatch)
    first = ut.endpoint(CFG)
    second = ut.endpoint(CFG)
    assert first == second == ("http://127.0.0.1:8087/v1", None)
    assert len(FakePopen.calls) == 1


def test_a_server_that_never_opens_its_port_is_stopped(world):
    url, message = ut.endpoint(CFG)
    assert url is None and "did not start" in message
    assert world.stopped == [FakePopen.next_pid]          # only the pid it started
    assert not os.path.exists(ut.state_path())
    assert world.audits[-1] == ("ui_tars_server_refused", {"reason": "load_failed"})


def test_a_server_that_exits_while_loading_is_not_stopped_again(world):
    FakePopen.exit_code = 1
    url, message = ut.endpoint(CFG)
    assert url is None and "did not start" in message
    assert world.stopped == []


def test_two_callers_at_once_start_one_server(world, monkeypatch):
    _ready_after_start(world, monkeypatch)
    results = []
    threads = [threading.Thread(target=lambda: results.append(ut.endpoint(CFG))) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert results == [("http://127.0.0.1:8087/v1", None)] * 2
    assert len(FakePopen.calls) == 1


def test_a_reused_pid_in_the_state_file_is_never_stopped(world, monkeypatch):
    os.makedirs(os.path.dirname(ut.state_path()), exist_ok=True)
    Path(ut.state_path()).write_text(json.dumps({"pid": os.getpid(), "port": 8087, "model": MODEL}))
    _ready_after_start(world, monkeypatch)
    assert ut.endpoint(CFG) == ("http://127.0.0.1:8087/v1", None)
    assert world.stopped == []                             # this test process is not a UI-TARS server
    assert len(FakePopen.calls) == 1


def test_settings_are_clamped():
    s = ut.settings({"ui_tars": {"port": 80, "idle_seconds": 5, "min_free_gb": 1000,
                                 "load_timeout_s": True, "on_demand": "no"}})
    assert s == {**ut.DEFAULTS, "port": 1024, "idle_seconds": 30, "min_free_gb": 64}


# ── scripts/ui_tars_server.py ────────────────────────────────────────────────

def test_split_args_takes_out_the_timer_flags():
    assert srv.split_args(["--model", "m", "--idle-seconds", "30", "--port", "8087", "--max-seconds", "900"]) \
        == (30, 900, ["--model", "m", "--port", "8087"])


@pytest.mark.parametrize("now, ready, last, inflight, why", [
    (1100, False, 0, 0, ""),        # still loading: idle time does not count
    (1100, True, 1000, 0, ""),      # 100 s idle, limit 300
    (1400, True, 1000, 0, "idle"),
    (1400, True, 1000, 1, ""),      # a request in flight is never cut
    (8300, False, 0, 1, "max"),     # the hard stop wins over everything
])
def test_should_stop(now, ready, last, inflight, why):
    assert srv.should_stop(now, 1000, ready, last, inflight, 300, 7200) == why


def test_idle_app_counts_requests_and_readiness():
    clock = Clock()
    seen = []

    async def inner(scope, receive, send):
        if scope["type"] == "lifespan":
            await send({"type": "lifespan.startup.complete"})
        else:
            seen.append(app.inflight)
            clock.now += 50

    async def send(message):
        pass

    app = srv.IdleApp(inner, clock=clock.monotonic)
    assert app.reason(30, 7200) == ""
    asyncio.run(app({"type": "lifespan"}, None, send))
    assert app.ready
    asyncio.run(app({"type": "http"}, None, send))
    assert seen == [1] and app.inflight == 0 and app.last == clock.now
    clock.now += 31
    assert app.reason(30, 7200) == "idle"


def test_install_timer_wraps_the_app_and_refuses_without_one():
    async def asgi(scope, receive, send):
        pass

    package = types.SimpleNamespace(app=asgi)
    app = srv.install_timer(package)
    assert isinstance(package.app, srv.IdleApp) and package.app is app and app.inner is asgi
    assert srv.install_timer(types.SimpleNamespace()) is None


def test_main_exits_before_loading_when_mlx_vlm_is_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "mlx_vlm", None)
    assert srv.main(["--model", MODEL]) == srv.EXIT_NO_TIMER


# ── mouse_control wiring ─────────────────────────────────────────────────────

@pytest.fixture
def mouse(monkeypatch):
    spec = importlib.util.spec_from_file_location("_t_mouse_control", REPO / "skills" / "mouse_control.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setitem(sys.modules, "pyautogui", types.SimpleNamespace(FAILSAFE=False, PAUSE=0))
    return mod


def test_mouse_control_returns_the_refusal(mouse, monkeypatch):
    monkeypatch.setattr(ut, "endpoint", lambda cfg=None: (None, "UI-TARS is not installed here."))
    monkeypatch.setattr(mouse, "_find_element", lambda *a, **k: pytest.fail("no vision call after a refusal"))
    assert mouse.run("click the send button") == "UI-TARS is not installed here."


def test_mouse_control_sends_to_the_url_codec_ui_tars_gives(mouse, monkeypatch):
    monkeypatch.setattr(ut, "endpoint", lambda cfg=None: ("http://127.0.0.1:8087/v1", None))
    url, refusal = mouse._vision_url()
    assert (url, refusal) == ("http://127.0.0.1:8087/v1/chat/completions", None)
    posted = []

    class Reply:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": '{"x": 100, "y": 200}'}}]}

    import requests
    monkeypatch.setattr(requests, "post", lambda u, **k: posted.append((u, k["json"]["model"])) or Reply())
    monkeypatch.setattr(mouse, "_take_screenshot", lambda *a, **k: "aGk=")
    monkeypatch.setattr(mouse, "_get_screen_size", lambda: (1920, 1080))
    monkeypatch.setattr(mouse, "_downscale_screenshot", lambda b64, max_width=1920: (b64, 1.0))
    assert mouse._find_element("send button", url=url) == ((100, 200), None)
    assert posted == [(url, mouse.VISION_MODEL)]


def _asked(mouse, monkeypatch, replies):
    """Run _find_element on a fake screen; return the prompts UI-TARS was sent."""
    prompts = []

    def ask(image_b64, prompt, url=None):
        prompts.append(prompt)
        return replies.pop(0), 1.0

    monkeypatch.setattr(mouse, "_ask_vision", ask)
    monkeypatch.setattr(mouse, "_take_screenshot", lambda *a, **k: "aGk=")
    monkeypatch.setattr(mouse, "_get_screen_size", lambda: (1920, 1080))
    return prompts


def test_mouse_control_asks_with_the_ui_tars_grounding_prompt(mouse, monkeypatch):
    prompts = _asked(mouse, monkeypatch, ["<|box_start|>(1086,685)<|box_end|>"])
    assert mouse._find_element("Send button") == ((1086, 685), None)
    assert prompts == ["Output only the coordinate of one point in your response. "
                       "What element matches the following task: Click on Send button"]


def test_mouse_control_retry_also_uses_the_grounding_prompt(mouse, monkeypatch):
    prompts = _asked(mouse, monkeypatch, ["<html><body>no point here</body></html>",
                                          "<|box_start|>(40,12)<|box_end|>"])
    assert mouse._find_element("Apple menu") == ((40, 12), None)
    assert len(prompts) == 2
    assert all(p.startswith("Output only the coordinate of one point") for p in prompts)
    assert prompts[1].endswith("Click on the element labeled 'Apple menu'")


def test_mouse_control_refine_pass_uses_the_grounding_prompt(mouse, monkeypatch):
    prompts = []
    monkeypatch.setattr(mouse, "_crop_region", lambda *a, **k: ("aGk=", 100, 200, 700, 500))
    monkeypatch.setattr(mouse, "_ask_vision", lambda b64, p, url=None: prompts.append(p) or ("(10,20)", 1.0))
    monkeypatch.setattr(mouse, "_get_screen_size", lambda: (3840, 2160))
    assert mouse._refine_coordinate("aGk=", "Send button", 400, 450) == (110, 220)
    assert prompts == [mouse._ground_prompt("Click on Send button")]


def test_mouse_control_waits_long_enough_for_the_load():
    assert codec_dispatch.skill_timeout("mouse_control") == 180


def test_tests_cannot_reach_a_real_ui_tars_server():
    from conftest import LIVE_PORTS
    assert ut.DEFAULTS["port"] in LIVE_PORTS
