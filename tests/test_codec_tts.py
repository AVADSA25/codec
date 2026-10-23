"""Voice 02 (codec_tts.py, docs/CODEC-VOICE-02-DESIGN.md): the cloned voice for reading aloud and announcements.

No real server is called here: requests.post is replaced by a recorder. The real mlx-audio server is tried by a gate script.
The rules: the clone only for purposes it is enabled for, Kokoro once when anything is wrong, every request exactly as it was
without tts_clone, the live conversation voice and the Telegram briefings untouched, and no text in the audit log.
"""
from __future__ import annotations

import builtins
import importlib.util
import io
import json
import re
from pathlib import Path

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

import codec_tts

REPO = Path(__file__).resolve().parent.parent
MP3 = b"ID3\x04\x00" + b"\xff\xf3" * 300
WAV = b"RIFF" + b"\x00" * 200
KOKORO = {"model": "mlx-community/Kokoro-82M-bf16", "voice": "am_adam", "speed": 0.9}


class Reply:
    def __init__(self, status=200, content=MP3, ctype="audio/mpeg"):
        self.status_code, self.content, self.headers = status, content, {"content-type": ctype}


@pytest.fixture(autouse=True)
def fresh():
    codec_tts._last_note.clear()


@pytest.fixture
def clone_files(tmp_path):
    ref = tmp_path / "reference.wav"
    ref.write_bytes(WAV)
    words = tmp_path / "reference.txt"
    words.write_text("Good evening. This is the reference.\n")
    return ref, words


@pytest.fixture
def cfg(clone_files):
    ref, words = clone_files
    return {"tts_clone": {"enabled_for": ["speak"], "model": "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16",
                          "ref_audio": str(ref), "ref_text_file": str(words)}}


@pytest.fixture
def server(monkeypatch):
    """requests.post replaced: server['replies'] is a list answered in order (an Exception is raised), server['calls'] what was sent."""
    state = {"calls": [], "replies": [Reply()]}

    def post(url, json=None, timeout=None, **kw):
        state["calls"].append({"url": url, "json": json, "timeout": timeout})
        r = state["replies"].pop(0) if len(state["replies"]) > 1 else state["replies"][0]
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr("requests.post", post)
    return state


@pytest.fixture
def audits(monkeypatch):
    seen = []
    import codec_audit
    monkeypatch.setattr(codec_audit, "log_event", lambda event, source, message="", extra=None, **kw: seen.append(
        {"event": event, "source": source, "extra": extra, **kw}))
    return seen


@pytest.fixture
def config_file(monkeypatch):
    """Whatever reads ~/.codec/config.json gets this dict instead (the owner's real file is never read)."""
    real_open = builtins.open
    box = {"cfg": {}}

    def fake_open(path, *a, **k):
        if str(path).endswith(".codec/config.json"):
            return io.StringIO(json.dumps(box["cfg"]))
        return real_open(path, *a, **k)

    monkeypatch.setattr(builtins, "open", fake_open)
    return box


# ── clone_settings ───────────────────────────────────────────────────────────

def test_the_clone_applies_only_when_configured_enabled_and_the_clip_is_there(cfg, tmp_path, clone_files):
    ref, words = clone_files
    s = codec_tts.clone_settings(cfg, "speak")
    assert s == {"model": "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16", "ref_audio": str(ref),
                 "ref_text": "Good evening. This is the reference.", "lang_code": "auto"}
    assert codec_tts.clone_settings(cfg, "live") is None            # not enabled for the live voice
    assert codec_tts.clone_settings(cfg, None) is None
    assert codec_tts.clone_settings({}, "speak") is None
    assert codec_tts.clone_settings({"tts_clone": "yes"}, "speak") is None
    assert codec_tts.clone_settings({"tts_clone": {"enabled_for": []}}, "speak") is None
    for key, bad in (("model", ""), ("ref_audio", str(tmp_path / "missing.wav")), ("ref_text_file", str(tmp_path / "missing.txt"))):
        broken = json.loads(json.dumps(cfg))
        broken["tts_clone"][key] = bad
        assert codec_tts.clone_settings(broken, "speak") is None, key
    ref.write_bytes(b"")
    assert codec_tts.clone_settings(cfg, "speak") is None            # an empty clip is no clip


def test_inline_reference_words_and_a_home_folder_path(cfg, clone_files, monkeypatch):
    ref, _ = clone_files
    monkeypatch.setenv("HOME", str(ref.parent))
    cfg["tts_clone"].update({"ref_audio": "~/reference.wav", "ref_text": "  Spoken words. ", "lang_code": "english"})
    del cfg["tts_clone"]["ref_text_file"]
    s = codec_tts.clone_settings(cfg, "speak")
    assert s["ref_audio"] == str(ref) and s["ref_text"] == "Spoken words." and s["lang_code"] == "english"


# ── payloads ─────────────────────────────────────────────────────────────────

def test_without_tts_clone_the_request_is_exactly_the_callers_own(cfg):
    assert codec_tts.payloads("Hello.", "speak", KOKORO, cfg={}) == [({**KOKORO, "input": "Hello."}, False)]
    assert codec_tts.payloads("Hello.", None, KOKORO, cfg=cfg) == [({**KOKORO, "input": "Hello."}, False)]
    assert codec_tts.payloads("Hello.", "live", KOKORO, cfg=cfg) == [({**KOKORO, "input": "Hello."}, False)]
    assert "input" not in KOKORO                                     # the caller's dict is not touched


def test_with_tts_clone_the_clone_comes_first_and_kokoro_stays_as_the_fallback(cfg):
    plan = codec_tts.payloads("Hello.", "speak", {**KOKORO, "response_format": "wav"}, cfg=cfg)
    assert [c for _, c in plan] == [True, False]
    clone, kokoro = plan[0][0], plan[1][0]
    assert set(clone) == {"model", "input", "ref_audio", "ref_text", "lang_code", "response_format"}
    assert clone["input"] == "Hello." and clone["response_format"] == "wav" and "voice" not in clone and "speed" not in clone
    assert kokoro == {**KOKORO, "response_format": "wav", "input": "Hello."}


# ── speak ────────────────────────────────────────────────────────────────────

def test_the_clone_speaks_when_it_works(cfg, server):
    r = codec_tts.speak("http://tts/x", "Your note is saved.", "speak", KOKORO, cfg=cfg, timeout=20)
    assert r.audio == MP3 and r.cloned and not r.unreachable and r.content_type == "audio/mpeg"
    assert len(server["calls"]) == 1
    assert server["calls"][0]["json"]["ref_text"] == "Good evening. This is the reference."
    assert server["calls"][0]["timeout"] == codec_tts.CLONE_TIMEOUT


@pytest.mark.parametrize("failure, reason", [
    (requests.ConnectionError("down"), "ConnectionError"),
    (requests.Timeout("slow"), "Timeout"),
    (RuntimeError("odd"), "RuntimeError"),
    (Reply(500, b"oops", "text/plain"), "http_500"),
    (Reply(200, b'{"error": "no model"}', "application/json"), "not_audio"),
    (Reply(200, b"", "audio/mpeg"), "not_audio"),
])
def test_any_failure_of_the_clone_falls_back_to_kokoro_once(cfg, server, audits, failure, reason):
    server["replies"] = [failure, Reply(200, WAV, "audio/wav")]
    r = codec_tts.speak("http://tts/x", "Your note is saved.", "speak", KOKORO, cfg=cfg, timeout=20)
    assert r.audio == WAV and not r.cloned and r.content_type == "audio/wav"
    assert [c["json"].get("voice") for c in server["calls"]] == [None, "am_adam"]
    assert server["calls"][1]["timeout"] == 20 and server["calls"][0]["timeout"] == codec_tts.CLONE_TIMEOUT
    assert [(a["event"], a["extra"]) for a in audits] == [("tts_fallback", {"purpose": "speak", "reason": reason})]


def test_the_fallback_audit_never_holds_the_text_and_is_not_repeated_every_call(cfg, server, audits):
    server["replies"] = [requests.ConnectionError("down"), Reply(), requests.ConnectionError("down"), Reply()]
    for _ in range(2):
        codec_tts.speak("http://tts/x", "the safe code is 4711", "speak", KOKORO, cfg=cfg)
    assert len(audits) == 1                                          # once per reason per minute
    assert "4711" not in json.dumps(audits) and audits[0]["source"] == "codec-tts" and audits[0]["level"] == "warning"


def test_nothing_is_audited_when_the_clone_is_not_in_play(cfg, server, audits):
    server["replies"] = [requests.ConnectionError("down")]
    r = codec_tts.speak("http://tts/x", "Hello.", "live", KOKORO, cfg=cfg)
    assert r.audio is None and r.unreachable and audits == []
    assert len(server["calls"]) == 1 and "ref_audio" not in server["calls"][0]["json"]


def test_a_kokoro_only_request_has_the_callers_timeout_and_the_status_is_kept(server):
    server["replies"] = [Reply(503, b"busy", "text/plain")]
    r = codec_tts.speak("http://tts/x", "Hello.", None, KOKORO, cfg={}, timeout=33)
    assert r.audio is None and r.status == 503 and not r.unreachable
    assert server["calls"][0]["timeout"] == 33 and server["calls"][0]["json"] == {**KOKORO, "input": "Hello."}


def test_both_failing_reports_whether_the_server_answered_at_all(cfg, server):
    server["replies"] = [requests.ConnectionError("a"), requests.ConnectionError("b")]
    assert codec_tts.speak("http://tts/x", "Hello.", "speak", KOKORO, cfg=cfg).unreachable is True
    server["replies"] = [requests.ConnectionError("a"), Reply(500, b"x", "text/plain")]
    r = codec_tts.speak("http://tts/x", "Hello.", "speak", KOKORO, cfg=cfg)
    assert r.unreachable is False and r.status == 500 and r.audio is None


def test_a_missing_clip_means_kokoro_with_no_noise(cfg, server, audits, tmp_path):
    cfg["tts_clone"]["ref_audio"] = str(tmp_path / "gone.wav")
    r = codec_tts.speak("http://tts/x", "Hello.", "speak", KOKORO, cfg=cfg)
    assert r.audio == MP3 and not r.cloned and len(server["calls"]) == 1 and audits == []


# ── warm_up ──────────────────────────────────────────────────────────────────

def test_warm_up_asks_for_one_word_only_when_the_clone_is_configured(cfg, server):
    assert codec_tts.warm_up("http://tts/x", {}) is False and server["calls"] == []
    assert codec_tts.warm_up("http://tts/x", cfg) is True
    assert len(server["calls"]) == 1 and server["calls"][0]["json"]["input"] == codec_tts.WARM_TEXT
    server["replies"] = [requests.ConnectionError("down"), Reply()]
    assert codec_tts.warm_up("http://tts/x", cfg) is False           # Kokoro answered, not the clone


# ── the callers: nothing changes without tts_clone, the clone comes first with it ────────────

def _bodies(server):
    return [c["json"] for c in server["calls"]]


def _load_skill(name, monkeypatch):
    spec = importlib.util.spec_from_file_location(f"codec_skill_{name}_under_test", REPO / "skills" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_pwa_read_aloud_uses_the_clone_for_the_owners_voice_but_not_for_a_voice_picked_by_hand(cfg, server, tmp_path, monkeypatch):
    import routes.tts as rt
    path = tmp_path / "config.json"
    path.write_text(json.dumps({**cfg, "tts_voice": "am_adam", "tts_speed": 0.9, "tts_url": "http://tts/x"}))
    monkeypatch.setattr(rt, "CONFIG_PATH", str(path))
    monkeypatch.setattr(rt, "voice_catalog", lambda config=None: [{"id": "am_adam"}, {"id": "bf_emma"}])
    app = FastAPI()
    app.include_router(rt.router)
    client = TestClient(app)

    r = client.get("/api/tts", params={"text": "Hello there."})
    assert r.status_code == 200 and r.headers["x-codec-voice"] == "clone" and "ref_audio" in _bodies(server)[0]
    server["calls"].clear()
    r = client.post("/api/tts", json={"text": "Hello there."})
    assert r.headers["x-codec-voice"] == "clone"
    server["calls"].clear()
    r = client.post("/api/tts", json={"text": "Hello there.", "voice": "am_adam"})          # the owner's own voice by name
    assert r.headers["x-codec-voice"] == "clone"
    server["calls"].clear()
    r = client.post("/api/tts", json={"text": "Hello there.", "voice": "bf_emma"})          # another voice: Kokoro's, as asked
    assert r.headers["x-codec-voice"] == "kokoro" and _bodies(server) == [
        {"model": "mlx-community/Kokoro-82M-bf16", "voice": "bf_emma", "speed": 0.9, "input": "Hello there."}]


def test_pwa_read_aloud_keeps_its_two_error_answers(cfg, server, tmp_path, monkeypatch):
    import routes.tts as rt
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"tts_url": "http://tts/x"}))
    monkeypatch.setattr(rt, "CONFIG_PATH", str(path))
    app = FastAPI()
    app.include_router(rt.router)
    client = TestClient(app)
    server["replies"] = [requests.ConnectionError("down")]
    assert client.post("/api/tts", json={"text": "Hi."}).status_code == 503
    server["replies"] = [Reply(500, b"x", "text/plain")]
    assert client.post("/api/tts", json={"text": "Hi."}).status_code == 502


def test_the_say_skill(server, cfg, monkeypatch):
    mod = _load_skill("tts_say", monkeypatch)
    started = []
    monkeypatch.setattr(mod.subprocess, "Popen", lambda args, **k: started.append(args))
    mod.KOKORO_URL, mod.KOKORO_MODEL, mod.TTS_VOICE, mod.TTS_SPEED = "http://tts/x", "kokoro-m", "am_adam", 0.85

    mod._cfg = {}
    assert mod.run("say all done captain").startswith("🔊 Speaking")
    assert _bodies(server) == [{"model": "kokoro-m", "voice": "am_adam", "speed": 0.85, "response_format": "wav", "input": "all done captain"}]
    assert started[0][0] == "afplay"
    server["calls"].clear()
    started.clear()
    mod._cfg = cfg
    mod.run("say all done captain")
    assert "ref_audio" in _bodies(server)[0] and _bodies(server)[0]["response_format"] == "wav"
    server["calls"].clear()
    server["replies"] = [requests.ConnectionError("down")]
    assert "Kokoro offline" in mod.run("say all done captain") and started[-1][0] == "say"


def test_the_timer_announcement(server, cfg, config_file, monkeypatch):
    mod = _load_skill("timer", monkeypatch)
    fired = []

    class FakeTimer:
        def __init__(self, seconds, fn):
            self.daemon = False
            fired.append(fn)

        def start(self):
            pass

    class FakeThread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

    import subprocess
    import threading
    played = []
    monkeypatch.setattr(threading, "Timer", FakeTimer)
    monkeypatch.setattr(threading, "Thread", FakeThread)
    monkeypatch.setattr(subprocess, "Popen", lambda args, **k: played.append(args))
    monkeypatch.setattr(subprocess, "run", lambda args, **k: played.append(args))
    config_file["cfg"] = {"tts_url": "http://tts/x", "tts_voice": "am_adam", "tts_speed": 0.85}
    assert mod.run("set a timer for 2 minutes") == "Timer set for 2 minutes."
    fired[0]()
    assert _bodies(server) == [{"model": "mlx-community/Kokoro-82M-bf16", "voice": "am_adam", "speed": 0.85,
                                "input": "Your 2 minutes timer is done."}]
    server["calls"].clear()
    config_file["cfg"].update(cfg)
    fired[0]()
    assert "ref_audio" in _bodies(server)[0] and _bodies(server)[0]["input"] == "Your 2 minutes timer is done."
    assert any(p[0] == "afplay" and p[1].endswith(".mp3") for p in played if isinstance(p, list) and len(p) > 1)


def test_the_morning_briefing_voice(server, cfg, config_file):
    import codec_briefing
    config_file["cfg"] = {"tts_url": "http://tts/x", "tts_voice": "am_adam", "tts_speed": 0.85, "tts_model": "kokoro-m"}
    assert codec_briefing._tts("Good morning.") == MP3
    assert _bodies(server) == [{"model": "kokoro-m", "voice": "am_adam", "speed": 0.85, "input": "Good morning."}]
    assert server["calls"][0]["timeout"] == 120
    server["calls"].clear()
    config_file["cfg"].update(cfg)
    assert codec_briefing._tts("Good morning.") == MP3 and "ref_audio" in _bodies(server)[0]
    server["replies"] = [Reply(500, b"x", "text/plain")]
    assert codec_briefing._tts("Good morning.") is None


def test_scheduled_results_spoken_on_the_mac(server, cfg, monkeypatch, tmp_path):
    import codec_scheduler as sch
    played = []
    monkeypatch.setattr(sch, "VOICE_SESSION_PATH", str(tmp_path / "no-voice-call"))
    monkeypatch.setattr("subprocess.run", lambda args, **k: played.append(args))
    monkeypatch.setattr(sch, "_config", lambda: {"tts_url": "http://tts/x", "tts_voice": "am_adam", "tts_speed": 0.85})
    sch._speak_on_mac("The report is ready.")
    assert _bodies(server) == [{"model": "mlx-community/Kokoro-82M-bf16", "voice": "am_adam", "speed": 0.85,
                                "input": "The report is ready."}]
    assert played and played[0][0] == "afplay"
    server["calls"].clear()
    monkeypatch.setattr(sch, "_config", lambda: {"tts_url": "http://tts/x", "tts_voice": "am_adam", **cfg})
    sch._speak_on_mac("The report is ready.")
    assert "ref_audio" in _bodies(server)[0]


def test_the_watcher_speaks_with_voice_02_when_asked(server, cfg, config_file, monkeypatch):
    import codec_watcher as w
    played = []
    monkeypatch.setattr(w.subprocess, "Popen", lambda args, **k: played.append(args))
    monkeypatch.setattr(w, "KOKORO_URL", "http://tts/x")
    config_file["cfg"] = {}
    w.speak("**Draft** ready.")
    assert _bodies(server) == [{"model": "mlx-community/Kokoro-82M-bf16", "voice": w.TTS_VOICE, "speed": w.TTS_SPEED, "input": "Draft ready."}]
    server["calls"].clear()
    config_file["cfg"] = cfg
    w.speak("**Draft** ready.")
    assert "ref_audio" in _bodies(server)[0] and played[-1][0] == "afplay"


# ── what is deliberately left alone ──────────────────────────────────────────

def test_the_live_voice_the_wake_word_replies_and_the_telegram_briefings_are_untouched():
    for name in ("codec_voice.py", "codec_core.py", "codec_telegram.py"):
        assert "codec_tts" not in (REPO / name).read_text(), f"{name} is outside phase 1 (docs/CODEC-VOICE-02-DESIGN.md)"


def test_every_phase_one_caller_goes_through_the_helper_and_none_posts_speech_itself():
    callers = ["routes/tts.py", "skills/tts_say.py", "skills/timer.py", "codec_briefing.py", "codec_scheduler.py",
               "codec_watcher.py", "codec_textassist.py"]
    for name in callers:
        src = (REPO / name).read_text()
        assert "codec_tts" in src, name
        speech_post = re.search(r"requests\.post\([^)]*(?:tts_url|KOKORO_URL|tts_say|audio/speech)", src)
        # codec_watcher keeps one direct request, used only when the repo is not importable
        assert speech_post is None or name == "codec_watcher.py", f"{name} still posts speech by itself"


def test_the_dashboard_starts_the_warm_up_in_the_background():
    src = (REPO / "codec_dashboard.py").read_text()
    assert "threading.Thread(target=codec_tts.warm_up, daemon=True" in src
